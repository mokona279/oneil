"""거래 값 객체 — 체결(Fill)·보유 포지션(Position) (계획서 §3.1).

체결(`Fill`)은 진입·청산 공통의 최소 사실 단위다: 발생일·체결가·수량·사유·비용.
비용은 CostModel이 계산해 넣어준다(수수료·슬리피지, 매도 시 거래세 포함).

`Position`은 보유 중인 한 트레이드의 경로 의존 상태(평단·수량·손절가·60MA 이탈일)를
담는 스냅샷이다. Phase 4B의 손절·청산 규칙이 이를 읽어 판정한다. 상태 진화(피라미딩
평단 갱신·손절 재계산·청산)는 소유자인 Portfolio/Engine(Phase 5/6)이 `replace`로
새 스냅샷을 만들어 반영한다 — 값객체는 불변으로 유지한다.

`ClosedTrade`(계획서 §3.1, RiskGovernor 입력)는 청산 회계가 필요한 Phase 5에서 추가한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .enums import EntryReason, ExitReason, Market


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


@dataclass(frozen=True)
class Position:
    """보유 중인 트레이드 스냅샷 (계획서 §3.1, Phase 4B 규칙 입력).

    - `entry_date`/`entry_price`: 1차 돌파 체결일·체결가. 8주 룰(돌파 후 3주 내 +20%)
      과 최소보유 기간의 기준점이다.
    - `avg_price`: 평균 매수가. 피라미딩으로 오르면 손절가를 재계산한다(규칙서 §4).
    - `stop_price`: 현재 손절가(StopRule.stop_price 산출값). 손절 판정은 이 값 대비.
    - `trend_break_date`: 60MA 이탈로 절반 매도가 발동한 날. 잔량 매도(3거래일 회복
      실패)의 카운터 시작점이며, 미이탈이면 None.
    """

    symbol: str
    market: Market
    entry_date: date
    entry_price: float
    avg_price: float
    qty: int
    stop_price: float
    trend_break_date: date | None = None
