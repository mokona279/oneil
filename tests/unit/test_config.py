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
    assert cfg.rulebook_version == "v3-6"  # v3-5 + P4(R4b 재진입) 승인분
    assert cfg.calendar_source == "index"


def test_trend_section(cfg: Config) -> None:
    assert cfg.trend.above_ma == (150, 200)
    assert cfg.trend.low_52w_gain_min_pct == 25.0
    assert cfg.trend.high_52w_within_pct == 25.0  # R2b(Q4) P1 승인
    assert cfg.trend.ma200_rising_lookback_alt == 5  # R2a(Q3b) P1 승인
    assert cfg.trend.turnover_20d_min_krw == 1.0e10


def test_quality_section_p1(cfg: Config) -> None:
    assert cfg.quality.contraction_atr_mult == 5.0  # R1(Q1b) k=5 P1 승인


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


def test_p1_keys_omitted_default_to_none() -> None:
    # P1 신규 키는 옵셔널 — YAML에서 생략하면 None(=v3-3 현행 동치)으로 파싱된다.
    from oneil_bt.domain.config import QualityCfg, TrendCfg

    trend = TrendCfg.from_dict(dict(
        above_ma=[150, 200], ma150_gt_ma200=True, ma200_rising_lookback=20,
        ma50_gt_ma150=True, low_52w_gain_min_pct=25, high_52w_within_pct=15,
        turnover_20d_min_krw=1.0e10,
    ))
    assert trend.ma200_rising_lookback_alt is None
    quality = QualityCfg.from_dict(dict(
        atr_le_pivot_pct=10, contraction_lookback=10,
        contraction_le_pivot_pct=10, dryup_lookback=10,
    ))
    assert quality.contraction_atr_mult is None


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


# --------------------------------------------------------------------------- #
# P2 신규 키 — R3(단계 규칙)·Q11(손절 클램프). P2 승인 반영(2026-07-14).
# --------------------------------------------------------------------------- #
def test_p2_keys_defaults(cfg: Config) -> None:
    st = cfg.base.stage
    assert st.overlimit_weight_factor is None       # R3a 실측 기각 = 4단계+ 금지 유지
    assert st.reset_no_breakout_months == 12        # R3b 승인(N=12)
    assert st.reset_min_depth_pct == 20.0
    assert cfg.stop.no_lower_recalc is True         # Q11 승인(하향 금지)


def test_p2_keys_omitted_default_off(tmp_path: Path) -> None:
    # 키 자체가 없는 구(舊) YAML도 로드된다(하위호환) — 전부 끔으로.
    import yaml

    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    for key in ("overlimit_weight_factor", "reset_no_breakout_months",
                "reset_min_depth_pct"):
        data["base"]["stage"].pop(key, None)
    data["stop"].pop("no_lower_recalc", None)
    old = tmp_path / "rules.yaml"
    old.write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = Config.load(old, COSTS)
    assert cfg.base.stage.overlimit_weight_factor is None
    assert cfg.base.stage.reset_no_breakout_months is None
    assert cfg.base.stage.reset_min_depth_pct is None
    assert cfg.stop.no_lower_recalc is False


def test_reset_months_requires_depth(tmp_path: Path) -> None:
    # 리셋을 켜는데 깊이 임계가 YAML에 없으면 명시적 실패(조용한 기본값 금지).
    import yaml

    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    data["base"]["stage"]["reset_no_breakout_months"] = 12
    data["base"]["stage"].pop("reset_min_depth_pct", None)
    bad = tmp_path / "rules.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(bad, COSTS)


# --------------------------------------------------------------------------- #
# P3 신규 키 — R4a(핸들 피벗). 기본 null = 현행(절대 고점 피벗) 비트 동치.
# --------------------------------------------------------------------------- #
def test_p3_handle_defaults_off(cfg: Config) -> None:
    assert cfg.base.handle.min_sessions is None      # 캘리브레이션 중(승인 대기)
    assert cfg.base.handle.max_depth_pct is None


def test_p3_handle_keys_parse(tmp_path: Path) -> None:
    import yaml

    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    data["base"]["handle"] = {"min_sessions": 5, "max_depth_pct": 12}
    good = tmp_path / "rules.yaml"
    good.write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = Config.load(good, COSTS)
    assert cfg.base.handle.min_sessions == 5
    assert cfg.base.handle.max_depth_pct == 12.0


def test_p3_handle_section_omitted_default_off(tmp_path: Path) -> None:
    # handle 섹션 자체가 없는 구(舊) YAML도 로드된다(하위호환) — 끔으로.
    import yaml

    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    data["base"].pop("handle", None)
    old = tmp_path / "rules.yaml"
    old.write_text(yaml.safe_dump(data), encoding="utf-8")
    cfg = Config.load(old, COSTS)
    assert cfg.base.handle.min_sessions is None
    assert cfg.base.handle.max_depth_pct is None


def test_p3_handle_requires_depth_and_positive(tmp_path: Path) -> None:
    # 켜는데 깊이 상한이 없거나 min_sessions < 1이면 명시적 실패.
    import yaml

    for patch in ({"min_sessions": 5}, {"min_sessions": 0, "max_depth_pct": 12}):
        data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
        data["base"]["handle"] = patch
        bad = tmp_path / "rules.yaml"
        bad.write_text(yaml.safe_dump(data), encoding="utf-8")
        with pytest.raises(ConfigError):
            Config.load(bad, COSTS)
