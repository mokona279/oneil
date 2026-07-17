"""RS 백분위 랭크 공유 모듈 (plan/q14_rs_rank.md §3) 단위 테스트.

`build_rs_rank_table`/`rank_pct_asof`의 경계값·동점·NaN 이력·스테일 한도·market
스코프를 검증한다. 엔진·스크리너가 공유하는 유일한 랭크 산식이라(§6 이중 구현
금지) 여기서만 철저히 검증하면 두 소비처 모두 커버된다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from oneil_bt.domain.enums import Market
from oneil_bt.indicators.rs_rank import (
    STALE_LIMIT_SESSIONS,
    build_rs_rank_table,
    rank_pct_asof,
)


def _cal(n: int, start: str = "2020-01-01") -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.bdate_range(start=start, periods=n))


# --------------------------------------------------------------------------- #
# 경계값 — 4종목이면 25% 스텝
# --------------------------------------------------------------------------- #
def test_percentile_boundary_with_four_symbols() -> None:
    cal = _cal(5)
    rs = {
        "A": pd.Series(1.0, index=cal),
        "B": pd.Series(2.0, index=cal),
        "C": pd.Series(3.0, index=cal),
        "D": pd.Series(4.0, index=cal),
    }
    markets = {s: Market.KOSPI for s in rs}
    table = build_rs_rank_table(rs, cal, markets, "all")
    row = table.loc[cal[0]]
    assert row["A"] == pytest.approx(0.25)
    assert row["B"] == pytest.approx(0.50)
    assert row["C"] == pytest.approx(0.75)
    assert row["D"] == pytest.approx(1.00)


# --------------------------------------------------------------------------- #
# 동점 — average
# --------------------------------------------------------------------------- #
def test_tie_uses_average_rank() -> None:
    cal = _cal(3)
    rs = {
        "A": pd.Series(1.0, index=cal),
        "B": pd.Series(2.0, index=cal),
        "C": pd.Series(2.0, index=cal),
    }
    markets = {s: Market.KOSPI for s in rs}
    table = build_rs_rank_table(rs, cal, markets, "all")
    row = table.loc[cal[0]]
    assert row["A"] == pytest.approx(1 / 3)
    # B·C 동점 → 순위 2·3 평균(2.5) / 3
    assert row["B"] == pytest.approx(2.5 / 3)
    assert row["C"] == pytest.approx(2.5 / 3)


# --------------------------------------------------------------------------- #
# NaN 이력 — 모집단 제외 + as-of 조회 None
# --------------------------------------------------------------------------- #
def test_nan_history_excluded_and_asof_returns_none() -> None:
    cal = _cal(5)
    rs = {
        "A": pd.Series(1.0, index=cal),
        "B": pd.Series(2.0, index=cal),
        # C는 이력 부족(전부 NaN) — 126일 미만 워밍업 시나리오 근사.
        "C": pd.Series(np.nan, index=cal),
    }
    markets = {s: Market.KOSPI for s in rs}
    table = build_rs_rank_table(rs, cal, markets, "all")
    row = table.loc[cal[0]]
    # C는 모집단에서 빠지고, A·B 둘만으로 랭크된다(2종목 → .5/1.0).
    assert row["A"] == pytest.approx(0.5)
    assert row["B"] == pytest.approx(1.0)
    assert pd.isna(row["C"])
    assert rank_pct_asof(table, "C", cal[0].date()) is None
    assert rank_pct_asof(table, "A", cal[0].date()) == pytest.approx(0.5)


def test_asof_missing_symbol_and_missing_row_return_none() -> None:
    cal = _cal(3)
    rs = {"A": pd.Series(1.0, index=cal)}
    markets = {"A": Market.KOSPI}
    table = build_rs_rank_table(rs, cal, markets, "all")
    assert rank_pct_asof(table, "ZZZ", cal[0].date()) is None  # 심볼 없음
    before = cal[0].date() - pd.Timedelta(days=10)
    assert rank_pct_asof(table, "A", before) is None  # 첫 행보다 이전


# --------------------------------------------------------------------------- #
# 스테일 한도 — STALE_LIMIT_SESSIONS 세션까지 전방채움, 초과 시 탈락
# --------------------------------------------------------------------------- #
def test_stale_limit_drops_after_threshold_sessions() -> None:
    n = STALE_LIMIT_SESSIONS + 15
    cal = _cal(n)
    # A는 완주(항상 1.0, population 유지). B는 초반 5세션만 실거래 후 결측
    # (거래정지·상폐 근사) — 자기 인덱스만 남기려고 dropna로 짧게 만든다.
    b_vals = [2.0] * 5 + [np.nan] * (n - 5)
    rs = {
        "A": pd.Series(1.0, index=cal),
        "B": pd.Series(b_vals, index=cal).dropna(),
    }
    markets = {"A": Market.KOSPI, "B": Market.KOSPI}
    table = build_rs_rank_table(rs, cal, markets, "all")

    # 마지막 실거래(인덱스4) + STALE_LIMIT_SESSIONS 세션 이내 → 여전히 전방채움
    # 값이 살아 있어 2종목 population(B가 위 → 1.0, A → 0.5).
    within = 4 + STALE_LIMIT_SESSIONS
    assert table["B"].iloc[within] == pytest.approx(1.0)
    assert table["A"].iloc[within] == pytest.approx(0.5)

    # 한도 초과 → B는 NaN(탈락), A만 모집단에 남아 단독 100%.
    beyond = within + 2
    assert pd.isna(table["B"].iloc[beyond])
    assert table["A"].iloc[beyond] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# scope="market" — 시장별 독립 랭크
# --------------------------------------------------------------------------- #
def test_market_scope_ranks_within_groups_independently() -> None:
    cal = _cal(3)
    # KOSPI: A=1, B=3(2종목) / KOSDAQ: C=2, D=4(2종목) — "all"이면 순위가 섞이지만
    # market이면 시장 안에서만 비교돼 각 그룹 최댓값이 1.0이 된다.
    rs = {
        "A": pd.Series(1.0, index=cal),
        "B": pd.Series(3.0, index=cal),
        "C": pd.Series(2.0, index=cal),
        "D": pd.Series(4.0, index=cal),
    }
    markets = {
        "A": Market.KOSPI, "B": Market.KOSPI,
        "C": Market.KOSDAQ, "D": Market.KOSDAQ,
    }
    table = build_rs_rank_table(rs, cal, markets, "market")
    row = table.loc[cal[0]]
    assert row["A"] == pytest.approx(0.5)
    assert row["B"] == pytest.approx(1.0)
    assert row["C"] == pytest.approx(0.5)
    assert row["D"] == pytest.approx(1.0)
    # 재조합 로직이 컬럼을 누락·재정렬하지 않는지 확인(다운스트림은 심볼명으로
    # 조회하므로 순서 자체보다 완전성이 중요).
    assert list(table.columns) == ["A", "B", "C", "D"]


def test_unsupported_scope_raises() -> None:
    cal = _cal(2)
    rs = {"A": pd.Series(1.0, index=cal)}
    markets = {"A": Market.KOSPI}
    with pytest.raises(ValueError):
        build_rs_rank_table(rs, cal, markets, "bogus")
