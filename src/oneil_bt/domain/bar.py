"""PriceFrame — 심볼 1개의 일봉 프레임 불변 래퍼 (계획서 §3.1).

index = DatetimeIndex(정렬·중복 없음, 정규화된 자정 타임스탬프)
columns = open, high, low, close, volume [, value]
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

import pandas as pd

REQUIRED_COLUMNS: Final[tuple[str, ...]] = ("open", "high", "low", "close", "volume")
OPTIONAL_COLUMNS: Final[tuple[str, ...]] = ("value",)


def _to_ts(d: date | pd.Timestamp | str) -> pd.Timestamp:
    """date/str/Timestamp를 자정 정규화된 Timestamp로 변환."""
    return pd.Timestamp(d).normalize()


@dataclass(frozen=True)
class PriceFrame:
    """심볼 1개의 일봉 데이터. 생성 후 불변(df를 재대입하지 않는다).

    로더가 검증을 마친 뒤 생성하는 것을 전제로 하나, 방어적으로 index 정렬·
    중복 여부만 가볍게 확인한다.
    """

    symbol: str
    df: pd.DataFrame

    def __post_init__(self) -> None:
        if not isinstance(self.df.index, pd.DatetimeIndex):
            raise ValueError(f"{self.symbol}: index must be a DatetimeIndex")
        if not self.df.index.is_monotonic_increasing:
            raise ValueError(f"{self.symbol}: index must be sorted ascending")
        if self.df.index.has_duplicates:
            raise ValueError(f"{self.symbol}: index has duplicate dates")
        missing = [c for c in REQUIRED_COLUMNS if c not in self.df.columns]
        if missing:
            raise ValueError(f"{self.symbol}: missing columns {missing}")

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.df.index  # type: ignore[return-value]

    def slice(self, start: date | None = None, end: date | None = None) -> "PriceFrame":
        """[start, end] 폐구간으로 자른 새 PriceFrame (양끝 포함)."""
        lo = _to_ts(start) if start is not None else None
        hi = _to_ts(end) if end is not None else None
        return PriceFrame(self.symbol, self.df.loc[lo:hi].copy())

    def asof(self, d: date) -> pd.Series | None:
        """날짜 d 이하(<=)에서 가장 최근 행을 반환. 없으면 None.

        이름 그대로 as-of 조회다. 거래정지 등으로 d에 실거래 바가 없을 때에도
        마지막 유효 종가로 평가할 수 있게 한다 (계획서 §4.4).
        """
        ts = _to_ts(d)
        idx = self.df.index
        pos = idx.searchsorted(ts, side="right") - 1
        if pos < 0:
            return None
        return self.df.iloc[pos]

    def row(self, d: date) -> pd.Series | None:
        """정확히 날짜 d의 행을 반환 (실거래 바). 없으면 None."""
        ts = _to_ts(d)
        try:
            return self.df.loc[ts]
        except KeyError:
            return None
