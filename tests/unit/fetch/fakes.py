"""FakeClient — KrxClient(Protocol) 오프라인 구현 (계획서 §7).

고정 DataFrame을 반환해 네트워크 없이 전 로직을 결정론으로 검증한다. pykrx 원본과
동일하게 '날짜 index + 한글 컬럼' 형태로 만든다.
"""

from __future__ import annotations

import pandas as pd


def krx_ohlcv_frame(
    dates: list[str],
    closes: list[float],
    *,
    volumes: list[float] | None = None,
    values: list[float] | None = None,
    spread: float = 0.01,
) -> pd.DataFrame:
    """pykrx get_market_ohlcv 형태(날짜 index, 한글 컬럼)의 프레임."""
    n = len(dates)
    vol = volumes if volumes is not None else [1000.0] * n
    val = values if values is not None else [c * v for c, v in zip(closes, vol)]
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    return pd.DataFrame(
        {
            "시가": closes,
            "고가": [c * (1 + spread) for c in closes],
            "저가": [c * (1 - spread) for c in closes],
            "종가": closes,
            "거래량": vol,
            "거래대금": val,
        },
        index=idx,
    )


def krx_index_frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    return pd.DataFrame({"종가": closes}, index=idx)


class FakeClient:
    """KrxClient Protocol 구현. 반환값을 생성자에서 주입한다."""

    def __init__(
        self,
        *,
        ohlcv: dict[str, pd.DataFrame] | None = None,
        indices: dict[str, pd.DataFrame] | None = None,
        tickers: dict[str, list[str]] | None = None,
        names: dict[str, str] | None = None,
        market_caps: dict[str, pd.DataFrame] | None = None,
        listing: pd.DataFrame | None = None,
    ) -> None:
        self._ohlcv = ohlcv or {}
        self._indices = indices or {}
        self._tickers = tickers or {}
        self._names = names or {}
        self._market_caps = market_caps or {}
        self._listing = listing if listing is not None else pd.DataFrame(
            {"Code": [], "ListingDate": []}
        )
        self.ohlcv_calls: list[tuple[str, str, str]] = []

    def ohlcv(self, fromdate: str, todate: str, ticker: str) -> pd.DataFrame:
        self.ohlcv_calls.append((fromdate, todate, ticker))
        if ticker not in self._ohlcv:
            raise RuntimeError(f"no fake ohlcv for {ticker}")
        df = self._ohlcv[ticker]
        # fromdate/todate로 슬라이스해 실제 API처럼 범위를 존중한다.
        lo = pd.Timestamp(fromdate)
        hi = pd.Timestamp(todate)
        return df[(df.index >= lo) & (df.index <= hi)]

    def index_ohlcv(self, fromdate: str, todate: str, code: str) -> pd.DataFrame:
        return self._indices[code]

    def tickers(self, on_date: str, market: str) -> list[str]:
        return list(self._tickers.get(market, []))

    def ticker_name(self, ticker: str) -> str:
        return self._names.get(ticker, "")

    def market_cap(self, on_date: str, market: str) -> pd.DataFrame:
        return self._market_caps[market]

    def listing_dates(self) -> pd.DataFrame:
        return self._listing
