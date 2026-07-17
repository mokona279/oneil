"""P6 스트레스 분석기(scripts/market_filter_stress.py) 단위 테스트.

합성 시리즈로 ① 상태 배선(MA min_periods·복귀 히스테리시스) ② 에피소드 추출
(피크·차단 시점 낙폭·방어 랙·휩쏘·재진입 랙, 열린 에피소드 NaN) ③ 연도 요약
(점유율 합 100·D-1 진입허용 규약)을 검증한다. 실데이터 end-to-end는 CLI로 별도
수행(레포 규칙상 out/ 아래는 테스트에서 쓰지 않는다).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import market_filter_stress as mfs  # noqa: E402

from oneil_bt.domain.enums import MarketState  # noqa: E402

N, C, D = MarketState.NORMAL, MarketState.CAUTION, MarketState.DEFENSE


def _series(values: list[float]) -> pd.Series:
    idx = pd.bdate_range("2020-01-01", periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


# --------------------------------------------------------------------------- #
# compute_states — MA 배선·복귀 히스테리시스
# --------------------------------------------------------------------------- #
def test_compute_states_small_windows() -> None:
    close = _series([10, 10, 10, 12, 5, 5, 12, 13, 14])
    states = mfs.compute_states(close, entry_window=2, defense_window=3,
                                recover_days=2)
    assert list(states) == [D, D, C, N, D, D, C, N, N]


# --------------------------------------------------------------------------- #
# extract_episodes — 3에피소드 합성(휩쏘·방어·열린 에피소드)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def synthetic() -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    close = _series([100, 110, 105, 95, 98, 108, 112, 90, 80, 85,
                     95, 100, 102, 104, 106, 101, 97, 96, 99, 98])
    states = pd.Series([N, N, N, C, C, N, N, D, D, C,
                        N, N, N, N, N, C, C, C, C, C],
                       index=close.index, dtype=object)
    return close, states, mfs.extract_episodes(close, states)


def test_episode_count_and_kinds(synthetic) -> None:
    _, _, ep = synthetic
    assert len(ep) == 3
    assert list(ep["kind"]) == ["caution", "defense", "caution"]


def test_whipsaw_episode_metrics(synthetic) -> None:
    close, _, ep = synthetic
    row = ep.iloc[0]  # s=3: 직전 런 0..2, 피크 110(pos1)
    assert row["peak"] == 110
    assert row["dd_at_block"] == pytest.approx(95 / 110 - 1)
    assert row["blocked_sessions"] == 2
    assert row["whipsaw_cost"] == pytest.approx(108 / 95 - 1)  # 더 비싸게 재진입
    assert row["dd_max"] == pytest.approx(95 / 110 - 1)
    assert row["bottom_to_reentry_sessions"] == 2
    assert row["missed_from_bottom"] == pytest.approx(108 / 95 - 1)
    assert pd.isna(row["defense_date"]) and pd.isna(row["lag_peak_to_defense"])


def test_defense_episode_metrics(synthetic) -> None:
    close, _, ep = synthetic
    row = ep.iloc[1]  # s=7: 직전 런 5..6, 피크 112(pos6), 즉시 DEFENSE
    assert row["peak"] == 112
    assert row["kind"] == "defense"
    assert row["lag_peak_to_defense"] == 1
    assert row["dd_at_defense"] == pytest.approx(90 / 112 - 1)
    assert row["dd_at_defense_vs252"] == pytest.approx(90 / 112 - 1)
    assert row["dd_max"] == pytest.approx(80 / 112 - 1)
    assert row["blocked_sessions"] == 3
    assert row["bottom_to_reentry_sessions"] == 2
    assert row["missed_from_bottom"] == pytest.approx(95 / 80 - 1)


def test_open_episode_has_nan_reentry(synthetic) -> None:
    _, _, ep = synthetic
    row = ep.iloc[2]  # s=15: 시리즈 끝까지 미복귀
    assert row["peak"] == 106
    assert pd.isna(row["reentry_date"]) and pd.isna(row["blocked_sessions"])
    assert pd.isna(row["whipsaw_cost"])
    assert row["dd_max"] == pytest.approx(96 / 106 - 1)


def test_warmup_block_not_counted() -> None:
    # 첫 NORMAL 이전(워밍업 DEFENSE)은 에피소드가 아니다.
    close = _series([100, 100, 100, 110, 105])
    states = pd.Series([D, D, N, N, C], index=close.index, dtype=object)
    ep = mfs.extract_episodes(close, states)
    assert len(ep) == 1 and ep.iloc[0]["block_date"] == close.index[4]


# --------------------------------------------------------------------------- #
# yearly_summary
# --------------------------------------------------------------------------- #
def test_yearly_summary_shares_and_allowed(synthetic) -> None:
    close, states, ep = synthetic
    y = mfs.yearly_summary(close, states, ep)
    assert len(y) == 1 and y.iloc[0]["year"] == 2020
    row = y.iloc[0]
    assert row["sessions"] == 20
    assert row["normal_pct"] + row["caution_pct"] + row["defense_pct"] == \
        pytest.approx(100.0)
    # D-1 규약: 진입허용 세션 = 전일 NORMAL 10개 (pos1..3, 6..7, 11..15)
    allowed = pd.Series(list(states)).shift(1).eq(N).sum()
    assert row["allowed_pct"] == pytest.approx(allowed / 20 * 100)
    assert row["n_blocks"] == 3 and row["n_defense"] == 1
    assert row["index_ret_pct"] == pytest.approx((98 / 100 - 1) * 100)
