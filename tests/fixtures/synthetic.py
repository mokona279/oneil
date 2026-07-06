"""합성 OHLCV 빌더 (계획서 §10).

경계 사례용 결정론적 일봉 프레임을 만든다. Phase 0에서는 로더/데이터소스/
PriceFrame 검증에 쓰이고, 이후 Phase에서 베이스·과열 등 시나리오로 확장한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

from oneil_bt.domain.bar import PriceFrame


def business_dates(start: str | date, n: int) -> list[date]:
    """start부터 n개의 영업일(월~금) 날짜. 거래일 캘린더 대용."""
    return [ts.date() for ts in pd.bdate_range(start=start, periods=n)]


def ohlcv_frame(
    dates: Sequence[date],
    closes: Sequence[float] | float = 100.0,
    volumes: Sequence[float] | float = 1_000,
    *,
    values: Sequence[float] | None = None,
    spread: float = 0.02,
) -> pd.DataFrame:
    """close 시리즈를 중심으로 정합적인 OHLCV DataFrame을 만든다.

    high = close*(1+spread), low = close*(1-spread), open = close.
    → 항상 low <= open,close <= high 를 만족한다.
    반환 index는 DatetimeIndex(name='date').
    """
    n = len(dates)
    close_list = list(closes) if isinstance(closes, Sequence) else [float(closes)] * n
    vol_list = list(volumes) if isinstance(volumes, Sequence) else [float(volumes)] * n
    if len(close_list) != n or len(vol_list) != n:
        raise ValueError("closes/volumes length must match dates")

    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)).normalize(), name="date")
    close = pd.Series(close_list, dtype=float)
    data = {
        "open": close.to_numpy(),
        "high": (close * (1 + spread)).to_numpy(),
        "low": (close * (1 - spread)).to_numpy(),
        "close": close.to_numpy(),
        "volume": pd.Series(vol_list, dtype=float).to_numpy(),
    }
    if values is not None:
        if len(values) != n:
            raise ValueError("values length must match dates")
        data["value"] = pd.Series(list(values), dtype=float).to_numpy()
    return pd.DataFrame(data, index=idx)


def price_frame(symbol: str, df: pd.DataFrame) -> PriceFrame:
    return PriceFrame(symbol, df)


def flat_price_frame(
    symbol: str = "TEST",
    start: str | date = "2020-01-01",
    n: int = 30,
    close: float = 100.0,
    volume: float = 1_000,
) -> PriceFrame:
    dates = business_dates(start, n)
    return PriceFrame(symbol, ohlcv_frame(dates, close, volume))


def write_prices_csv(
    df: pd.DataFrame,
    path: Path,
    *,
    encoding: str = "utf-8-sig",
) -> Path:
    """PriceFrame용 CSV로 저장 (date 컬럼 + OHLCV[+value])."""
    out = df.copy()
    out.insert(0, "date", out.index.strftime("%Y-%m-%d"))
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, encoding=encoding)
    return path


def write_index_csv(
    dates: Sequence[date],
    closes: Sequence[float] | float,
    path: Path,
    *,
    encoding: str = "utf-8-sig",
) -> Path:
    """지수 CSV (date, close)로 저장."""
    n = len(dates)
    close_list = list(closes) if isinstance(closes, Sequence) else [float(closes)] * n
    frame = pd.DataFrame(
        {"date": [d.strftime("%Y-%m-%d") for d in dates], "close": close_list}
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding=encoding)
    return path


def write_meta_csv(
    rows: Sequence[dict],
    path: Path,
    *,
    encoding: str = "utf-8-sig",
    columns: Sequence[str] = ("symbol", "name", "market", "listing_date", "shares_out"),
) -> Path:
    frame = pd.DataFrame(list(rows))
    cols = [c for c in columns if c in frame.columns]
    path.parent.mkdir(parents=True, exist_ok=True)
    frame[cols].to_csv(path, index=False, encoding=encoding)
    return path
