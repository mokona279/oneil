"""포지션 사이저 — 리스크 기반 비중·트랜치 수량 (규칙서 §1, 계획서 §3.5, Phase 5).

규칙서 §1: 1종목 목표 비중 = 손절폭 역수로 리스크를 균등화한다. 손절 시 계좌의
`risk_per_trade_pct`(기본 1%)만 잃도록 비중을 잡되, 상한(`max_weight_pct`, 20%)을 둔다.

    비중(분수) = min(상한, risk_per_trade_pct / 손절폭%)

손절폭%는 StopRule(§6①)과 **같은** 산식으로 구한다: 2×ATR을 진입가 대비 %로 환산하되
-10% 캡으로 클램프. 손절폭이 작을수록(변동성 낮을수록) 비중이 커지고, 캡(20%)에 걸린다.
StopRule은 손절가(원)를 내고 여기선 손절폭(%)만 쓰므로 산식을 공유하되 중복 저장은 없다.

트랜치 수량(`tranche_qty`)은 목표 비중 × 트랜치 비율(50/30/20)에 해당하는 명목금액을
체결가로 나눈 **정수주**(floor)다 — 잔여 현금은 이월된다(§6.4).
"""

from __future__ import annotations

import math

from ..domain.config import Config
from ..domain.enums import StopMethod


class PositionSizer:
    def __init__(self, cfg: Config) -> None:
        self.scfg = cfg.sizing
        self.stopcfg = cfg.stop

    def stop_distance_pct(self, entry: float, atr: float) -> float:
        """진입가 대비 손절폭(%). StopRule(§6①)과 동일한 -10% 캡 클램프."""
        if entry <= 0:
            raise ValueError("entry price must be positive")
        if self.stopcfg.method is StopMethod.ATR2X:
            raw = self.stopcfg.atr_mult * atr / entry * 100.0
        else:  # FIXED_PCT — 고정 -7~8% (대안)
            raw = self.stopcfg.fixed_pct
        return min(raw, self.stopcfg.max_stop_pct)

    def target_weight(self, entry: float, atr: float) -> float:
        """목표 비중(0~1 분수) = min(상한, risk_per_trade% / 손절폭%)."""
        cap = self.scfg.max_weight_pct / 100.0
        stop_pct = self.stop_distance_pct(entry, atr)
        if stop_pct <= 0:  # 손절폭 0(=ATR 0) → 상한까지 허용
            return cap
        raw = self.scfg.risk_per_trade_pct / stop_pct  # 두 %의 비 = 분수
        return min(cap, raw)

    def target_notional(self, equity: float, weight: float) -> float:
        """전체 포지션 목표 명목금액 = 자본 × 비중 (트랜치 예약 현금 산정용)."""
        return max(0.0, equity) * max(0.0, weight)

    def tranche_qty(
        self, equity: float, weight: float, tranche_ratio: float, price: float
    ) -> int:
        """트랜치 체결 수량(정수주, floor). 자금·가격이 비정상이면 0."""
        if price <= 0 or equity <= 0 or weight <= 0 or tranche_ratio <= 0:
            return 0
        notional = equity * weight * tranche_ratio
        return int(math.floor(notional / price))
