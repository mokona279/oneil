"""진단(§11 후속) 통합 테스트 — 진입 퍼널·게이트 분해·현 베이스 단계.

엔진이 채운 진단 필드가 (1) 실제 이벤트/체결과 일치(비드리프트), (2) 퍼널 단조성·
회계 불변식을 만족, (3) record_diagnostics=False면 비고 체결은 그대로, (4) write_report가
CSV로 낸다 — 를 확인한다. `data_example` 소형 데이터를 conftest 픽스처로 공유한다.
"""

from __future__ import annotations

import csv

import pytest

from oneil_bt.data.csv_source import CsvDataSource
from oneil_bt.domain.config import Config
from oneil_bt.engine.engine import BacktestEngine
from oneil_bt.reporting import write_report
from oneil_bt.reporting.diagnostics import (
    BASE_STAGE_HEADER,
    ENTRY_FUNNEL_HEADER,
    GATE_BREAKDOWN_HEADER,
)

from .conftest import END, START

INITIAL_CASH = 1.0e8


@pytest.fixture(scope="module")
def result(source: CsvDataSource, cfg: Config):
    return BacktestEngine(source, cfg, initial_cash=INITIAL_CASH).run(START, END)


# --------------------------------------------------------------------------- #
# 퍼널 단조성 + 이벤트/체결과의 일치
# --------------------------------------------------------------------------- #
def test_funnel_is_monotone_and_covers_universe(result, source) -> None:
    funnel = result.entry_funnel
    assert set(funnel) == set(source.symbols())
    sessions = len(result.equity_curve)
    for f in funnel.values():
        assert f.held + f.shopped <= sessions            # 첫날 prev None·거버너로 ≤
        assert f.base_present <= f.shopped
        assert f.stage_ok <= f.base_present
        assert f.breakout <= f.stage_ok
        for g in (f.gate_trend_ok, f.gate_rs_ok, f.gate_market_ok, f.gate_quality_ok):
            assert g <= f.breakout
        assert f.gates_all_ok <= min(
            f.gate_trend_ok, f.gate_rs_ok, f.gate_market_ok, f.gate_quality_ok
        )
        assert f.entered <= f.gates_all_ok


def test_funnel_totals_match_events(result) -> None:
    n_entry = sum(1 for e in result.events if e.event == "ENTRY")
    n_candidate = sum(1 for e in result.events if e.event == "BREAKOUT_CANDIDATE")
    assert sum(f.entered for f in result.entry_funnel.values()) == n_entry
    assert sum(f.gates_all_ok for f in result.entry_funnel.values()) == n_candidate


def test_gate_breakdown_consistent(result) -> None:
    # 게이트 행 수 == 퍼널의 돌파(기회) 합.
    assert len(result.gate_breakdown) == sum(
        f.breakout for f in result.entry_funnel.values()
    )
    for r in result.gate_breakdown:
        assert r.all_pass == (r.n_failed == 0)
        assert 0 <= r.n_failed <= 7
    # all_pass 행 수 == 후보(=BREAKOUT_CANDIDATE) 수.
    n_pass = sum(1 for r in result.gate_breakdown if r.all_pass)
    assert n_pass == sum(f.gates_all_ok for f in result.entry_funnel.values())


def test_base_stage_snapshot_sane(result, source) -> None:
    stages = result.base_stages
    assert set(stages) == set(source.symbols())
    for s in stages.values():
        assert s.n_breakouts >= 0
        assert s.max_stage_reached >= (s.last_breakout_stage or 0)
        if s.has_base:
            assert s.stage is not None and s.stage >= 1
            assert s.stage <= s.max_stage_reached
            assert s.pivot and s.pivot > 0
        else:
            assert s.stage is None


# --------------------------------------------------------------------------- #
# 비드리프트 — 진단 on/off 가 체결·이벤트를 바꾸지 않는다
# --------------------------------------------------------------------------- #
def test_diagnostics_off_does_not_alter_trading(source, cfg, result) -> None:
    off = BacktestEngine(
        source, cfg, initial_cash=INITIAL_CASH, record_diagnostics=False
    ).run(START, END)
    # 진단 필드는 비어 있다.
    assert off.entry_funnel == {} and off.gate_breakdown == [] and off.base_stages == {}
    # 체결·이벤트·최종자본은 진단 on 결과와 완전히 동일.
    assert [e.event for e in off.events] == [e.event for e in result.events]
    assert len(off.trades) == len(result.trades)
    assert off.final_equity == pytest.approx(result.final_equity)


# --------------------------------------------------------------------------- #
# 리포트 산출물
# --------------------------------------------------------------------------- #
def test_write_report_emits_diagnostic_csvs(result, tmp_path) -> None:
    report = write_report(result, tmp_path)
    for key, header in (
        ("entry_funnel", ENTRY_FUNNEL_HEADER),
        ("gate_breakdown", GATE_BREAKDOWN_HEADER),
        ("base_stage", BASE_STAGE_HEADER),
    ):
        path = report.paths[key]
        assert path.exists()
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        assert tuple(rows[0]) == header
    # entry_funnel 행 수(헤더 제외) == 유니버스 크기.
    with report.paths["entry_funnel"].open(encoding="utf-8-sig", newline="") as f:
        assert len(list(csv.reader(f))) - 1 == len(result.entry_funnel)
