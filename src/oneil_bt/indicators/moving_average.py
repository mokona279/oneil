"""단순이동평균 (계획서 §3.3, Phase 1).

전략에 필요한 MA는 50/60/120/150/200이며 전부 종가 SMA다.
`min_periods=window`로, 창이 다 차기 전에는 NaN을 유지한다(부분창 오판 방지).
값@D = mean(close[D-window+1 .. D]) 이라 룩어헤드가 없다.
"""

from __future__ import annotations

import pandas as pd

from ..domain.bar import PriceFrame


class MovingAverage:
    def __init__(self, window: int) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        self.window = window

    def compute(self, prices: PriceFrame) -> pd.Series:
        close = prices.df["close"]
        return (
            close.rolling(self.window, min_periods=self.window)
            .mean()
            .rename(f"ma{self.window}")
        )
