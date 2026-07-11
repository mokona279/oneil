"""티커 목록 → 대상 심볼 리스트 (계획서 §5.4). 순수 함수.

기본은 보통주만: 티커 끝자리 '0'(관례상 우선주는 5/7/9/K 등), 종목명에 '스팩' 포함 제외.
ETF/ETN/리츠는 get_market_ticker_list에 원래 안 나오므로 별도 처리 불필요.

생존편향: --end 시점 상장 종목만 수집하므로 기간 중 상폐 종목은 빠진다. v1은 감수·문서화
(§5.4, §9 Q2).
"""

from __future__ import annotations

_SPAC_KEYWORD = "스팩"


def is_common_stock(ticker: str) -> bool:
    """보통주 여부(관례): 6자리 티커의 끝자리가 '0'."""
    return len(ticker) == 6 and ticker.endswith("0")


def select_universe(
    tickers: list[str],
    names: dict[str, str],
    *,
    include_non_common: bool = False,
) -> list[str]:
    """티커 목록을 보통주 필터·스팩 제외로 걸러 정렬·중복제거한 대상 심볼로 반환.

    include_non_common=True면 보통주 필터를 끄고(우선주 등 포함) 스팩만 제외한다.
    """
    result: set[str] = set()
    for t in tickers:
        if not include_non_common and not is_common_stock(t):
            continue
        name = names.get(t, "")
        if _SPAC_KEYWORD in name:
            continue
        result.add(t)
    return sorted(result)
