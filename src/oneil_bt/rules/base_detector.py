"""베이스 감지기 — 시작점·피벗·깊이·기간·무효화/리셋 (규칙서 §5, 계획서 §3.4, Phase 3A).

한 심볼의 세션을 한 번 전방 스캔하며 "지금 유효하게 다져진 베이스"를 상태로 유지한다.
경로 의존 상태(직전 신고가·직전 저점·성숙 여부)라 Phase 2의 시장필터와 같은 사유로
전방 스캔 후 as-of 조회 형태로 구현한다.

정의(규칙서 §5):
    1. 시작점  = 조정이 시작된 신고가 발생일. 새 고가 갱신 시 시작점도 그 날로 이동.
    2. 피벗    = 베이스 기간 장중 최고가(단일 피벗, 대체 피벗 불채택).
    3. 깊이 D% = (피벗 − 베이스 최저 장중 저가) ÷ 피벗 × 100. 장중 고저 기준.
    4. 기간    = 돌파일 − 시작일(달력일). 최소 N주 충족 = 경과 ≥ 7×N일.

기간·깊이 판정(config `base.depth_tiers`, 깊이 오름차순):
    D ≤ 15% → 5주 / 15% < D ≤ 33% → 7주 / D > 33% → 패턴 무효.

무효화·리셋:
    - 기간 미충족 상태에서 피벗 상회 → 카운트 무효, 그 신고가에서 재시작.
    - D > 33% → 패턴 무효, 회복 랠리 후 형성되는 고가에서 재시작.
    두 경우 모두 "그 바에서 베이스를 다시 무장"으로 통일한다. 전자는 신고가(피벗=고가)
    에서, 후자는 하락 바(피벗=그 바 고가)에서 재시작하며, 이후 회복 랠리의 신고가마다
    시작점이 위로 따라 이동해(전자의 미성숙 상회 로직) 랠리 정점이 새 시작점으로 남는다.

단계 카운트는 `StageTracker`에 위임한다(규칙서 §5 단계). 스캔 중 매 바 `on_bar`,
새 베이스가 성숙할 때 `stage_for_new_base`, 유효 돌파 시 `on_breakout`을 호출한다.

타이밍(§6.1): `base_asof(d)`는 구조값(시작점·피벗·저점·깊이)을 **직전 세션(≤d-1)
까지로 확정**하고 기간만 돌파일 d 기준으로 계산한 유효 베이스를 돌려준다.
`is_breakout(d, base)`는 d 장중 고가가 피벗 이상인지 본다. 따라서 d의 가격을
조작해도 `base_asof(d)`는 불변(룩어헤드 없음).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from ..domain.bar import PriceFrame
from ..domain.config import Config, DepthTier
from ..indicators.indicator_set import IndicatorSet
from .stage_tracker import StageTracker


@dataclass(frozen=True)
class Base:
    """확정된 유효 베이스 스냅샷(계획서 §3.4)."""

    start: date
    pivot: float
    base_low: float
    depth_pct: float
    weeks_elapsed: float
    min_weeks: int
    tier: str
    stage: int


@dataclass(frozen=True)
class Breakout:
    """유효 돌파 이력(테스트·디버깅용)."""

    date: date
    stage: int
    pivot: float


class BaseDetector:
    def __init__(self, prices: PriceFrame, ind: IndicatorSet, cfg: Config) -> None:
        self.prices = prices
        self.ind = ind
        self._cfg = cfg
        self.bcfg = cfg.base
        self._index: pd.DatetimeIndex = prices.df.index  # type: ignore[assignment]
        self._dates: list[date] = [ts.date() for ts in self._index]
        self._breakouts: list[Breakout] = []
        # 세션별 다짐(consolidation) 상태 후보: (start_pos, pivot, base_low, stage).
        # 구조값(피벗·저점)은 ≤그 세션으로 확정된다. 성숙·기간 판정은 base_asof에서
        # 돌파일 d 기준으로 계산한다(§6.1: 기간 = 돌파일 − 시작일).
        self._cand: list[tuple[int, float, float, int] | None] = []
        self._scan()

    # ------------------------------------------------------------------ #
    # 전방 스캔
    # ------------------------------------------------------------------ #
    def _scan(self) -> None:
        df = self.prices.df
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)
        dates = self._dates
        n = len(dates)

        tracker = StageTracker(self._cfg)
        self._cand = [None] * n
        if n == 0:
            return

        # 초기 베이스 후보: 첫 바를 임시 정점으로 무장.
        start_pos = 0
        pivot = highs[0]
        base_low = lows[0]
        base_stage = 1
        self._cand[0] = (start_pos, pivot, base_low, base_stage)

        for i in range(1, n):
            h, low, c = highs[i], lows[i], closes[i]
            tracker.on_bar(c, low)

            if h >= pivot:
                # 피벗 도달 — 성숙했으면 유효 돌파, 아니면 미성숙 상회. 어느 쪽이든
                # 이 신고가에서 베이스를 다시 무장한다(규칙서 §5 정의1·무효화1).
                elapsed = (dates[i] - dates[start_pos]).days
                if self._matured(self._depth(pivot, base_low), elapsed):
                    tracker.on_breakout(dates[i], c, base_low, base_stage)
                    self._breakouts.append(Breakout(dates[i], base_stage, pivot))
                start_pos, pivot, base_low = i, h, low
                base_stage = tracker.stage_for_new_base(
                    dates[i], self._depth(pivot, base_low)
                )
                self._cand[i] = (start_pos, pivot, base_low, base_stage)
                continue

            # 피벗 아래 — 베이스 진행 중. 저점 갱신 후 깊이 판정.
            base_low = min(base_low, low)
            if self._depth(pivot, base_low) > self.bcfg.invalid_depth_pct:
                # D>33% 패턴 무효 → 이 바에서 재시작(피벗=현재 고가). 이후 회복
                # 랠리의 신고가마다 위 분기가 시작점을 정점으로 끌어올린다.
                start_pos, pivot, base_low = i, h, low

            base_stage = tracker.stage_for_new_base(
                dates[i], self._depth(pivot, base_low)
            )
            self._cand[i] = (start_pos, pivot, base_low, base_stage)

    # ------------------------------------------------------------------ #
    # 판정 헬퍼
    # ------------------------------------------------------------------ #
    @staticmethod
    def _depth(pivot: float, base_low: float) -> float:
        if pivot <= 0 or math.isnan(pivot) or math.isnan(base_low):
            return 0.0
        return (pivot - base_low) / pivot * 100.0

    def _tier_for_depth(self, depth: float) -> DepthTier | None:
        """깊이가 속하는 티어(가장 작은 max_depth_pct부터). 초과하면 None(패턴 무효)."""
        for tier in self.bcfg.depth_tiers:
            if depth <= tier.max_depth_pct:
                return tier
        return None

    def _matured(self, depth: float, elapsed_days: int) -> bool:
        """깊이가 유효 티어에 속하고 최소 기간(7×N 달력일)을 채웠는가."""
        tier = self._tier_for_depth(depth)
        if tier is None:
            return False
        return elapsed_days >= self.bcfg.min_days_per_week * tier.min_weeks

    # ------------------------------------------------------------------ #
    # 공개 API
    # ------------------------------------------------------------------ #
    def base_asof(self, d: date) -> Base | None:
        """d에 돌파할 수 있는 유효 베이스. 없으면 None.

        구조값(시작점·피벗·저점·깊이)은 직전 세션(≤d-1)까지로 확정하고, 기간은
        규칙서 §5대로 **돌파일 d − 시작일**로 계산한다. 따라서 d의 가격을 조작해도
        결과가 바뀌지 않는다(룩어헤드 없음).
        """
        ts = pd.Timestamp(d).normalize()
        pos = self._index.searchsorted(ts, side="left") - 1  # 마지막 세션 < d
        if pos < 0 or self._cand[pos] is None:
            return None
        start_pos, pivot, base_low, stage = self._cand[pos]  # type: ignore[misc]
        start_date = self._dates[start_pos]
        depth = self._depth(pivot, base_low)
        tier = self._tier_for_depth(depth)
        if tier is None:
            return None
        elapsed = (d - start_date).days
        if elapsed < self.bcfg.min_days_per_week * tier.min_weeks:
            return None
        return Base(
            start=start_date,
            pivot=pivot,
            base_low=base_low,
            depth_pct=depth,
            weeks_elapsed=elapsed / self.bcfg.min_days_per_week,
            min_weeks=tier.min_weeks,
            tier=f"<={tier.max_depth_pct:g}%",
            stage=stage,
        )

    def is_breakout(self, d: date, base: Base) -> bool:
        """d 장중 고가가 피벗 이상이면 돌파. d에 실거래 바가 없으면 False."""
        row = self.prices.row(d)
        if row is None:
            return False
        return bool(float(row["high"]) >= base.pivot)

    @property
    def breakouts(self) -> list[Breakout]:
        """스캔 중 확정된 유효 돌파 이력(발생일 오름차순)."""
        return list(self._breakouts)
