"""meta.csv 행 생성 — name/market/listing_date/shares_out 조인 (계획서 §1.3, §2.2).

symbol,name,market,listing_date,shares_out. market은 KOSPI/KOSDAQ만 허용(로더 enum).
listing_date는 FDR에서 조인, 실패 시 빈칸 + 경고. shares_out은 최신 스냅샷(선택).
prices/에 있는 심볼이 당일 상장 목록에 없을 수 있다(거래정지·상폐 절차 진입 —
2026-07-17 012510 실측). 이때 이전 meta.csv 행을 폴백으로 재사용한다.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


def normalize_listing_dates(fdr_df: pd.DataFrame) -> dict[str, str]:
    """FDR StockListing('KRX-DESC') → {티커(6자리): ISO 상장일 문자열}.

    Code를 zero-pad 6자리로 정규화, ListingDate를 ISO(YYYY-MM-DD)로. 파싱 실패·결측은 제외.
    """
    result: dict[str, str] = {}
    for code, listing in zip(fdr_df["Code"], fdr_df["ListingDate"]):
        ticker = str(code).strip().zfill(6)
        ts = pd.to_datetime(listing, errors="coerce")
        if pd.isna(ts):
            continue
        result[ticker] = ts.strftime("%Y-%m-%d")
    return result


@dataclass
class MetaBuildResult:
    """meta 행 + 결측 심볼 목록(리포트 경고용, §1.3).

    market_fallback: 당일 상장 목록에 없어 이전 meta에서 market을 재사용한 심볼.
    market_missing: 폴백으로도 market을 못 구한 심볼 — 빈 market은 로더 검증상
                    meta.csv 전체를 죽이므로 호출자는 쓰기 전에 중단해야 한다.
    """

    rows: list[dict] = field(default_factory=list)
    missing_listing_date: list[str] = field(default_factory=list)
    market_fallback: list[str] = field(default_factory=list)
    market_missing: list[str] = field(default_factory=list)


def load_meta_fallback(path: Path | str) -> dict[str, dict[str, str]]:
    """기존 meta.csv를 검증 없이 관대하게 읽어 {symbol: 행 dict}로 돌려준다.

    당일 상장 목록에서 사라진 종목의 폴백 원천 — 부분 오염된 파일에서도 정상 행은
    살린다(MetaRepository는 한 행이라도 불량이면 전체 거부). 없으면 빈 dict.
    """
    path = Path(path)
    if not path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                sym = (row.get("symbol") or "").strip()
                if sym:
                    rows[sym] = {k: (v or "").strip() for k, v in row.items() if k}
    except OSError:
        return {}
    return rows


def _prev_shares(prev: dict[str, str]) -> object:
    raw = prev.get("shares_out", "")
    if not raw:
        return ""
    try:
        return int(float(raw))
    except ValueError:
        return ""


def build_meta_rows(
    symbols: list[str],
    names: dict[str, str],
    markets: dict[str, str],
    listing_dates: dict[str, str],
    shares_out: dict[str, int],
    fallback: dict[str, dict[str, str]] | None = None,
) -> MetaBuildResult:
    """대상 심볼별 meta 행을 만든다.

    prices/ 전수를 덮는다는 §1.3 불변식 때문에 대상에는 당일 상장 목록에 없는
    종목(거래정지·상폐 절차)이 포함될 수 있다. market·기타 필드가 비면 이전 meta
    행(fallback)에서 재사용하고, 그래도 market이 없으면 market_missing으로 보고한다.
    """
    fallback = fallback or {}
    result = MetaBuildResult()
    for sym in symbols:
        prev = fallback.get(sym, {})
        market = markets.get(sym, "")
        if not market and prev.get("market"):
            market = prev["market"]
            result.market_fallback.append(sym)
        if not market:
            result.market_missing.append(sym)
        listing = listing_dates.get(sym, "") or prev.get("listing_date", "")
        if not listing:
            result.missing_listing_date.append(sym)
        shares = shares_out.get(sym)
        result.rows.append(
            {
                "symbol": sym,
                "name": names.get(sym, "") or prev.get("name", ""),
                "market": market,
                "listing_date": listing,
                "shares_out": _prev_shares(prev) if shares is None else int(shares),
            }
        )
    return result
