"""과열 제외 — 셋업 게이트 (규칙서 §3 과열제외, 계획서 §3.4, §11-4).

규칙서의 과열 조항 3가지:
    (a) 베이스 없이 최근 20일 +50% 이상 수직 상승 직후
    (b) 최근 2주 내 상한가 기록
    (c) 하루 ±15% 이상 급등락 반복 (테마성 과열)

v1 범위(계획서 §11-4, §12 Q3): (a)의 +50% 수직상승만 완비한다. (b)(c)는 상한가
데이터·"반복" 횟수 정의가 없어 플래그만 두고 미구현(config `swing_min_count: null`).

(a)의 '베이스 없이' 판정(§12 Q4)은 BaseDetector(Phase 3A) 연동이 필요하다.
Phase 2에서는 `excluded(d, has_base=...)`로 훅만 열어두고, 베이스 정보가 없으면
(기본 has_base=False) 수직상승만으로 제외한다. require_no_base=False면 베이스
유무와 무관하게 수직상승만으로 제외한다.
"""

from __future__ import annotations

from datetime import date

from ..domain.config import Config
from ..indicators.base import asof_value
from ..indicators.indicator_set import IndicatorSet


class OverheatingFilter:
    def __init__(self, ind: IndicatorSet, cfg: Config) -> None:
        self.ind = ind
        self.cfg = cfg.overheating

    def vertical_spike(self, d: date) -> bool:
        """직전 N일(기본 20) 수익률 ≥ 임계(기본 +50%)의 수직 상승 여부."""
        ret = asof_value(self.ind.ret_20d, d)
        if ret is None:
            return False
        return ret >= self.cfg.ret_threshold_pct / 100

    def excluded(self, d: date, *, has_base: bool = False) -> bool:
        """d 기준 과열로 진입 금지면 True.

        require_no_base=True면 '베이스 없이'가 조건이므로, 유효 베이스가 있으면
        (has_base=True) 수직상승이라도 제외하지 않는다. 베이스 정보가 아직
        없으면(기본) 수직상승만으로 제외한다.
        """
        if not self.vertical_spike(d):
            return False
        if self.cfg.require_no_base and has_base:
            return False
        return True
