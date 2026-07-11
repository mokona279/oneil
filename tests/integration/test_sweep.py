"""파라미터 민감도 스윕 end-to-end (계획서 §11 후속과제, Phase 8 통합).

`data_example` 소형 실데이터로 그리드를 돌려, 스윕 하니스가 엔진과 올바로 배선됐는지
확인한다: 조합 수·결정론·파라미터 민감도(값 변경이 결과를 바꿈)·CSV 스키마.
"""

from __future__ import annotations

from oneil_bt.analysis import (
    ParameterGrid,
    run_sweep,
    sweep_table,
    write_sweep_csv,
)
from oneil_bt.data.csv_source import CsvDataSource
from oneil_bt.domain.config import Config

from .conftest import END, START

INITIAL_CASH = 1.0e8


def _sweep(source, cfg, grid):
    return run_sweep(source, cfg, grid, START, END, initial_cash=INITIAL_CASH)


def test_sweep_row_count_matches_grid(source: CsvDataSource, cfg: Config) -> None:
    grid = ParameterGrid.from_mapping(
        {"sizing.max_weight_pct": [5.0, 10.0, 20.0], "stop.atr_mult": [1.5, 2.5]}
    )
    result = _sweep(source, cfg, grid)
    assert len(result.rows) == len(grid) == 6
    assert result.axes == ("sizing.max_weight_pct", "stop.atr_mult")
    # 각 행의 오버라이드에 두 축이 모두 담긴다.
    for row in result.rows:
        assert set(row.overrides) == {"sizing.max_weight_pct", "stop.atr_mult"}


def test_sweep_is_deterministic(source: CsvDataSource, cfg: Config) -> None:
    grid = ParameterGrid.from_mapping({"sizing.max_weight_pct": [5.0, 20.0]})
    r1 = _sweep(source, cfg, grid)
    r2 = _sweep(source, cfg, grid)
    assert [r.final_equity for r in r1.rows] == [r.final_equity for r in r2.rows]
    assert [r.metrics.total_return_pct for r in r1.rows] == [
        r.metrics.total_return_pct for r in r2.rows
    ]


def test_sweep_reflects_parameter_change(source: CsvDataSource, cfg: Config) -> None:
    # 비중 상한을 조이면(5% vs 20%) 최대 노출도가 낮아진다 — 파라미터가 실제 배선됨.
    grid = ParameterGrid.from_mapping({"sizing.max_weight_pct": [5.0, 20.0]})
    result = _sweep(source, cfg, grid)
    by_weight = {row.overrides["sizing.max_weight_pct"]: row for row in result.rows}
    assert by_weight[5.0].max_exposure_pct < by_weight[20.0].max_exposure_pct


def test_sweep_baseline_matches_current_config(source: CsvDataSource, cfg: Config) -> None:
    # 축 값이 현재 config와 같은 조합은 기본 백테스트와 동일해야 한다(오버라이드 무해성).
    from oneil_bt.engine.engine import BacktestEngine

    base = BacktestEngine(source, cfg, initial_cash=INITIAL_CASH).run(START, END)
    grid = ParameterGrid.from_mapping(
        {"sizing.max_weight_pct": [cfg.sizing.max_weight_pct]}
    )
    (row,) = _sweep(source, cfg, grid).rows
    assert row.final_equity == base.final_equity


def test_sweep_csv_schema(source: CsvDataSource, cfg: Config, tmp_path) -> None:
    grid = ParameterGrid.from_mapping({"sizing.max_weight_pct": [5.0, 20.0]})
    result = _sweep(source, cfg, grid)

    header, rows = sweep_table(result)
    assert header[0] == "sizing.max_weight_pct"
    for col in ("total_return_pct", "mdd_pct", "n_trades", "n_stop", "final_equity"):
        assert col in header
    assert len(rows) == 2

    out = tmp_path / "sweep.csv"
    write_sweep_csv(result, out)
    text = out.read_text(encoding="utf-8-sig")
    lines = text.strip().split("\n")
    assert lines[0].split(",")[0] == "sizing.max_weight_pct"
    assert len(lines) == 1 + 2  # 헤더 + 조합 2행
