"""통합 스모크 — 소형 실데이터 1회 완주 + 수치 sanity (계획서 §8 Phase 8, §10).

`data_example/`를 CsvDataSource로 로드해 엔진을 end-to-end로 돌리고, 예외 없이 완주하며
산출물(자본곡선·트레이드·이벤트·리포트)이 회계·스키마 불변식을 만족하는지 확인한다.
정확한 수치는 골든 테스트(test_golden)가, 여기서는 "말이 되는가"만 본다.
"""

from __future__ import annotations

import json

import pytest

from oneil_bt.data.csv_source import CsvDataSource
from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import Market, MarketState
from oneil_bt.engine.engine import BacktestEngine
from oneil_bt.reporting import write_report

from .conftest import END, START

INITIAL_CASH = 1.0e8


@pytest.fixture(scope="module")
def result(source: CsvDataSource, cfg: Config):
    return BacktestEngine(source, cfg, initial_cash=INITIAL_CASH).run(START, END)


# --------------------------------------------------------------------------- #
# 완주 + 자본곡선 sanity
# --------------------------------------------------------------------------- #
def test_run_completes_and_curve_spans_range(result) -> None:
    curve = result.equity_curve
    assert curve, "자본곡선이 비어서는 안 된다"
    assert curve[0].date >= START and curve[-1].date <= END
    # 거래일마다 1행, 날짜 오름차순·중복 없음.
    dates = [rec.date for rec in curve]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))


def test_accounting_identity_holds_each_day(result) -> None:
    for rec in result.equity_curve:
        assert rec.equity == pytest.approx(rec.cash + rec.holdings_value)
        assert rec.equity > 0
        assert 0.0 <= rec.exposure_pct <= 100.0 + 1e-9
        assert 0 <= rec.n_positions
        for st in rec.market_states.values():
            assert st in MarketState


def test_dataset_exercises_entry_and_exit(result) -> None:
    # 설계상 3종목 모두 돌파 진입, 그중 하나(000660)는 급락 청산되어야 한다.
    events = {e.event for e in result.events}
    assert "ENTRY" in events
    assert "PYRAMID" in events
    assert result.trades, "청산이 최소 1건 발생해야 한다"
    assert any(t.closed.is_stop for t in result.trades), "손절이 최소 1건"


def test_final_equity_sane(result) -> None:
    # 승자 2종목이 보유·상승 중이라 자본은 초기 이상이어야 한다(대략적 sanity).
    assert result.final_equity > 0
    assert result.final_equity == pytest.approx(result.equity_curve[-1].equity)


# --------------------------------------------------------------------------- #
# 리포트 산출물 스키마
# --------------------------------------------------------------------------- #
def test_write_report_produces_all_outputs(result, tmp_path) -> None:
    report = write_report(result, tmp_path)
    for key in ("trades", "equity_curve", "events", "metrics_txt", "metrics_json"):
        assert report.paths[key].exists()

    # metrics.json 이 파싱되고 기대 키를 갖는다.
    payload = json.loads(report.paths["metrics_json"].read_text(encoding="utf-8"))
    for key in ("total_return_pct", "cagr_pct", "mdd_pct", "win_rate_pct", "n_trades"):
        assert key in payload
    assert payload["n_trades"] == len(result.trades)

    # trades.csv 행 수(헤더 제외) == 트레이드 수.
    lines = report.paths["trades"].read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) - 1 == len(result.trades)

    # equity_curve.csv 행 수 == 세션 수.
    eq_lines = report.paths["equity_curve"].read_text(encoding="utf-8-sig").splitlines()
    assert len(eq_lines) - 1 == len(result.equity_curve)


# --------------------------------------------------------------------------- #
# 단일종목 모드도 완주
# --------------------------------------------------------------------------- #
def test_single_symbol_mode_runs(source: CsvDataSource, cfg: Config) -> None:
    res = BacktestEngine(source, cfg, initial_cash=INITIAL_CASH).run(
        START, END, symbols=["005930"]
    )
    assert len(res.equity_curve) == len(  # 포트폴리오 모드와 동일 세션 수
        BacktestEngine(source, cfg, initial_cash=INITIAL_CASH).run(START, END).equity_curve
    )
    assert all(e.symbol == "005930" for e in res.events)
