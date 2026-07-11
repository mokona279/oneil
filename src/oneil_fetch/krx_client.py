"""pykrx / FinanceDataReader 호출 격리 (계획서 §2, §3.1, §5.1).

외부 라이브러리 호출을 이 파일 한 곳에 가둔다. 이유:
- 네트워크 의존성이 백테스트 코어(oneil_bt)에 스며들면 안 된다.
- pykrx/FDR은 KRX 웹 변경에 따라 컬럼명·동작이 깨질 수 있으므로(§2.3), 반환 직후
  기대 컬럼 존재를 assert해서 조용한 오염이 아니라 즉시 실패하게 한다.

KrxClient(Protocol)로 계약을 정의하고, 실구현 PykrxClient를 둔다. 테스트는 이 Protocol을
구현한 FakeClient를 주입해 네트워크 없이 전 로직을 검증한다(§7).
"""

from __future__ import annotations

import time
from typing import Callable, Protocol

import pandas as pd

# pykrx 반환에 반드시 존재해야 하는 한글 컬럼 (§2.3 표류 감지).
# adjusted=True는 수정 OHLC+거래량을 주되 거래대금 대신 등락률을 준다(pykrx 1.2.8 관측).
# 실측 거래대금은 adjusted=False에만 있고, 거래대금은 수정과 무관하므로 두 호출을 병합한다.
_ADJ_OHLCV_REQUIRED = ("시가", "고가", "저가", "종가", "거래량")
_VALUE_REQUIRED = ("거래대금",)
_INDEX_REQUIRED = ("종가",)
_MARKET_CAP_REQUIRED = ("상장주식수",)


class KrxClient(Protocol):
    """수집 로직이 의존하는 데이터 소스 계약. 실구현·Fake 모두 이 형태를 만족한다."""

    def ohlcv(self, fromdate: str, todate: str, ticker: str) -> pd.DataFrame:
        """종목 수정주가 일봉. index=날짜, 컬럼=한글(시가·고가·…·거래대금)."""
        ...

    def index_ohlcv(self, fromdate: str, todate: str, code: str) -> pd.DataFrame:
        """지수 일봉. index=날짜, 컬럼에 '종가' 포함."""
        ...

    def tickers(self, on_date: str, market: str) -> list[str]:
        """on_date 시점 market('KOSPI'|'KOSDAQ') 상장 종목 티커 목록."""
        ...

    def ticker_name(self, ticker: str) -> str:
        """종목명."""
        ...

    def market_cap(self, on_date: str, market: str) -> pd.DataFrame:
        """market 전 종목 시가총액 스냅샷. index=티커, '상장주식수' 포함."""
        ...

    def listing_dates(self) -> pd.DataFrame:
        """전 종목 상장일. 컬럼에 'Code'와 'ListingDate' 포함 (FDR)."""
        ...


def _assert_columns(df: pd.DataFrame, required: tuple[str, ...], where: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"KRX API 표류 의심: {where} 반환에 컬럼 {missing} 없음 "
            f"(실제 컬럼: {list(df.columns)})"
        )


class PykrxClient:
    """pykrx + FinanceDataReader 실구현.

    모든 호출은 sleep(요청 간격) + 지수 백오프 재시도(§5.1)로 감싼다. sleeper는
    주입 가능(테스트/속도조절용). pykrx/FDR은 지연 import — 미설치 환경에서도 이
    모듈은 import되며, FakeClient 기반 테스트가 돈다.
    """

    def __init__(
        self,
        *,
        sleep_sec: float = 0.5,
        retries: int = 3,
        backoff: tuple[float, ...] = (2.0, 8.0, 32.0),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep_sec = sleep_sec
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleeper

    # ------------------------------------------------------------------ #
    def _call(self, fn: Callable[[], pd.DataFrame], where: str) -> pd.DataFrame:
        """요청 간 sleep + 예외/빈 DataFrame 시 지수 백오프 재시도."""
        last_err: Exception | None = None
        for attempt in range(self._retries + 1):
            if attempt == 0:
                self._sleep(self._sleep_sec)
            else:
                wait = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
                self._sleep(wait)
            try:
                df = fn()
            except Exception as exc:  # pykrx는 KRX 응답 파싱 실패를 다양한 예외로 던진다
                last_err = exc
                continue
            if df is None or len(df) == 0:
                last_err = RuntimeError(f"{where}: 빈 응답")
                continue
            return df
        raise RuntimeError(f"{where}: {self._retries}회 재시도 실패") from last_err

    # ------------------------------------------------------------------ #
    def ohlcv(self, fromdate: str, todate: str, ticker: str) -> pd.DataFrame:
        """수정 OHLC+거래량(adjusted=True)에 실측 거래대금(adjusted=False)을 병합.

        pykrx 1.2.8은 adjusted=True에서 거래대금 대신 등락률을 준다. 실측 거래대금은
        수정 여부와 무관한 실제 원화 체결액이므로 미수정 호출에서 가져와 날짜로 정렬 병합한다
        (§1.1 value 실측 원칙, §9 Q4). 종목당 2호출로 늘지만 근사 없이 정확하다.
        """
        from pykrx import stock

        adj = self._call(
            lambda: stock.get_market_ohlcv(fromdate, todate, ticker, adjusted=True),
            f"get_market_ohlcv({ticker}, adjusted)",
        )
        _assert_columns(adj, _ADJ_OHLCV_REQUIRED, f"get_market_ohlcv({ticker}, adjusted)")

        unadj = self._call(
            lambda: stock.get_market_ohlcv(fromdate, todate, ticker, adjusted=False),
            f"get_market_ohlcv({ticker}, unadjusted)",
        )
        _assert_columns(unadj, _VALUE_REQUIRED, f"get_market_ohlcv({ticker}, unadjusted)")

        merged = adj.copy()
        merged["거래대금"] = unadj["거래대금"].reindex(adj.index)
        return merged

    def index_ohlcv(self, fromdate: str, todate: str, code: str) -> pd.DataFrame:
        from pykrx import stock

        df = self._call(
            lambda: stock.get_index_ohlcv(fromdate, todate, code),
            f"get_index_ohlcv({code})",
        )
        _assert_columns(df, _INDEX_REQUIRED, f"get_index_ohlcv({code})")
        return df

    def tickers(self, on_date: str, market: str) -> list[str]:
        from pykrx import stock

        # 티커 목록은 빈 리스트가 정상일 수 있어 _call(빈=실패) 대신 직접 호출.
        self._sleep(self._sleep_sec)
        return list(stock.get_market_ticker_list(on_date, market=market))

    def ticker_name(self, ticker: str) -> str:
        from pykrx import stock

        self._sleep(self._sleep_sec)
        return str(stock.get_market_ticker_name(ticker))

    def market_cap(self, on_date: str, market: str) -> pd.DataFrame:
        from pykrx import stock

        df = self._call(
            lambda: stock.get_market_cap(on_date, market=market),
            f"get_market_cap({market})",
        )
        _assert_columns(df, _MARKET_CAP_REQUIRED, f"get_market_cap({market})")
        return df

    def listing_dates(self) -> pd.DataFrame:
        import FinanceDataReader as fdr

        self._sleep(self._sleep_sec)
        df = fdr.StockListing("KRX-DESC")
        _assert_columns(df, ("Code", "ListingDate"), "StockListing('KRX-DESC')")
        return df
