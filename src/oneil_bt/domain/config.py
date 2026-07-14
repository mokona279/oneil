"""Config — 모든 규칙 수치의 타입 있는 단일 진실 원천 (계획서 §3.1, §5).

`config/rules_v3-3.yaml` + `config/costs.yaml`을 읽어 불변 DTO 트리로 만든다.
코드 어디에도 규칙 수치를 하드코딩하지 않으며, 모든 소비자는 이 객체를 주입받는다.

fill 모델 관련 설정은 규칙서 원문 구조상 `entry`/`stop` 섹션에 흩어져 있어,
편의를 위해 `Config.fill`(FillCfg)로 파생·통합해 제공한다(중복 저장 아님).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .enums import FillModelType, StopMethod


class ConfigError(Exception):
    """설정 파일 파싱·검증 실패."""


# --------------------------------------------------------------------------- #
# 파싱 헬퍼 — 키 누락 시 조용히 넘어가지 않고 명시적으로 실패한다.
# --------------------------------------------------------------------------- #
def _req(d: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in d:
        raise ConfigError(f"missing required key '{key}' in {ctx}")
    return d[key]


# --------------------------------------------------------------------------- #
# 규칙 섹션 DTO
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TrendCfg:
    above_ma: tuple[int, ...]
    ma150_gt_ma200: bool
    ma200_rising_lookback: int
    ma200_rising_lookback_alt: int | None  # R2a(Q3): 보조 룩백 OR, None이면 현행 단일 룩백
    ma50_gt_ma150: bool
    low_52w_gain_min_pct: float
    high_52w_within_pct: float
    turnover_20d_min_krw: float

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TrendCfg":
        return TrendCfg(
            above_ma=tuple(int(x) for x in _req(d, "above_ma", "trend_template")),
            ma150_gt_ma200=bool(_req(d, "ma150_gt_ma200", "trend_template")),
            ma200_rising_lookback=int(_req(d, "ma200_rising_lookback", "trend_template")),
            ma200_rising_lookback_alt=(None if d.get("ma200_rising_lookback_alt") is None
                                       else int(d["ma200_rising_lookback_alt"])),
            ma50_gt_ma150=bool(_req(d, "ma50_gt_ma150", "trend_template")),
            low_52w_gain_min_pct=float(_req(d, "low_52w_gain_min_pct", "trend_template")),
            high_52w_within_pct=float(_req(d, "high_52w_within_pct", "trend_template")),
            turnover_20d_min_krw=float(_req(d, "turnover_20d_min_krw", "trend_template")),
        )


@dataclass(frozen=True)
class OverheatCfg:
    ret_lookback_days: int
    ret_threshold_pct: float
    require_no_base: bool
    limitup_lookback_days: int
    swing_pct: float
    swing_min_count: int | None

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "OverheatCfg":
        return OverheatCfg(
            ret_lookback_days=int(_req(d, "ret_lookback_days", "overheating")),
            ret_threshold_pct=float(_req(d, "ret_threshold_pct", "overheating")),
            require_no_base=bool(_req(d, "require_no_base", "overheating")),
            limitup_lookback_days=int(_req(d, "limitup_lookback_days", "overheating")),
            swing_pct=float(_req(d, "swing_pct", "overheating")),
            swing_min_count=(None if d.get("swing_min_count") is None
                             else int(d["swing_min_count"])),
        )


@dataclass(frozen=True)
class RsCfg:
    lookback_days: int
    method: str

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "RsCfg":
        return RsCfg(
            lookback_days=int(_req(d, "lookback_days", "rs")),
            method=str(_req(d, "method", "rs")),
        )


@dataclass(frozen=True)
class DepthTier:
    max_depth_pct: float
    min_weeks: int


@dataclass(frozen=True)
class StageCfg:
    step_up_close_gain_pct: float
    max_stage: int
    # R3a(Q5a): max_stage 초과 베이스의 감액 진입 — 목표 비중에 곱할 계수(예: 0.5).
    # None이면 현행(4단계+ 진입 금지).
    overlimit_weight_factor: float | None
    # R3b(Q5b): 마지막 유효 돌파 후 N개월 무돌파 + 깊이 임계 이상 새 베이스 → 단계 1 리셋.
    # months가 None이면 현행(리셋은 직전 베이스 저점 하회뿐).
    reset_no_breakout_months: int | None
    reset_min_depth_pct: float | None


@dataclass(frozen=True)
class HandleCfg:
    # R4a(Q10): 컵위드핸들 손잡이 피벗 — 최종 저점 이후 회복 랠리 정점(절대 고점 미만)에서
    # min_sessions 거래일 이상 이어진 얕은 눌림(깊이 ≤ max_depth_pct%, 상반부)이면 그 정점을
    # 진입 피벗으로 쓴다. min_sessions가 None이면 꺼짐(현행 절대 고점 피벗과 비트 동치).
    min_sessions: int | None
    max_depth_pct: float | None


@dataclass(frozen=True)
class BaseCfg:
    depth_tiers: tuple[DepthTier, ...]
    invalid_depth_pct: float
    min_days_per_week: int
    stage: StageCfg
    handle: HandleCfg

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "BaseCfg":
        tiers = tuple(
            DepthTier(float(t["max_depth_pct"]), int(t["min_weeks"]))
            for t in _req(d, "depth_tiers", "base")
        )
        # 티어는 깊이 오름차순이어야 판별 로직(작은 깊이부터 매칭)이 결정론적이다.
        if list(tiers) != sorted(tiers, key=lambda t: t.max_depth_pct):
            raise ConfigError("base.depth_tiers must be sorted by max_depth_pct ascending")
        st = _req(d, "stage", "base")
        reset_months = (None if st.get("reset_no_breakout_months") is None
                        else int(st["reset_no_breakout_months"]))
        reset_depth = (None if st.get("reset_min_depth_pct") is None
                       else float(st["reset_min_depth_pct"]))
        # 리셋을 켜려면 깊이 임계도 YAML에 있어야 한다(코드 기본값 하드코딩 금지).
        if reset_months is not None and reset_depth is None:
            raise ConfigError(
                "base.stage.reset_min_depth_pct is required when "
                "reset_no_breakout_months is set"
            )
        hd = d.get("handle") or {}
        handle_min = (None if hd.get("min_sessions") is None
                      else int(hd["min_sessions"]))
        handle_depth = (None if hd.get("max_depth_pct") is None
                        else float(hd["max_depth_pct"]))
        if handle_min is not None:
            if handle_min < 1:
                raise ConfigError("base.handle.min_sessions must be >= 1")
            # 핸들을 켜려면 눌림 깊이 상한도 YAML에 있어야 한다(코드 기본값 하드코딩 금지).
            if handle_depth is None:
                raise ConfigError(
                    "base.handle.max_depth_pct is required when min_sessions is set"
                )
        return BaseCfg(
            depth_tiers=tiers,
            invalid_depth_pct=float(_req(d, "invalid_depth_pct", "base")),
            min_days_per_week=int(_req(d, "min_days_per_week", "base")),
            stage=StageCfg(
                step_up_close_gain_pct=float(_req(st, "step_up_close_gain_pct", "base.stage")),
                max_stage=int(_req(st, "max_stage", "base.stage")),
                overlimit_weight_factor=(None if st.get("overlimit_weight_factor") is None
                                         else float(st["overlimit_weight_factor"])),
                reset_no_breakout_months=reset_months,
                reset_min_depth_pct=reset_depth,
            ),
            handle=HandleCfg(
                min_sessions=handle_min,
                max_depth_pct=handle_depth,
            ),
        )


@dataclass(frozen=True)
class QualityCfg:
    atr_le_pivot_pct: float
    contraction_lookback: int
    contraction_le_pivot_pct: float
    contraction_atr_mult: float | None  # R1(Q1b): max(피벗%, k×ATR) 하이브리드, None이면 현행
    dryup_lookback: int

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "QualityCfg":
        return QualityCfg(
            atr_le_pivot_pct=float(_req(d, "atr_le_pivot_pct", "quality")),
            contraction_lookback=int(_req(d, "contraction_lookback", "quality")),
            contraction_le_pivot_pct=float(_req(d, "contraction_le_pivot_pct", "quality")),
            contraction_atr_mult=(None if d.get("contraction_atr_mult") is None
                                  else float(d["contraction_atr_mult"])),
            dryup_lookback=int(_req(d, "dryup_lookback", "quality")),
        )


@dataclass(frozen=True)
class EntryCfg:
    breakout_use_intraday: bool
    chase_limit_pct: float
    breakout_volume_mult: float
    tranche_ratios: tuple[float, ...]
    pyramid_triggers_pct: tuple[float, ...]
    tranche_price_cap_pct: float

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "EntryCfg":
        ratios = tuple(float(x) for x in _req(d, "tranche_ratios", "entry"))
        triggers = tuple(float(x) for x in _req(d, "pyramid_triggers_pct", "entry"))
        # 트랜치 3개(50/30/20) 대비 피라미딩 트리거 2개(2·3차)여야 한다.
        if len(triggers) != len(ratios) - 1:
            raise ConfigError(
                "entry.pyramid_triggers_pct length must be len(tranche_ratios) - 1"
            )
        return EntryCfg(
            breakout_use_intraday=bool(_req(d, "breakout_use_intraday", "entry")),
            chase_limit_pct=float(_req(d, "chase_limit_pct", "entry")),
            breakout_volume_mult=float(_req(d, "breakout_volume_mult", "entry")),
            tranche_ratios=ratios,
            pyramid_triggers_pct=triggers,
            tranche_price_cap_pct=float(_req(d, "tranche_price_cap_pct", "entry")),
        )


@dataclass(frozen=True)
class StopCfg:
    method: StopMethod
    atr_mult: float
    max_stop_pct: float
    fixed_pct: float
    fill_model: FillModelType
    # Q11(개선계획 §5, 확정 2026-07-12): 피라미딩 재계산 시 손절가 하향 금지 —
    # max(기존, 새 손절가). False면 현행(하향 허용). P2 트레이드오프 승인 시 기본 켬.
    no_lower_recalc: bool

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "StopCfg":
        return StopCfg(
            method=StopMethod(str(_req(d, "method", "stop"))),
            atr_mult=float(_req(d, "atr_mult", "stop")),
            max_stop_pct=float(_req(d, "max_stop_pct", "stop")),
            fixed_pct=float(_req(d, "fixed_pct", "stop")),
            fill_model=FillModelType(str(_req(d, "fill_model", "stop"))),
            no_lower_recalc=bool(d.get("no_lower_recalc", False)),
        )


@dataclass(frozen=True)
class EightWeekCfg:
    fast_gain_pct: float
    fast_window_days: int
    min_hold_days: int


@dataclass(frozen=True)
class ExitCfg:
    ma_trend: int
    trend_break_partial: float
    trend_recover_days: int
    volbreak_full: bool
    volbreak_mult: float
    market_defense_ma: int
    market_defense_reduce: float
    eight_week: EightWeekCfg

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ExitCfg":
        ew = _req(d, "eight_week", "exit")
        return ExitCfg(
            ma_trend=int(_req(d, "ma_trend", "exit")),
            trend_break_partial=float(_req(d, "trend_break_partial", "exit")),
            trend_recover_days=int(_req(d, "trend_recover_days", "exit")),
            volbreak_full=bool(_req(d, "volbreak_full", "exit")),
            volbreak_mult=float(_req(d, "volbreak_mult", "exit")),
            market_defense_ma=int(_req(d, "market_defense_ma", "exit")),
            market_defense_reduce=float(_req(d, "market_defense_reduce", "exit")),
            eight_week=EightWeekCfg(
                fast_gain_pct=float(_req(ew, "fast_gain_pct", "exit.eight_week")),
                fast_window_days=int(_req(ew, "fast_window_days", "exit.eight_week")),
                min_hold_days=int(_req(ew, "min_hold_days", "exit.eight_week")),
            ),
        )


@dataclass(frozen=True)
class SizingCfg:
    risk_per_trade_pct: float
    max_weight_pct: float
    min_weight_pct: float | None
    reserve_pyramid_cash: bool

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "SizingCfg":
        return SizingCfg(
            risk_per_trade_pct=float(_req(d, "risk_per_trade_pct", "sizing")),
            max_weight_pct=float(_req(d, "max_weight_pct", "sizing")),
            min_weight_pct=(None if d.get("min_weight_pct") is None
                            else float(d["min_weight_pct"])),
            reserve_pyramid_cash=bool(_req(d, "reserve_pyramid_cash", "sizing")),
        )


@dataclass(frozen=True)
class PortfolioCfg:
    max_positions: int
    min_positions_soft: int

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PortfolioCfg":
        return PortfolioCfg(
            max_positions=int(_req(d, "max_positions", "portfolio")),
            min_positions_soft=int(_req(d, "min_positions_soft", "portfolio")),
        )


@dataclass(frozen=True)
class MarketFilterCfg:
    entry_ma: int
    defense_ma: int
    defense_max_equity_pct: float
    recover_days: int

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "MarketFilterCfg":
        return MarketFilterCfg(
            entry_ma=int(_req(d, "entry_ma", "market_filter")),
            defense_ma=int(_req(d, "defense_ma", "market_filter")),
            defense_max_equity_pct=float(_req(d, "defense_max_equity_pct", "market_filter")),
            recover_days=int(_req(d, "recover_days", "market_filter")),
        )


@dataclass(frozen=True)
class RiskGovernorCfg:
    enabled: bool
    consecutive_stops: int
    halt_days: int

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "RiskGovernorCfg":
        return RiskGovernorCfg(
            enabled=bool(_req(d, "enabled", "risk_governor")),
            consecutive_stops=int(_req(d, "consecutive_stops", "risk_governor")),
            halt_days=int(_req(d, "halt_days", "risk_governor")),
        )


@dataclass(frozen=True)
class TaxTier:
    from_date: date
    kospi_bp: float
    kosdaq_bp: float


@dataclass(frozen=True)
class CostCfg:
    commission_bp: float
    slippage_bp: float
    sell_tax_schedule: tuple[TaxTier, ...]

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "CostCfg":
        tiers = tuple(
            TaxTier(
                from_date=date.fromisoformat(str(t["from"])),
                kospi_bp=float(t["kospi_bp"]),
                kosdaq_bp=float(t["kosdaq_bp"]),
            )
            for t in _req(d, "sell_tax_schedule", "costs")
        )
        if not tiers:
            raise ConfigError("costs.sell_tax_schedule must not be empty")
        if list(tiers) != sorted(tiers, key=lambda t: t.from_date):
            raise ConfigError("costs.sell_tax_schedule must be sorted by 'from' ascending")
        return CostCfg(
            commission_bp=float(_req(d, "commission_bp", "costs")),
            slippage_bp=float(_req(d, "slippage_bp", "costs")),
            sell_tax_schedule=tiers,
        )


@dataclass(frozen=True)
class FillCfg:
    """체결 모델용 파생 설정 (entry + stop 에서 통합). 계획서 §3.1의 `fill`."""

    breakout_use_intraday: bool
    chase_limit_pct: float
    tranche_price_cap_pct: float
    breakout_volume_mult: float
    stop_fill_model: FillModelType


# --------------------------------------------------------------------------- #
# 최상위 Config
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Config:
    rulebook_version: str
    calendar_source: str
    trend: TrendCfg
    overheating: OverheatCfg
    rs: RsCfg
    base: BaseCfg
    quality: QualityCfg
    entry: EntryCfg
    stop: StopCfg
    exit: ExitCfg
    sizing: SizingCfg
    portfolio: PortfolioCfg
    market_filter: MarketFilterCfg
    risk_governor: RiskGovernorCfg
    cost: CostCfg

    @property
    def fill(self) -> FillCfg:
        """entry/stop 에서 파생된 체결 설정(단일 진실은 여전히 YAML)."""
        return FillCfg(
            breakout_use_intraday=self.entry.breakout_use_intraday,
            chase_limit_pct=self.entry.chase_limit_pct,
            tranche_price_cap_pct=self.entry.tranche_price_cap_pct,
            breakout_volume_mult=self.entry.breakout_volume_mult,
            stop_fill_model=self.stop.fill_model,
        )

    @staticmethod
    def load(rules_yaml: Path | str, costs_yaml: Path | str) -> "Config":
        rules = _read_yaml(Path(rules_yaml))
        costs = _read_yaml(Path(costs_yaml))
        version = rules.get("rulebook_version")
        if not version:
            raise ConfigError("rules yaml missing 'rulebook_version' (재현성 태그 필수)")
        return Config(
            rulebook_version=str(version),
            calendar_source=str(rules.get("calendar_source", "index")),
            trend=TrendCfg.from_dict(_req(rules, "trend_template", "rules")),
            overheating=OverheatCfg.from_dict(_req(rules, "overheating", "rules")),
            rs=RsCfg.from_dict(_req(rules, "rs", "rules")),
            base=BaseCfg.from_dict(_req(rules, "base", "rules")),
            quality=QualityCfg.from_dict(_req(rules, "quality", "rules")),
            entry=EntryCfg.from_dict(_req(rules, "entry", "rules")),
            stop=StopCfg.from_dict(_req(rules, "stop", "rules")),
            exit=ExitCfg.from_dict(_req(rules, "exit", "rules")),
            sizing=SizingCfg.from_dict(_req(rules, "sizing", "rules")),
            portfolio=PortfolioCfg.from_dict(_req(rules, "portfolio", "rules")),
            market_filter=MarketFilterCfg.from_dict(_req(rules, "market_filter", "rules")),
            risk_governor=RiskGovernorCfg.from_dict(_req(rules, "risk_governor", "rules")),
            cost=CostCfg.from_dict(costs),
        )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:  # pragma: no cover - 방어적
        raise ConfigError(f"failed to parse YAML {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a mapping: {path}")
    return data
