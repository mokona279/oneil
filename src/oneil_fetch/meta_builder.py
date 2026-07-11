"""meta.csv 행 생성 — name/market/listing_date/shares_out 조인 (계획서 §1.3, §2.2).

symbol,name,market,listing_date,shares_out. market은 KOSPI/KOSDAQ만 허용(로더 enum).
listing_date는 FDR에서 조인, 실패 시 빈칸 + 경고. shares_out은 최신 스냅샷(선택).
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    """meta 행 + listing_date 결측 심볼(리포트 경고용, §1.3)."""

    rows: list[dict] = field(default_factory=list)
    missing_listing_date: list[str] = field(default_factory=list)


def build_meta_rows(
    symbols: list[str],
    names: dict[str, str],
    markets: dict[str, str],
    listing_dates: dict[str, str],
    shares_out: dict[str, int],
) -> MetaBuildResult:
    """대상 심볼별 meta 행을 만든다.

    market을 못 구한 심볼은 로더 검증(KOSPI/KOSDAQ 필수)을 통과 못 하므로 제외하지 않고
    호출자가 판단하도록 그대로 빈 문자열로 두지 않는다 — market 없는 심볼은 애초에 유니버스
    확정 단계에서 걸러진 상태를 전제한다. 여기서는 조인만 수행한다.
    """
    result = MetaBuildResult()
    for sym in symbols:
        listing = listing_dates.get(sym, "")
        if not listing:
            result.missing_listing_date.append(sym)
        shares = shares_out.get(sym)
        result.rows.append(
            {
                "symbol": sym,
                "name": names.get(sym, ""),
                "market": markets.get(sym, ""),
                "listing_date": listing,
                "shares_out": "" if shares is None else int(shares),
            }
        )
    return result
