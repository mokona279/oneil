"""Indicator 계약 + 공용 유틸 (계획서 §3.3).

`Indicator` Protocol: `compute(prices) -> pd.Series` (index=date, 값@D는 ≤D만 사용).
RS처럼 지수 프레임이 추가로 필요한 지표는 이 계약을 따르지 않고 별도 시그니처를
가진다(relative_strength.py 참조).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

from ..domain.bar import PriceFrame


@runtime_checkable
class Indicator(Protocol):
    def compute(self, prices: PriceFrame) -> pd.Series:
        """심볼 프레임 → 날짜 인덱스 시리즈. 값@D는 ≤D 데이터만 사용."""
        ...


def asof_value(series: pd.Series, d: date) -> float | None:
    """시리즈에서 날짜 d 이하(<=)의 가장 최근 값을 반환. 없으면 None.

    PriceFrame.asof와 같은 as-of 조회 규칙이라, 규칙 판정이 "D-1 종가 기준"처럼
    특정 시점 값을 뽑을 때 룩어헤드 없이 재사용할 수 있다.
    """
    ts = pd.Timestamp(d).normalize()
    pos = series.index.searchsorted(ts, side="right") - 1
    if pos < 0:
        return None
    val = series.iloc[pos]
    if pd.isna(val):
        return None
    return float(val)
