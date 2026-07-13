"""베이스 품질 요건 — 진입 가능 4요건 (규칙서 §5 품질요건, 계획서 §3.4, Phase 3B).

유효 베이스(Phase 3A `Base`)가 돌파일 d에 실제로 매수 가능한 품질인지 판정한다.
규칙서 §5 "품질 요건(전부 충족 시 진입 가능)":
    1. 과열 제외 미해당 (OverheatingFilter 재사용).
    2. 2×ATR(14) ≤ 피벗의 10%.
    3. 수축: 돌파 직전 10거래일 장중 고저 범위 ≤ 피벗의 10%.
    4. 드라이업: 돌파 직전 10거래일 평균 거래량 < 베이스 전체 일평균 거래량.

타이밍(§6.1): 구조 품질은 **직전 세션(≤d-1)까지의 정보만으로 확정**한다. 돌파일 d의
가격·거래량은 판정에 쓰지 않으므로 d를 조작해도 결과가 불변이다(룩어헤드 없음).
'직전 10거래일'·'베이스 전체'는 모두 [start, d-1] 구간의 실거래 세션으로 계산한다.

과열(요건1)은 `has_base=True`로 조회한다 — 판정 시점에 유효 베이스가 손에 있으므로
'베이스 없이 수직상승'(과열 조항 a)에는 해당할 수 없다. v1은 과열 조항 a만 구현돼
있어(§11-4, §12 Q3) 이 요건은 사실상 통과하며, ±15% 스윙·상한가(b·c)는 데이터 확보
시 OverheatingFilter에 추가되면 자동으로 반영된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from ..domain.bar import PriceFrame
from ..domain.config import Config
from ..indicators.base import asof_value
from ..indicators.indicator_set import IndicatorSet
from .base_detector import Base
from .overheating import OverheatingFilter


@dataclass(frozen=True)
class QualityResult:
    """4요건 개별 판정 + 종합. `passed`가 True면 진입 가능."""

    not_overheated: bool
    atr_ok: bool
    contraction_ok: bool
    dryup_ok: bool

    @property
    def passed(self) -> bool:
        return (
            self.not_overheated
            and self.atr_ok
            and self.contraction_ok
            and self.dryup_ok
        )


class BaseQualityCheck:
    def __init__(
        self,
        prices: PriceFrame,
        ind: IndicatorSet,
        overheating: OverheatingFilter,
        cfg: Config,
    ) -> None:
        self.prices = prices
        self.ind = ind
        self.overheating = overheating
        self.qcfg = cfg.quality
        self._index: pd.DatetimeIndex = prices.df.index  # type: ignore[assignment]
        self._highs = prices.df["high"].to_numpy(dtype=float)
        self._lows = prices.df["low"].to_numpy(dtype=float)
        self._vols = prices.df["volume"].to_numpy(dtype=float)

    def passes(self, d: date, base: Base) -> QualityResult:
        """돌파일 d에 base가 진입 가능한 품질인가. 판정은 ≤d-1 정보만 사용."""
        ts = pd.Timestamp(d).normalize()
        pos = int(self._index.searchsorted(ts, side="left")) - 1  # 마지막 세션 < d
        if pos < 0:
            return QualityResult(False, False, False, False)
        prev_date = self._index[pos].date()

        return QualityResult(
            not_overheated=not self.overheating.excluded(prev_date, has_base=True),
            atr_ok=self._atr_ok(prev_date, base.pivot),
            contraction_ok=self._contraction_ok(pos, prev_date, base.pivot),
            dryup_ok=self._dryup_ok(pos, base.start),
        )

    # ------------------------------------------------------------------ #
    # 개별 요건
    # ------------------------------------------------------------------ #
    def _atr_ok(self, prev_date: date, pivot: float) -> bool:
        """2×ATR(14) ≤ 피벗 × atr_le_pivot_pct%."""
        atr = asof_value(self.ind.atr14, prev_date)
        if atr is None or pivot <= 0:
            return False
        return 2.0 * atr <= pivot * self.qcfg.atr_le_pivot_pct / 100.0

    def _contraction_ok(self, pos: int, prev_date: date, pivot: float) -> bool:
        """직전 N거래일 장중 고저 범위 ≤ 임계.

        임계는 기본 피벗 × contraction_le_pivot_pct%. `contraction_atr_mult`(R1, Q1b)가
        설정되면 max(피벗%, k×ATR(d-1)) 하이브리드 — 저변동 종목은 현행과 동일하고,
        고변동 주도주만 자기 변동성만큼 완화된다. ATR 미확정이면 현행 기준으로 폴백.
        """
        lookback = self.qcfg.contraction_lookback
        lo = pos - lookback + 1
        if lo < 0 or pivot <= 0:
            return False  # 확인할 세션 부족 → 품질 미확정(불통과)
        window_high = self._highs[lo : pos + 1].max()
        window_low = self._lows[lo : pos + 1].min()
        rng = window_high - window_low
        if math.isnan(rng):
            return False
        threshold = pivot * self.qcfg.contraction_le_pivot_pct / 100.0
        k = self.qcfg.contraction_atr_mult
        if k is not None:
            atr = asof_value(self.ind.atr14, prev_date)
            if atr is not None:
                threshold = max(threshold, k * atr)
        return bool(rng <= threshold)

    def _dryup_ok(self, pos: int, start: date) -> bool:
        """직전 N거래일 평균 거래량 < 베이스 전체([start, d-1]) 일평균 거래량."""
        lookback = self.qcfg.dryup_lookback
        lo = pos - lookback + 1
        if lo < 0:
            return False
        start_ts = pd.Timestamp(start).normalize()
        start_pos = int(self._index.searchsorted(start_ts, side="left"))
        if start_pos > pos:
            return False
        recent_avg = self._vols[lo : pos + 1].mean()
        base_avg = self._vols[start_pos : pos + 1].mean()
        if math.isnan(recent_avg) or math.isnan(base_avg):
            return False
        return bool(recent_avg < base_avg)
