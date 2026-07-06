"""ATR — 평균 진폭 (계획서 §3.3, Phase 1).

진폭(True Range) = max(high-low, |high-prev_close|, |low-prev_close|).
첫 바는 prev_close가 없어 high-low로 정의한다.

ATR은 TR의 **단순이동평균**(창=period, min_periods=period)으로 계산한다.
과거포함 롤링이라 룩어헤드가 없고 손계산 검증이 쉽다. (Wilder RMA는 후속
파라미터화 여지로 남겨둔다.) 손절 규칙의 2×ATR가 이 값을 쓴다.
"""

from __future__ import annotations

import pandas as pd

from ..domain.bar import PriceFrame


def true_range(df: pd.DataFrame) -> pd.Series:
    """일별 True Range 시리즈. index=df.index."""
    prev_close = df["close"].shift(1)
    parts = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    # 첫 바는 prev_close가 NaN → 두 항이 NaN이지만 max(skipna)로 high-low가 남는다.
    return parts.max(axis=1).rename("tr")


class AverageTrueRange:
    def __init__(self, period: int = 14) -> None:
        if period <= 0:
            raise ValueError("period must be positive")
        self.period = period

    def compute(self, prices: PriceFrame) -> pd.Series:
        tr = true_range(prices.df)
        return (
            tr.rolling(self.period, min_periods=self.period)
            .mean()
            .rename(f"atr{self.period}")
        )
