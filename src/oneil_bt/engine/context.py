"""엔진 상태 자료구조·컨텍스트 조립 (계획서 §3.5, Phase 6).

- `SymbolContext`/`MarketContext`: 심볼·시장별로 필요한 규칙 컴포넌트 묶음. 엔진이
  DataSource·Config로부터 한 번 만들어 매일 재사용한다(지표는 사전계산·캐시).
- `TradePlan`: 한 포지션의 경로 의존 진입 계획(피벗·목표비중·트랜치 진행·예약현금·
  1주당 리스크). `Position`(도메인 값객체)이 담지 않는 "다음 트랜치를 어떻게 살까"를
  엔진 측에서 들고 있는 상태다.
- `DailyRecord`/`TradeRecord`/`EventRecord`/`BacktestResult`: 일별 자본곡선·트레이드
  로그·육안검증 이벤트의 원자료. Phase 7 리포팅이 이 구조를 읽어 CSV/지표를 만든다.

컨텍스트 빌더(`build_symbol_context`/`build_market_context`)는 지표 사전계산을 한 곳에
모아 엔진 본체를 얇게 유지한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..domain.bar import PriceFrame
from ..domain.config import Config
from ..domain.enums import EntryReason, Market, MarketState
from ..domain.trade import ClosedTrade
from ..indicators.indicator_set import IndicatorSet
from ..rules.base_detector import BaseDetector
from ..rules.base_quality import BaseQualityCheck
from ..rules.exit_rules import EightWeekGuard, MarketDefenseRule, TrendExitRule
from ..rules.market_filter import MarketFilter
from ..rules.overheating import OverheatingFilter
from ..rules.rs_filter import RsFilter
from ..rules.stop_rule import StopRule
from ..rules.trend_template import TrendTemplateFilter


# --------------------------------------------------------------------------- #
# 시장(지수)·심볼 컨텍스트
# --------------------------------------------------------------------------- #
@dataclass
class MarketContext:
    """시장 하나(코스피/코스닥)의 지수·지표·시장필터."""

    market: Market
    index_prices: PriceFrame
    ind: IndicatorSet
    filter: MarketFilter


@dataclass
class SymbolContext:
    """종목 하나에 필요한 규칙 컴포넌트 묶음(지표는 사전계산 캐시)."""

    symbol: str
    market: Market
    prices: PriceFrame
    ind: IndicatorSet
    trend: TrendTemplateFilter
    overheating: OverheatingFilter
    rs: RsFilter
    detector: BaseDetector
    quality: BaseQualityCheck
    stop: StopRule
    trend_exit: TrendExitRule
    guard: EightWeekGuard
    defense: MarketDefenseRule


def build_market_context(
    market: Market, index_prices: PriceFrame, cfg: Config
) -> MarketContext:
    ind = IndicatorSet(index_prices, index_prices, cfg)
    return MarketContext(
        market=market,
        index_prices=index_prices,
        ind=ind,
        filter=MarketFilter(index_prices, ind, cfg),
    )


def build_symbol_context(
    symbol: str,
    market: Market,
    prices: PriceFrame,
    index_prices: PriceFrame,
    cfg: Config,
) -> SymbolContext:
    ind = IndicatorSet(prices, index_prices, cfg)
    overheating = OverheatingFilter(ind, cfg)
    guard = EightWeekGuard(prices, cfg)
    return SymbolContext(
        symbol=symbol,
        market=market,
        prices=prices,
        ind=ind,
        trend=TrendTemplateFilter(prices, ind, cfg),
        overheating=overheating,
        rs=RsFilter(ind, cfg),
        detector=BaseDetector(prices, ind, cfg),
        quality=BaseQualityCheck(prices, ind, overheating, cfg),
        stop=StopRule(prices, cfg),
        trend_exit=TrendExitRule(prices, ind, cfg),
        guard=guard,
        defense=MarketDefenseRule(guard, cfg),
    )


# --------------------------------------------------------------------------- #
# 진입 계획(경로 의존 상태)
# --------------------------------------------------------------------------- #
@dataclass
class TradePlan:
    """한 포지션의 진입 계획·트랜치 진행 상태(엔진 소유).

    - `first_fill_price`: 1차 체결가. 2·3차 피라미딩 트리거의 기준가.
    - `target_notional`: 목표 비중에 해당하는 전체 명목금액. 각 트랜치 수량은 이 값에
      트랜치 비율을 곱해 산정한다(자본 변동과 무관하게 목표 비중을 지킨다).
    - `next_tranche_idx`: 다음에 채울 트랜치 인덱스(1=2차, 2=3차). ratios 길이 도달 시 완료.
    - `pyramid_allowed`: 돌파일 거래량 게이트(1.5×) 통과 여부. 미통과면 2·3차 없음.
    - `reserved`: 피라미딩용으로 예약해 둔 현금(available_cash에서 빠짐).
    - `risk_per_share`: 1주당 리스크(1차 체결가 − 최초 손절가). ClosedTrade R 배수 분모.
    - `exiting`: 청산(부분 포함)이 시작되면 True — 이후 피라미딩을 멈춘다.
    - `entry_reason`: 트레이드 로그의 진입 사유. R4b 재진입은 REENTRY_50MA로 기록하며,
      이때 `pivot`=트리거 확인일 종가(기준가), `base_stage`=0(베이스 없음),
      `next_tranche_idx`=len(ratios)로 시작해 피라미딩이 없다.
    """

    symbol: str
    market: Market
    pivot: float
    base_stage: int
    weight: float
    target_notional: float
    tranche_ratios: tuple[float, ...]
    first_fill_price: float
    risk_per_share: float
    next_tranche_idx: int = 1
    pyramid_allowed: bool = False
    reserved: float = 0.0
    total_entry_cost: float = 0.0
    total_entry_qty: int = 0
    exiting: bool = False
    entry_reason: EntryReason = EntryReason.BREAKOUT_T1

    @property
    def entry_cost_per_share(self) -> float:
        return self.total_entry_cost / self.total_entry_qty if self.total_entry_qty else 0.0

    @property
    def complete(self) -> bool:
        return self.next_tranche_idx >= len(self.tranche_ratios)


@dataclass
class ReentryTicket:
    """R4b 재진입 자격(심볼당 최신 1개, 엔진 소유) — plan/p4_reentry.md Q6.

    §6② 사유(TREND_60MA_REST/VOLBREAK) 전량 청산 체결이 부여한다. `streak`는
    청산 체결일부터 실거래 바 세션의 종가 ≥ 50MA 연속 수(하회 시 0 리셋)로,
    confirm_sessions 도달이 트리거 확인이다. 소멸: 재진입 체결(소진)·유효 기간
    만료·그 심볼의 새 베이스 돌파 진입(자격 대체).
    """

    symbol: str
    granted_on: date       # 전량 청산 체결일(카운트 기산일)
    expires_on: date       # granted_on + window_months(달력일 환산)
    exit_reason: str       # 부여 사유(진단)
    streak: int = 0


@dataclass(frozen=True)
class PendingReentry:
    """트리거 확인(종가) → 익일 시가 체결 대기 중인 재진입 주문 파라미터.

    사이징·손절 산정값(기준가·ATR)은 확인일 종가 확정치로 고정한다 — 체결일의
    직전 세션 값이라 기존 '직전 세션' 계약과 동일(룩어헤드 없음).
    """

    symbol: str
    decided_on: date
    ref_price: float       # 확인일 종가 — 사이징 기준가·추격 상한 기준
    atr: float             # 확인일 ATR(14) — 손절가 산정
    weight: float
    target_notional: float
    qty: int
    rs: float              # 동일일 다중 재진입 정렬용(RS 내림차순·심볼 사전순)


# --------------------------------------------------------------------------- #
# 결과 자료구조
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DailyRecord:
    """일별 자본곡선 1행(계획서 §9)."""

    date: date
    cash: float
    holdings_value: float
    equity: float
    n_positions: int
    exposure_pct: float
    market_states: dict[Market, MarketState]


@dataclass(frozen=True)
class TradeRecord:
    """트레이드 로그 1행 = 청산 회계(ClosedTrade) + 진입 맥락(피벗·단계)."""

    closed: ClosedTrade
    pivot: float
    base_stage: int


@dataclass(frozen=True)
class EventRecord:
    """육안검증 이벤트 1건(계획서 §9): 돌파 후보·추격 스킵·거래량 실패·체결 등."""

    date: date
    symbol: str
    event: str
    detail: dict


# --------------------------------------------------------------------------- #
# 진단(§11 후속) — 진입 퍼널·게이트 분해·현 베이스 단계. 모두 관찰용(체결/이벤트
# 불변). 엔진이 run 중 채우고 리포팅이 CSV로 낸다. 골든 해시에는 포함하지 않는다.
# --------------------------------------------------------------------------- #
@dataclass
class EntryFunnel:
    """종목별 진입 퍼널 — 각 게이트를 순서대로 통과한 세션 수(잔존 카운트).

    엔진이 실제 진입 판정 시점에 세므로 값이 곧 실제 결정과 일치한다(재현·비드리프트).
    필드는 깔때기 순서: held/shopped(분모) → base_present → stage_ok → breakout(기회)
    → 게이트별 통과 → gates_all_ok(후보) → entered(체결).
    """

    symbol: str
    held: int = 0            # 보유중이라 신규진입 평가를 건너뛴 날
    shopped: int = 0         # 신규진입을 평가한 날(미보유·거버너 정상)
    base_present: int = 0    # 유효 베이스 존재
    stage_ok: int = 0        # + 단계 ≤ max_stage
    breakout: int = 0        # + 당일 피벗 돌파(= 진입 '기회'일)
    gate_trend_ok: int = 0   # 기회일 중 트렌드템플릿 통과
    gate_rs_ok: int = 0
    gate_rs_rank_ok: int = 0  # Q14: 전시장 RS 백분위 랭크(꺼짐이면 항상 breakout과 동일)
    gate_market_ok: int = 0
    gate_quality_ok: int = 0
    gates_all_ok: int = 0    # 전 게이트 통과(= 진입 후보)
    entered: int = 0         # 실제 신규 체결


@dataclass(frozen=True)
class GateBreakdownRow:
    """돌파(기회)일 1건의 게이트 개별 판정 + 베이스 스냅샷.

    한 번의 진입 기회에서 어느 게이트가 막았는지 특정한다. `n_failed==1`은 파라미터
    한 개만 풀면 잡히는 '니어미스'다.
    """

    date: date
    symbol: str
    stage: int
    depth_pct: float
    weeks_elapsed: float
    pivot: float
    trend_ok: bool
    rs_ok: bool
    rs_rank_ok: bool      # Q14: 전시장 RS 백분위 랭크(꺼짐이면 항상 True)
    market_ok: bool
    overheat_ok: bool     # 과열 아님
    atr_ok: bool
    contraction_ok: bool
    dryup_ok: bool
    all_pass: bool
    n_failed: int


@dataclass(frozen=True)
class RuleActivation:
    """저표본 개정 발동 1건 — §3.3 추적 의무(P2 승인 조건)의 분리 집계 원자료.

    rule:
      - "r3b_reset_entry"  — 진입 베이스의 단계가 R3b 리셋 없이는 max_stage 초과였을
                             신규 진입(detail: stage, stage_no_reset).
      - "q11_stop_clamp"   — 피라미딩 재계산 손절가가 클램프로 유지된 발동
                             (detail: kept, recalc).
      - "r4a_handle_entry" — 손잡이 피벗으로 성사된 신규 진입
                             (detail: pivot, struct_pivot).
      - "r4b_reentry_entry" — 추세 복귀 재진입 체결(P4, detail: granted_on,
                             exit_reason, streak, ref_price, price).
    골든 해시 제외(진단 채널) — 매 Phase 후보·최종 실행에서 트레이드와 조인해
    건수·손익 기여를 병기한다.
    """

    date: date
    symbol: str
    rule: str
    detail: dict


@dataclass(frozen=True)
class BaseStageSnapshot:
    """종료 시점 종목별 베이스 단계 스냅샷 + 유효 돌파 이력 요약.

    `has_base=False`면 지금 성숙한 베이스가 없다는 뜻(수직상승·붕괴 중). 이때도
    `last_breakout_stage`로 '다음 베이스가 쌓일 단계'를 가늠할 수 있다.
    """

    symbol: str
    as_of: date
    has_base: bool
    stage: int | None
    pivot: float | None
    depth_pct: float | None
    weeks_elapsed: float | None
    tier: str | None
    n_breakouts: int             # 전 기간 유효 돌파 수
    max_stage_reached: int       # 이력 중 최고 단계
    last_breakout_date: date | None
    last_breakout_stage: int | None


@dataclass
class BacktestResult:
    """백테스트 산출물(원자료). Phase 7 리포팅 입력."""

    start: date
    end: date
    initial_cash: float
    final_cash: float
    equity_curve: list[DailyRecord] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    events: list[EventRecord] = field(default_factory=list)
    # 진단(관찰용, 골든 해시 제외). 엔진이 record_diagnostics=True일 때만 채운다.
    entry_funnel: dict[str, EntryFunnel] = field(default_factory=dict)
    gate_breakdown: list[GateBreakdownRow] = field(default_factory=list)
    base_stages: dict[str, BaseStageSnapshot] = field(default_factory=dict)
    rule_activations: list[RuleActivation] = field(default_factory=list)

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1].equity if self.equity_curve else self.final_cash

    @property
    def total_return_pct(self) -> float:
        if self.initial_cash <= 0:
            return 0.0
        return (self.final_equity / self.initial_cash - 1.0) * 100.0
