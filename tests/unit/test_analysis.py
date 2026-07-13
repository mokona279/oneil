"""분석 하니스 — 오버라이드·그리드·표 직렬화 (계획서 §11 후속과제).

엔진을 돌리지 않는 순수 단위: config 점 경로 오버라이드의 정확성·불변성·오류, 그리드
조합의 순서·개수, 표 헤더 스키마. 엔진을 함께 도는 end-to-end 스윕은 통합 테스트
(`tests/integration/test_sweep.py`)에서 다룬다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oneil_bt.analysis import (
    OverrideError,
    ParameterGrid,
    SweepResult,
    SweepRow,
    apply_overrides,
    sweep_table,
)
from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import FillModelType, StopMethod
from oneil_bt.reporting.metrics import PerformanceMetrics

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


# --------------------------------------------------------------------------- #
# 오버라이드 — 정확성·불변성
# --------------------------------------------------------------------------- #
def test_override_sets_nested_field(cfg: Config) -> None:
    changed = apply_overrides(cfg, {"sizing.max_weight_pct": 5.0})
    assert changed.sizing.max_weight_pct == 5.0
    # 같은 섹션의 다른 필드는 보존.
    assert changed.sizing.risk_per_trade_pct == cfg.sizing.risk_per_trade_pct


def test_override_leaves_original_unchanged(cfg: Config) -> None:
    original = cfg.sizing.max_weight_pct
    apply_overrides(cfg, {"sizing.max_weight_pct": 1.23})
    assert cfg.sizing.max_weight_pct == original  # 원본 불변


def test_override_multiple_axes(cfg: Config) -> None:
    changed = apply_overrides(
        cfg, {"sizing.max_weight_pct": 7.0, "stop.atr_mult": 3.0}
    )
    assert changed.sizing.max_weight_pct == 7.0
    assert changed.stop.atr_mult == 3.0


def test_override_top_level_field(cfg: Config) -> None:
    changed = apply_overrides(cfg, {"portfolio.max_positions": 4})
    assert changed.portfolio.max_positions == 4


# --------------------------------------------------------------------------- #
# 오버라이드 — 타입 보정
# --------------------------------------------------------------------------- #
def test_override_coerces_int_to_float(cfg: Config) -> None:
    changed = apply_overrides(cfg, {"sizing.max_weight_pct": 5})  # int 입력
    assert isinstance(changed.sizing.max_weight_pct, float)
    assert changed.sizing.max_weight_pct == 5.0


def test_override_coerces_enum_from_string(cfg: Config) -> None:
    changed = apply_overrides(cfg, {"stop.method": "fixed_pct"})
    assert changed.stop.method is StopMethod.FIXED_PCT
    assert isinstance(changed.stop.method, StopMethod)


def test_override_coerces_fill_model_enum(cfg: Config) -> None:
    changed = apply_overrides(cfg, {"stop.fill_model": "intraday_touch"})
    assert changed.stop.fill_model is FillModelType.INTRADAY_TOUCH


def test_override_coerces_bool(cfg: Config) -> None:
    changed = apply_overrides(cfg, {"risk_governor.enabled": 0})
    assert changed.risk_governor.enabled is False


def test_override_coerces_list_to_tuple(cfg: Config) -> None:
    changed = apply_overrides(cfg, {"trend.above_ma": [100, 200]})
    assert changed.trend.above_ma == (100, 200)
    assert isinstance(changed.trend.above_ma, tuple)


# --------------------------------------------------------------------------- #
# 오버라이드 — 오류 (오타는 조용한 no-op이 아니라 즉시 실패)
# --------------------------------------------------------------------------- #
def test_override_unknown_field_raises(cfg: Config) -> None:
    with pytest.raises(OverrideError, match="max_weight_pctt|없는 config 필드"):
        apply_overrides(cfg, {"sizing.max_weight_pctt": 5.0})


def test_override_unknown_section_raises(cfg: Config) -> None:
    with pytest.raises(OverrideError):
        apply_overrides(cfg, {"nope.field": 1})


def test_override_descend_into_scalar_raises(cfg: Config) -> None:
    # max_weight_pct 는 float(스칼라)라 더 내려갈 수 없다.
    with pytest.raises(OverrideError, match="dataclass"):
        apply_overrides(cfg, {"sizing.max_weight_pct.deeper": 5.0})


def test_override_empty_path_raises(cfg: Config) -> None:
    with pytest.raises(OverrideError):
        apply_overrides(cfg, {"": 5.0})


# --------------------------------------------------------------------------- #
# ParameterGrid — 조합 순서·개수
# --------------------------------------------------------------------------- #
def test_grid_combination_count_and_order() -> None:
    grid = ParameterGrid.from_mapping(
        {"a": [1, 2, 3], "b": [10, 20]}
    )
    combos = grid.combinations()
    assert len(grid) == 6
    assert len(combos) == 6
    # 첫 축이 바깥 루프: a가 천천히, b가 빨리 변한다(itertools.product 순서).
    assert combos[0] == {"a": 1, "b": 10}
    assert combos[1] == {"a": 1, "b": 20}
    assert combos[2] == {"a": 2, "b": 10}
    assert grid.names == ("a", "b")


def test_grid_single_axis() -> None:
    grid = ParameterGrid.from_mapping({"x": [1, 2, 3, 4]})
    assert len(grid) == 4
    assert [c["x"] for c in grid.combinations()] == [1, 2, 3, 4]


def test_grid_empty_axis_rejected() -> None:
    with pytest.raises(ValueError, match="값이 없다"):
        ParameterGrid.from_mapping({"a": []})


def test_grid_no_axes_yields_single_empty_combo() -> None:
    grid = ParameterGrid(axes=())
    assert grid.combinations() == [{}]
    assert len(grid) == 1


# --------------------------------------------------------------------------- #
# 표 직렬화 — 헤더 스키마
# --------------------------------------------------------------------------- #
def _row(total_return: float = 12.5, **overrides: object) -> SweepRow:
    metrics = PerformanceMetrics(
        total_return_pct=total_return, cagr_pct=8.0, mdd_pct=15.0, win_rate_pct=60.0,
        payoff_ratio=2.0, expectancy_r=0.4, avg_hold_days=30.0,
        avg_exposure_pct=45.0, total_cost=1234.0, n_trades=10, n_wins=6,
        n_losses=4, exit_breakdown={"stop": 3, "trend_60ma": 1, "market_defense": 0},
    )
    return SweepRow(
        overrides=dict(overrides), metrics=metrics,
        final_equity=1.125e8, max_exposure_pct=80.0,
    )


def test_sweep_table_header_and_axis_columns() -> None:
    result = SweepResult(
        axes=("sizing.max_weight_pct", "stop.atr_mult"),
        rows=(_row(**{"sizing.max_weight_pct": 5.0, "stop.atr_mult": 2.0}),),
    )
    header, rows = sweep_table(result)
    # 축 열이 먼저, 그 뒤 지표 열.
    assert header[:2] == ["sizing.max_weight_pct", "stop.atr_mult"]
    assert "total_return_pct" in header
    assert "n_stop" in header and "final_equity" in header
    assert len(rows) == 1
    assert rows[0][:2] == [5.0, 2.0]  # 축 값
    # n_stop 은 exit_breakdown['stop'] 에서.
    assert rows[0][header.index("n_stop")] == 3


def test_sweep_table_renders_enum_axis_value() -> None:
    result = SweepResult(
        axes=("stop.method",),
        rows=(_row(**{"stop.method": StopMethod.FIXED_PCT}),),
    )
    _, rows = sweep_table(result)
    assert rows[0][0] == "fixed_pct"  # Enum → .value


def test_sweep_result_ranked_orders_by_metric() -> None:
    lo = _row(total_return=5.0, a=1)
    hi = _row(total_return=99.0, a=2)
    result = SweepResult(axes=("a",), rows=(lo, hi))
    ranked = result.ranked("total_return_pct", reverse=True)
    assert ranked[0].overrides["a"] == 2  # 99.0 이 먼저
    assert ranked[1].overrides["a"] == 1


# --------------------------------------------------------------------------- #
# CLI 스칼라 파싱 — null/none은 옵셔널 키의 '끔' 상태(P1: contraction_atr_mult 등)
# --------------------------------------------------------------------------- #
def test_cli_parse_scalar_null_and_types() -> None:
    from oneil_bt.cli.run_sweep import _parse_scalar, parse_param_specs

    assert _parse_scalar("null") is None
    assert _parse_scalar("None") is None
    assert _parse_scalar("4") == 4
    assert _parse_scalar("4.5") == 4.5
    assert _parse_scalar("true") is True
    axes = parse_param_specs(["quality.contraction_atr_mult=null,4,5,6"])
    assert axes == {"quality.contraction_atr_mult": [None, 4, 5, 6]}
