"""종목 메타데이터 (계획서 §4.2 — 별도 meta.csv 채택).

meta.csv 컬럼: symbol,name,market,listing_date[,shares_out]
- market(KOSPI/KOSDAQ)은 RS 벤치마크·시장필터 매칭에 필수.
- listing_date로 IPO 베이스(상장 52주 미만) 유니버스 배제.
- shares_out은 선택(유동성 하한 대용).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..domain.enums import Market
from .loader import ValidationError, read_text_autodetect


@dataclass(frozen=True)
class SymbolMeta:
    symbol: str
    name: str
    market: Market
    listing_date: date | None
    shares_out: int | None


def _parse_date(raw: str | None) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError(f"invalid listing_date '{raw}': {exc}") from exc


def _parse_int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError as exc:
        raise ValidationError(f"invalid shares_out '{raw}': {exc}") from exc


class MetaRepository:
    """meta.csv를 로드해 심볼별 SymbolMeta를 제공."""

    _REQUIRED = ("symbol", "name", "market")

    def __init__(self, metas: dict[str, SymbolMeta]) -> None:
        self._metas = dict(metas)

    @classmethod
    def from_csv(cls, path: Path | str) -> "MetaRepository":
        path = Path(path)
        if not path.exists():
            raise ValidationError(f"meta csv not found: {path}")
        text = read_text_autodetect(path)
        reader = csv.DictReader(text.splitlines())
        if reader.fieldnames is None:
            raise ValidationError(f"meta csv is empty: {path}")
        header = [h.strip() for h in reader.fieldnames]
        missing = [c for c in cls._REQUIRED if c not in header]
        if missing:
            raise ValidationError(f"meta csv missing columns {missing}: {path}")

        metas: dict[str, SymbolMeta] = {}
        for i, row in enumerate(reader, start=2):  # 2 = 헤더 다음 첫 데이터 행
            symbol = (row.get("symbol") or "").strip()
            if not symbol:
                raise ValidationError(f"{path}:{i} empty symbol")
            if symbol in metas:
                raise ValidationError(f"{path}:{i} duplicate symbol '{symbol}'")
            market_raw = (row.get("market") or "").strip().upper()
            try:
                market = Market(market_raw)
            except ValueError as exc:
                raise ValidationError(
                    f"{path}:{i} invalid market '{market_raw}' (expected KOSPI/KOSDAQ)"
                ) from exc
            metas[symbol] = SymbolMeta(
                symbol=symbol,
                name=(row.get("name") or "").strip(),
                market=market,
                listing_date=_parse_date(row.get("listing_date")),
                shares_out=_parse_int(row.get("shares_out")),
            )
        return cls(metas)

    def get(self, symbol: str) -> SymbolMeta:
        try:
            return self._metas[symbol]
        except KeyError as exc:
            raise ValidationError(f"no metadata for symbol '{symbol}'") from exc

    def has(self, symbol: str) -> bool:
        return symbol in self._metas

    def symbols(self) -> list[str]:
        return sorted(self._metas)
