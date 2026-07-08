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
from ..domain.enums import Market, MarketState
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

    @property
    def entry_cost_per_share(self) -> float:
        return self.total_entry_cost / self.total_entry_qty if self.total_entry_qty else 0.0

    @property
    def complete(self) -> bool:
        return self.next_tranche_idx >= len(self.tranche_ratios)


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

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1].equity if self.equity_curve else self.final_cash

    @property
    def total_return_pct(self) -> float:
        if self.initial_cash <= 0:
            return 0.0
        return (self.final_equity / self.initial_cash - 1.0) * 100.0
