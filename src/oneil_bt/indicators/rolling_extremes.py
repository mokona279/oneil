"""52주 고저 롤링 (계획서 §3.3, Phase 1).

트렌드 템플릿의 "52주 저가 +25% 이상", "52주 고가 -15% 이내" 판정에 쓴다.
**장중 고저 기준**(계획서 §3.3): 고가는 high의 롤링 최대, 저가는 low의 롤링 최소.
창=252 거래일(약 52주). min_periods=window로 창이 다 차기 전에는 NaN.
"""

from __future__ import annotations

from typing import Final

import pandas as pd

from ..domain.bar import PriceFrame

WEEKS_52_SESSIONS: Final[int] = 252


class RollingHigh:
    def __init__(self, window: int = WEEKS_52_SESSIONS) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        self.window = window

    def compute(self, prices: PriceFrame) -> pd.Series:
        return (
            prices.df["high"]
            .rolling(self.window, min_periods=self.window)
            .max()
            .rename(f"high_{self.window}")
        )


class RollingLow:
    def __init__(self, window: int = WEEKS_52_SESSIONS) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        self.window = window

    def compute(self, prices: PriceFrame) -> pd.Series:
        return (
            prices.df["low"]
            .rolling(self.window, min_periods=self.window)
            .min()
            .rename(f"low_{self.window}")
        )
