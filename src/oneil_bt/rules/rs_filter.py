"""상대강도 게이트 — 셋업 1단계 (규칙서 §3 1단계, 계획서 §3.4, §12 Q5).

1단계 체크리스트의 "최근 6개월 수익률이 소속 지수보다 높다"를 불리언으로 구현한다
(미너비니 RS 70+의 대체). IndicatorSet.rs_6m = 종목 6M수익 − 지수 6M수익이며,
> 0 이면 통과다. 이력 부족(None)이면 통과하지 않는다.

체크리스트의 나머지 두 항목(분기 영업이익 +20%·흑자, 테마 대장주)은 데이터 부재로
v1 범위에서 제외한다(계획서 §11-1, §11-2).
"""

from __future__ import annotations

from datetime import date

from ..domain.config import Config
from ..indicators.base import asof_value
from ..indicators.indicator_set import IndicatorSet


class RsFilter:
    def __init__(self, ind: IndicatorSet, cfg: Config) -> None:
        self.ind = ind
        self.cfg = cfg.rs

    def passes(self, d: date) -> bool:
        """d 기준(≤d) 종목 6M수익 > 지수 6M수익이면 True."""
        rs = asof_value(self.ind.rs_6m, d)
        if rs is None:
            return False
        return rs > 0
