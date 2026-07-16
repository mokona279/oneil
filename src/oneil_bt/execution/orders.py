"""주문 객체 — 체결 모델 입력 (계획서 §3.1, §6.2).

`Order`는 "무엇을 어떤 가격 조건으로 얼마나" 사고팔지를 담는 불변 지시다. 트리거가
(스탑/지정가 발동가)와 지정가 상한(추격/트랜치 상한)을 함께 실어, 체결 모델이 당일
바(OHLC)와 대조해 체결가를 결정한다.

규칙서 §4 매수:
    - 1차 돌파: 자동감시(스탑) 매수. 트리거 = 피벗, 지정가 상한 = 피벗 +5%(추격 한도).
    - 2·3차 피라미딩: 지정가 매수. 트리거 = 1차가 +2.5%/+5%, 상한 = 트리거 +3%.
      다음날 갭이 상한을 넘어 있으면 그 회차는 건너뛴다.

규칙서 §6 매도(Phase 4B):
    - 시장가 매도. 손절(장중 자동스탑 대안)일 때만 `trigger`에 손절가를 실어, 갭하락 시
      `min(시가, 손절가)` 체결을 계산한다. 세금이 시장별이라 `market`을 함께 싣는다.

수량(`qty`)은 사이저(Phase 5)가 채운다. Phase 4A/4B의 체결 테스트에서는 직접 지정한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import EntryReason, ExitReason, Market, OrderKind


@dataclass(frozen=True)
class Order:
    """단일 주문. `trigger`/`limit_cap`은 매수 계열에서 체결가 결정에 쓰인다."""

    symbol: str
    kind: OrderKind
    reason: EntryReason | ExitReason
    qty: int
    trigger: float | None = None    # 스탑/지정가 발동가 (STOP_BUY: 피벗, LIMIT_BUY: 트리거, 손절: 손절가)
    limit_cap: float | None = None  # 지정가 상한 (초과 갭이면 미체결/스킵)
    market: Market | None = None    # 매도 세금 계산용 (시장별 거래세)

    @classmethod
    def breakout(
        cls,
        symbol: str,
        pivot: float,
        qty: int,
        chase_limit_pct: float,
        reason: EntryReason = EntryReason.BREAKOUT_T1,
    ) -> "Order":
        """1차 돌파 스탑 매수: 트리거=피벗, 상한=피벗×(1+추격한도%)."""
        return cls(
            symbol=symbol,
            kind=OrderKind.STOP_BUY,
            reason=reason,
            qty=qty,
            trigger=pivot,
            limit_cap=pivot * (1.0 + chase_limit_pct / 100.0),
        )

    @classmethod
    def pyramid(
        cls,
        symbol: str,
        trigger_price: float,
        qty: int,
        cap_pct: float,
        reason: EntryReason,
    ) -> "Order":
        """2·3차 지정가 매수: 트리거=1차가 기준 상승가, 상한=트리거×(1+상한%)."""
        return cls(
            symbol=symbol,
            kind=OrderKind.LIMIT_BUY,
            reason=reason,
            qty=qty,
            trigger=trigger_price,
            limit_cap=trigger_price * (1.0 + cap_pct / 100.0),
        )

    @classmethod
    def reentry(
        cls,
        symbol: str,
        ref_price: float,
        qty: int,
        chase_limit_pct: float,
    ) -> "Order":
        """R4b 재진입 시가 매수(P4): 상한 = 트리거 확인일 종가 × (1+추격한도%).

        트리거가 없다 — 전일 종가에 이미 판정이 끝났고 당일 시가에 산다. 상한 초과
        갭이면 1차 돌파와 같은 문법으로 장중 상한 복귀 체결을 허용한다(fill_entry_open).
        """
        return cls(
            symbol=symbol,
            kind=OrderKind.MARKET_BUY,
            reason=EntryReason.REENTRY_50MA,
            qty=qty,
            limit_cap=ref_price * (1.0 + chase_limit_pct / 100.0),
        )

    @classmethod
    def exit(
        cls,
        symbol: str,
        qty: int,
        reason: ExitReason,
        market: Market,
        stop_price: float | None = None,
    ) -> "Order":
        """청산 시장가 매도. `stop_price`는 손절 장중스탑(대안 모델) 체결가 계산용."""
        return cls(
            symbol=symbol,
            kind=OrderKind.MARKET_SELL,
            reason=reason,
            qty=qty,
            trigger=stop_price,
            market=market,
        )
