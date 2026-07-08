"""거래비용 모델 (계획서 §3.5, §6.4, §5 costs.yaml).

편도 수수료(bp)·슬리피지(bp)는 매수·매도 공통, 거래세(bp)는 **매도에만** 부과하며
시장(KOSPI/KOSDAQ)·시행일 기준 계단으로 달라진다. 비용은 명목금액(가격×수량)에
bp를 곱한 금액이며, 슬리피지는 체결가를 흔들지 않고 비용 항목으로 반영한다(결정론).

세금 계단은 `costs.yaml`의 `sell_tax_schedule`(시행일 오름차순, Config가 정렬 보장).
매도일 d에 적용되는 세율 = d 이상인 마지막(가장 최근) 시행일의 세율.
"""

from __future__ import annotations

from datetime import date

from ..domain.config import CostCfg
from ..domain.enums import Market

_BP = 1.0e4  # basis point 분모 (1bp = 1/10000)


class CostModel:
    def __init__(self, cfg: CostCfg) -> None:
        self._commission_bp = cfg.commission_bp
        self._slippage_bp = cfg.slippage_bp
        # Config가 시행일 오름차순 정렬·비어있지 않음을 이미 보증한다.
        self._tiers = cfg.sell_tax_schedule

    @property
    def _one_way_bp(self) -> float:
        """매수·매도 공통 편도 비용(수수료 + 슬리피지)."""
        return self._commission_bp + self._slippage_bp

    def buy_cost(self, price: float, qty: int, d: date) -> float:
        """매수 비용 = 명목금액 × (수수료 + 슬리피지) bp. 세금 없음."""
        return price * qty * self._one_way_bp / _BP

    def sell_cost(self, price: float, qty: int, d: date, market: Market) -> float:
        """매도 비용 = 명목금액 × (수수료 + 슬리피지 + 거래세) bp."""
        total_bp = self._one_way_bp + self._tax_bp(d, market)
        return price * qty * total_bp / _BP

    # ------------------------------------------------------------------ #
    def _tax_bp(self, d: date, market: Market) -> float:
        """매도일 d·시장에 적용되는 거래세율(bp).

        시행일 오름차순 스케줄에서 `from_date <= d`인 마지막 계단을 고른다. d가 최초
        시행일보다 이르면(방어) 가장 이른 계단을 적용한다.
        """
        applicable = self._tiers[0]
        for tier in self._tiers:
            if tier.from_date <= d:
                applicable = tier
            else:
                break
        return applicable.kospi_bp if market is Market.KOSPI else applicable.kosdaq_bp
