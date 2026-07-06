"""DataSource 추상 계약 (계획서 §3.2).

구조적 서브타이핑(Protocol)으로 계약을 고정한다. CsvDataSource가 첫 구현이고,
이후 API 소스 등으로 교체 가능(의존성 주입).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.bar import PriceFrame
from ..domain.enums import Market
from .metadata import SymbolMeta


@runtime_checkable
class DataSource(Protocol):
    def symbols(self) -> list[str]:
        """운용 대상 종목 코드 목록 (결정론적 정렬)."""
        ...

    def load_prices(self, symbol: str) -> PriceFrame:
        """종목 일봉 PriceFrame."""
        ...

    def load_index(self, market: Market) -> PriceFrame:
        """시장 지수 일봉 PriceFrame (RS·시장필터·캘린더 기준)."""
        ...

    def meta(self, symbol: str) -> SymbolMeta:
        """종목 메타데이터."""
        ...
