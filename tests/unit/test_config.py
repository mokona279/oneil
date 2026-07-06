"""Config 로드·검증 (Phase 0 DoD: config 파싱·버전 태그)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from oneil_bt.domain.config import Config, ConfigError
from oneil_bt.domain.enums import FillModelType, StopMethod

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


def test_version_tag(cfg: Config) -> None:
    assert cfg.rulebook_version == "v3-3"
    assert cfg.calendar_source == "index"


def test_trend_section(cfg: Config) -> None:
    assert cfg.trend.above_ma == (150, 200)
    assert cfg.trend.low_52w_gain_min_pct == 25.0
    assert cfg.trend.high_52w_within_pct == 15.0
    assert cfg.trend.turnover_20d_min_krw == 1.0e10


def test_base_tiers_sorted_and_typed(cfg: Config) -> None:
    tiers = cfg.base.depth_tiers
    assert [t.max_depth_pct for t in tiers] == [15.0, 33.0]
    assert [t.min_weeks for t in tiers] == [5, 7]
    assert cfg.base.stage.max_stage == 3


def test_entry_and_stop(cfg: Config) -> None:
    assert cfg.entry.tranche_ratios == (0.5, 0.3, 0.2)
    assert cfg.entry.pyramid_triggers_pct == (2.5, 5.0)
    assert cfg.stop.method is StopMethod.ATR2X
    assert cfg.stop.fill_model is FillModelType.CLOSE_CONFIRMED_NEXT_OPEN
    assert cfg.stop.max_stop_pct == 10.0


def test_nullable_fields(cfg: Config) -> None:
    assert cfg.overheating.swing_min_count is None
    assert cfg.sizing.min_weight_pct is None


def test_fill_derived(cfg: Config) -> None:
    fill = cfg.fill
    assert fill.breakout_use_intraday is True
    assert fill.chase_limit_pct == 5.0
    assert fill.stop_fill_model is FillModelType.CLOSE_CONFIRMED_NEXT_OPEN


def test_cost_schedule(cfg: Config) -> None:
    sched = cfg.cost.sell_tax_schedule
    assert sched[0].from_date == date(2000, 1, 1)
    assert sched[0].kospi_bp == 30.0
    # 시행일 오름차순 정렬 보장
    assert list(sched) == sorted(sched, key=lambda t: t.from_date)


def test_missing_version_raises(tmp_path: Path) -> None:
    bad = tmp_path / "rules.yaml"
    bad.write_text("calendar_source: index\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(bad, COSTS)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(tmp_path / "nope.yaml", COSTS)


def test_unsorted_depth_tiers_raises(tmp_path: Path) -> None:
    import yaml

    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    data["base"]["depth_tiers"] = [
        {"max_depth_pct": 33, "min_weeks": 7},
        {"max_depth_pct": 15, "min_weeks": 5},
    ]
    bad = tmp_path / "rules.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(bad, COSTS)


def test_config_is_frozen(cfg: Config) -> None:
    with pytest.raises(Exception):
        cfg.rulebook_version = "x"  # type: ignore[misc]
