"""거래 값 객체 — 체결(Fill) (계획서 §3.1).

체결(`Fill`)은 진입·청산 공통의 최소 사실 단위다: 발생일·체결가·수량·사유·비용.
비용은 CostModel이 계산해 넣어준다(수수료·슬리피지, 매도 시 거래세 포함).

Phase 4A는 진입/피라미딩 체결까지만 다룬다. `Position`·`ClosedTrade`(계획서 §3.1)는
보유·손절 상태가 필요한 Phase 4B/5에서 이 모듈에 추가한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .enums import EntryReason, ExitReason


@dataclass(frozen=True)
class Fill:
    """단일 체결. 비용은 이미 반영된 값(CostModel 산출)."""

    date: date
    price: float
    qty: int
    reason: EntryReason | ExitReason
    cost: float

    @property
    def notional(self) -> float:
        """체결 명목금액(가격 × 수량), 비용 제외."""
        return self.price * self.qty

    @property
    def cash_out(self) -> float:
        """매수 관점 총 현금 지출 = 명목금액 + 비용."""
        return self.notional + self.cost

    @property
    def cash_in(self) -> float:
        """매도 관점 순 현금 유입 = 명목금액 − 비용."""
        return self.notional - self.cost
