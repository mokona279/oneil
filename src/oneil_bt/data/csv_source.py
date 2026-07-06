"""CsvDataSource — CSV 기반 DataSource 구현 (계획서 §3.2, §4).

레이아웃:
- price_dir/{symbol}.csv         종목 일봉
- index_paths[Market] -> path    시장 지수 일봉
- meta.csv                       종목 메타데이터 (MetaRepository)

로드 결과는 캐시해 동일 심볼 재로드 시 재파싱하지 않는다(결정론엔 영향 없음).
"""

from __future__ import annotations

from pathlib import Path

from ..domain.bar import PriceFrame
from ..domain.enums import Market
from .loader import CsvBarLoader, ValidationError
from .metadata import MetaRepository, SymbolMeta


class CsvDataSource:
    def __init__(
        self,
        price_dir: Path | str,
        index_paths: dict[Market, Path | str],
        meta: MetaRepository,
        loader: CsvBarLoader | None = None,
    ) -> None:
        self._price_dir = Path(price_dir)
        if not self._price_dir.is_dir():
            raise ValidationError(f"price_dir is not a directory: {self._price_dir}")
        self._index_paths = {m: Path(p) for m, p in index_paths.items()}
        self._meta = meta
        self._loader = loader or CsvBarLoader()
        self._price_cache: dict[str, PriceFrame] = {}
        self._index_cache: dict[Market, PriceFrame] = {}

    # ------------------------------------------------------------------ #
    def symbols(self) -> list[str]:
        """price_dir의 *.csv 파일명(확장자 제외)을 정렬해 반환."""
        return sorted(p.stem for p in self._price_dir.glob("*.csv"))

    def load_prices(self, symbol: str) -> PriceFrame:
        if symbol not in self._price_cache:
            path = self._price_dir / f"{symbol}.csv"
            self._price_cache[symbol] = self._loader.load(path, symbol=symbol)
        return self._price_cache[symbol]

    def load_index(self, market: Market) -> PriceFrame:
        if market not in self._index_cache:
            path = self._index_paths.get(market)
            if path is None:
                raise ValidationError(f"no index path configured for {market}")
            self._index_cache[market] = self._loader.load_index(
                path, symbol=f"INDEX_{market}"
            )
        return self._index_cache[market]

    def meta(self, symbol: str) -> SymbolMeta:
        return self._meta.get(symbol)
