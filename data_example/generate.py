"""data_example 소형 실데이터 생성기 (Phase 8).

통합 스모크·골든 회귀 테스트가 CsvDataSource로 로드해 엔진을 end-to-end로 돌릴 수
있는 **결정론적** 소형 데이터셋을 만든다. 실서비스 데이터가 아니라, 규칙서의 주요
경로(돌파 진입·피라미딩·추세이탈 청산)를 자극하도록 손으로 설계한 합성 시계열이다.

레이아웃(계획서 §4):
    data_example/
    ├─ prices/{symbol}.csv    종목 일봉 (date,open,high,low,close,volume,value)
    ├─ kospi.csv / kosdaq.csv 지수 일봉 (date,close) — 거래일 캘린더 기준
    └─ meta.csv               symbol,name,market,listing_date,shares_out

재현:
    python data_example/generate.py
CSV는 utf-8-sig(엑셀/한글 호환)로 고정. numpy 난수 없이 순수 함수라 항상 동일 산출.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
PRICES = OUT / "prices"

# 52주(252세션) 워밍업 이후 베이스가 오도록 넉넉한 상승추세.
UPTREND = 260
BASE = 30
TAIL = 30
N = UPTREND + BASE + TAIL  # 총 세션 수
START = "2019-01-02"
SPREAD = 0.02  # high/low = close*(1±spread)


def _sessions(n: int) -> list[date]:
    return [ts.date() for ts in pd.bdate_range(start=START, periods=n)]


def _uptrend(peak: float) -> list[float]:
    return list(np.linspace(100.0, peak, UPTREND))


def _base(base_px: float) -> list[float]:
    # 피벗(=peak 고가) 아래에서 얕게(±2) 다진다.
    return [base_px + (2.0 if i % 2 == 0 else -2.0) for i in range(BASE)]


def winner_closes(peak: float = 300.0) -> list[float]:
    """상승추세 → 얕은 베이스 → 돌파 후 지속 상승(수익 청산 없이 보유)."""
    tail = [peak + 5.0] + list(np.linspace(peak + 8.0, peak + 60.0, TAIL - 1))
    return _uptrend(peak) + _base(peak - 5.0) + tail


def stopped_closes(peak: float = 300.0) -> list[float]:
    """돌파 진입 후 급락 → 손절/추세이탈 청산을 자극한다."""
    # 돌파일 상회 후 곧바로 무너져 60MA·손절선을 하회.
    tail = [peak + 5.0, peak + 6.0] + list(np.linspace(peak, peak * 0.72, TAIL - 2))
    return _uptrend(peak) + _base(peak - 5.0) + tail


def _volumes() -> list[float]:
    up = [5_000.0] * UPTREND
    base = [2_000.0] * (BASE - 10) + [800.0] * 10  # 드라이업: 직전 10일 조용
    tail = [6_000.0] * TAIL                          # 돌파일 거래량 급증(≥1.5×)
    return up + base + tail


def _price_frame(closes: list[float]) -> pd.DataFrame:
    close = pd.Series(closes, dtype=float)
    vol = pd.Series(_volumes(), dtype=float)
    idx = [d.strftime("%Y-%m-%d") for d in _sessions(N)]
    return pd.DataFrame(
        {
            "date": idx,
            "open": close.round(2),
            "high": (close * (1 + SPREAD)).round(2),
            "low": (close * (1 - SPREAD)).round(2),
            "close": close.round(2),
            "volume": vol,
            "value": [2.0e10] * N,  # 거래대금 200억(트렌드 템플릿 100억 게이트 통과)
        }
    )


def _index_frame(peak: float) -> pd.DataFrame:
    # 완만한 상승 → 60/120MA 위 NORMAL 유지 & 종목 RS(>지수) 성립.
    close = np.linspace(100.0, peak, N).round(2)
    return pd.DataFrame({"date": [d.strftime("%Y-%m-%d") for d in _sessions(N)], "close": close})


# 종목 시나리오: (심볼, 이름, 시장, closes)
SYMBOLS = [
    ("005930", "가상반도체", "KOSPI", winner_closes()),
    ("000660", "가상전자", "KOSPI", stopped_closes()),
    ("035720", "가상바이오", "KOSDAQ", winner_closes(peak=280.0)),
]


def main() -> None:
    PRICES.mkdir(parents=True, exist_ok=True)
    for sym, _name, _mkt, closes in SYMBOLS:
        _price_frame(closes).to_csv(PRICES / f"{sym}.csv", index=False, encoding="utf-8-sig")

    _index_frame(150.0).to_csv(OUT / "kospi.csv", index=False, encoding="utf-8-sig")
    _index_frame(140.0).to_csv(OUT / "kosdaq.csv", index=False, encoding="utf-8-sig")

    meta = pd.DataFrame(
        [
            {"symbol": s, "name": n, "market": m, "listing_date": "2010-01-01", "shares_out": 100_000_000}
            for s, n, m, _c in SYMBOLS
        ]
    )
    meta.to_csv(OUT / "meta.csv", index=False, encoding="utf-8-sig")

    sessions = _sessions(N)
    print(f"생성 완료: {len(SYMBOLS)}종목 × {N}세션 ({sessions[0]} ~ {sessions[-1]})")
    print(f"  prices/  : {[s for s, *_ in SYMBOLS]}")
    print(f"  indices  : kospi.csv, kosdaq.csv")
    print(f"  meta.csv : {len(SYMBOLS)}행")


if __name__ == "__main__":
    main()
