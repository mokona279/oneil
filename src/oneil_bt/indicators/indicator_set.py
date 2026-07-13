"""IndicatorSet — 심볼별 지표 사전계산·캐시 (계획서 §3.3, Phase 1).

한 심볼의 모든 지표를 생성 시점에 한 번 계산해 시리즈로 보관한다. 전부 과거포함
롤링이라 룩어헤드가 없다. Phase 2 이후의 규칙들은 이 객체를 주입받아 as-of 조회로
소비한다.

구조적으로 고정된 창(MA 50/60/120/150/200, ATR 14, 52주 252, 20일 거래대금·거래량)
은 상수로, 규칙 파라미터로 조정되는 창(과열 수익률 룩백, RS 룩백, 200MA 상승 룩백)
은 Config에서 가져온다.
"""

from __future__ import annotations

from datetime import date
from typing import Final

import pandas as pd

from ..domain.bar import PriceFrame
from ..domain.config import Config
from .atr import AverageTrueRange
from .base import asof_value
from .moving_average import MovingAverage
from .relative_strength import RelativeStrength
from .rolling_extremes import RollingHigh, RollingLow

ATR_PERIOD: Final[int] = 14
WEEKS_52_SESSIONS: Final[int] = 252
TURNOVER_WINDOW: Final[int] = 20
VOLUME_WINDOW: Final[int] = 20


class IndicatorSet:
    def __init__(
        self, prices: PriceFrame, index_prices: PriceFrame, cfg: Config
    ) -> None:
        self.symbol = prices.symbol
        df = prices.df
        self.index: pd.DatetimeIndex = df.index  # type: ignore[assignment]

        # --- 이동평균 (구조 고정 창) ---
        self.ma50 = MovingAverage(50).compute(prices)
        self.ma60 = MovingAverage(60).compute(prices)
        self.ma120 = MovingAverage(120).compute(prices)
        self.ma150 = MovingAverage(150).compute(prices)
        self.ma200 = MovingAverage(200).compute(prices)

        # --- 변동성 ---
        self.atr14 = AverageTrueRange(ATR_PERIOD).compute(prices)

        # --- 52주 고저 (장중 기준) ---
        self.high_52w = RollingHigh(WEEKS_52_SESSIONS).compute(prices)
        self.low_52w = RollingLow(WEEKS_52_SESSIONS).compute(prices)

        # --- 거래대금(20일 평균) : value 있으면 사용, 없으면 close*volume 근사 ---
        if "value" in df.columns:
            daily_value = df["value"]
        else:
            daily_value = df["close"] * df["volume"]
        self.turnover_20d = (
            daily_value.rolling(TURNOVER_WINDOW, min_periods=TURNOVER_WINDOW)
            .mean()
            .rename("turnover_20d")
        )

        # --- 거래량(20일 평균) : 돌파일 거래량 1.5× 게이트용 (Phase 4A) ---
        self.vol_ma20 = (
            df["volume"]
            .rolling(VOLUME_WINDOW, min_periods=VOLUME_WINDOW)
            .mean()
            .rename("vol_ma20")
        )

        # --- 과열 판정용 N일 수익률 ---
        ret_lookback = cfg.overheating.ret_lookback_days
        self.ret_20d = df["close"].pct_change(ret_lookback).rename("ret_20d")

        # --- 상대강도 6M ---
        self.rs_6m = RelativeStrength(
            cfg.rs.lookback_days, cfg.rs.method
        ).compute(prices, index_prices)

        # --- 200MA 상승 판정용 룩백 + 사전 비교 시리즈 ---
        self._rise_lookback = cfg.trend.ma200_rising_lookback
        # NaN 비교는 False → 이력 부족 구간은 자동으로 "상승 아님"이 된다.
        self._ma200_rising = self.ma200 > self.ma200.shift(self._rise_lookback)
        # R2a(Q3): 보조 룩백 OR — 장기 횡보 끝의 첫 돌파에서 긴 룩백의 계단 후행 보정.
        alt = cfg.trend.ma200_rising_lookback_alt
        if alt is not None:
            self._ma200_rising = self._ma200_rising | (
                self.ma200 > self.ma200.shift(int(alt))
            )

    def ma200_rising(self, d: date) -> bool:
        """200MA[d] > 200MA[d-rise_lookback]. 이력 부족·데이터 없음이면 False.

        보조 룩백(ma200_rising_lookback_alt)이 설정되면 둘 중 하나만 충족해도 상승(OR).
        d가 거래일이 아니면 d 이하 최근 거래일 기준으로 판정한다(as-of).
        """
        ts = pd.Timestamp(d).normalize()
        s = self._ma200_rising
        pos = s.index.searchsorted(ts, side="right") - 1
        if pos < 0:
            return False
        return bool(s.iloc[pos])

    def asof(self, series_name: str, d: date) -> float | None:
        """이름으로 지정한 지표 시리즈의 d 이하 최근 값(as-of). 없으면 None.

        규칙 컴포넌트가 "D-1 종가 기준" 등으로 룩어헤드 없이 값을 뽑을 때 쓴다.
        """
        series = getattr(self, series_name)
        if not isinstance(series, pd.Series):
            raise AttributeError(f"{series_name!r} is not an indicator series")
        return asof_value(series, d)
