"""트렌드 템플릿 — 셋업 0단계 자동 필터 (규칙서 §3 0단계, 계획서 §3.4).

미너비니 트렌드 템플릿 7조건의 AND. 통과 종목만 1단계(RS)로 넘긴다.

    1. 주가 > 150일선 AND 주가 > 200일선     (above_ma)
    2. 150일선 > 200일선                       (ma150_gt_ma200)
    3. 200일선 최소 1개월 이상 상승 중          (ma200_rising)
    4. 50일선 > 150일선 (정배열)               (ma50_gt_ma150)
    5. 주가 ≥ 52주 저가 × (1 + 25%)            (low_52w_gain_min_pct)
    6. 주가 ≥ 52주 고가 × (1 − 15%)            (high_52w_within_pct)
    7. 20일 평균 거래대금 ≥ 100억               (turnover_20d_min_krw)

'주가'는 종가다. 모든 값은 as-of d(≤d)로 조회하며, 이력 부족으로 어느 하나라도
값이 없으면(None) 통과하지 않는다. 진입 판정 시 엔진은 D-1을 넘겨 §6.1 타이밍
계약(게이트는 D-1 종가 기준)을 만족시킨다.
"""

from __future__ import annotations

from datetime import date

from ..domain.bar import PriceFrame
from ..domain.config import Config
from ..indicators.base import asof_value
from ..indicators.indicator_set import IndicatorSet


class TrendTemplateFilter:
    def __init__(self, prices: PriceFrame, ind: IndicatorSet, cfg: Config) -> None:
        self.prices = prices
        self.ind = ind
        self.cfg = cfg.trend
        # 레벨(50/150/200 등) → 해당 이동평균 시리즈. 구조 고정 창만 존재한다.
        self._ma_by_period = {
            50: ind.ma50,
            60: ind.ma60,
            120: ind.ma120,
            150: ind.ma150,
            200: ind.ma200,
        }

    def _ma(self, period: int, d: date) -> float | None:
        series = self._ma_by_period.get(period)
        if series is None:
            raise KeyError(f"trend_template requires MA{period} but it is not precomputed")
        return asof_value(series, d)

    def passes(self, d: date) -> bool:
        """d 기준(≤d) 7조건 AND. 값 하나라도 없으면 False."""
        c = asof_value(self.prices.df["close"], d)
        if c is None:
            return False

        # 1. 주가 > 지정 이동평균들
        for period in self.cfg.above_ma:
            ma = self._ma(int(period), d)
            if ma is None or not (c > ma):
                return False

        # 2. 150일선 > 200일선
        if self.cfg.ma150_gt_ma200:
            ma150 = self._ma(150, d)
            ma200 = self._ma(200, d)
            if ma150 is None or ma200 is None or not (ma150 > ma200):
                return False

        # 3. 200일선 1개월 이상 상승
        if not self.ind.ma200_rising(d):
            return False

        # 4. 50일선 > 150일선
        if self.cfg.ma50_gt_ma150:
            ma50 = self._ma(50, d)
            ma150 = self._ma(150, d)
            if ma50 is None or ma150 is None or not (ma50 > ma150):
                return False

        # 5. 52주 저가 대비 +N% 이상
        low_52w = asof_value(self.ind.low_52w, d)
        if low_52w is None or not (c >= low_52w * (1 + self.cfg.low_52w_gain_min_pct / 100)):
            return False

        # 6. 52주 고가 대비 -N% 이내
        high_52w = asof_value(self.ind.high_52w, d)
        if high_52w is None or not (c >= high_52w * (1 - self.cfg.high_52w_within_pct / 100)):
            return False

        # 7. 20일 평균 거래대금 하한
        turnover = asof_value(self.ind.turnover_20d, d)
        if turnover is None or not (turnover >= self.cfg.turnover_20d_min_krw):
            return False

        return True
