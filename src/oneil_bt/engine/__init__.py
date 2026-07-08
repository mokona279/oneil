"""백테스트 엔진 레이어 (Phase 6).

전 컴포넌트(data·indicators·rules·execution·portfolio)를 조립해 일별 이벤트 루프로
백테스트를 돌린다. 조립 지점이라 의존은 가장 넓지만, 각 컴포넌트의 계약(인터페이스)
만 소비하고 새 규칙 수치를 만들지 않는다.
"""

from __future__ import annotations

from .context import (
    BacktestResult,
    DailyRecord,
    EventRecord,
    MarketContext,
    SymbolContext,
    TradePlan,
    TradeRecord,
)
from .engine import BacktestEngine

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "DailyRecord",
    "EventRecord",
    "MarketContext",
    "SymbolContext",
    "TradePlan",
    "TradeRecord",
]
