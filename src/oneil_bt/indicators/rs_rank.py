"""RS 백분위 랭크 — Q14 전시장 대장주 프록시 게이트 (plan/q14_rs_rank.md §3).

편집형 테마 분류(§3 1단계 "해당 테마의 1~2등 대장주")는 재현 불가(사후 정보)라, 근사로
"전시장 RS 6M 백분위 상위 X%"를 게이트로 쓴다. 엔진(engine.py `_prepare`)과 스크리너
(`scripts/screen_today.py`)가 이 모듈 하나만 공유한다 — 랭크 산식 이중 구현 금지(§6).

랭크 대상은 기존 `IndicatorSet.rs_6m`(종목 6M수익 - 자기 시장 지수 6M수익) 그대로다
(Q14-1). 별도 지표를 신설하지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import Final

import pandas as pd

from ..domain.enums import Market

# Q14-4: as-of 정렬 전방채움 한도(세션). 일시 거래정지는 살리고 장기 정지·상폐 잔존값의
# 모집단 오염은 막는 절충값 — 민감도 이슈가 실측되면 그때 설정화한다.
STALE_LIMIT_SESSIONS: Final[int] = 10

_SCOPES = ("all", "market")


def build_rs_rank_table(
    rs_by_symbol: dict[str, pd.Series],
    calendar: pd.DatetimeIndex,
    market_by_symbol: dict[str, Market],
    scope: str,
) -> pd.DataFrame:
    """종목별 rs_6m 시리즈 → 캘린더 축 횡단면 백분위 wide 프레임 (날짜×심볼).

    각 종목 시리즈(자기 거래일 인덱스)를 마스터 `calendar`로 정렬(reindex)한 뒤
    `STALE_LIMIT_SESSIONS` 세션까지만 전방채움한다. 126일 이력 부족·그 이상 결측
    (거래정지·상폐 잔존)은 NaN으로 남아 해당일 모집단에서 제외된다(Q14-4).

    scope="all": 행(날짜) 단위 전시장 통합 백분위 — `rank(axis=1, pct=True)`
    (method="average" 기본, pct = 비NaN 모집단 중 자기 이하 비율).
    scope="market": 시장별(KOSPI/KOSDAQ) 컬럼 그룹 안에서만 랭크한 뒤 원래 컬럼
    순서로 재조합한다. deprecated `groupby(axis=1)` 대신 시장별 부분프레임을
    직접 만들어 concat한다.
    """
    if scope not in _SCOPES:
        raise ValueError(f"unsupported rs rank scope: {scope!r} (expected one of {_SCOPES})")

    columns = list(rs_by_symbol)
    aligned = {
        sym: series.reindex(calendar).ffill(limit=STALE_LIMIT_SESSIONS)
        for sym, series in rs_by_symbol.items()
    }
    wide = pd.DataFrame(aligned, index=calendar, columns=columns)

    if scope == "all":
        return wide.rank(axis=1, pct=True)

    # scope == "market" — 시장별로 나눠 랭크한 뒤 컬럼 순서를 보존해 재조합.
    parts = []
    for m in sorted(set(market_by_symbol.get(c) for c in columns) - {None}):
        cols = [c for c in columns if market_by_symbol.get(c) == m]
        parts.append(wide[cols].rank(axis=1, pct=True))
    if not parts:
        return wide  # 시장 매핑이 전혀 없으면(방어적) 빈 프레임 그대로.
    combined = pd.concat(parts, axis=1)
    return combined[columns]


def rank_pct_asof(table: pd.DataFrame, symbol: str, d: date) -> float | None:
    """table에서 symbol의 d 이하(<=) 최근 랭크 백분위. 없으면 None.

    비거래일 조회에도 안전한 as-of 조회(IndicatorSet.asof와 동일 idiom —
    indicators/indicator_set.py 참조). symbol이 테이블에 없거나 행이 없거나
    값이 NaN(모집단 제외)이면 None을 반환한다.
    """
    if symbol not in table.columns:
        return None
    series = table[symbol]
    ts = pd.Timestamp(d).normalize()
    pos = series.index.searchsorted(ts, side="right") - 1
    if pos < 0:
        return None
    val = series.iloc[pos]
    if pd.isna(val):
        return None
    return float(val)
