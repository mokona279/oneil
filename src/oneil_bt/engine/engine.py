"""백테스트 엔진 — 일별 이벤트 루프 (규칙서 §8, 계획서 §6, Phase 6).

전 컴포넌트를 조립해 하루를 §6.3 우선순위로 처리한다. 판정/체결 시점은 §6.1 타이밍
계약을 따른다. 결정론(난수 없음·정렬 고정)이라 동일 입력·설정이면 동일 결과다.

하루 처리 순서(세션일 d):
    1.  대기 청산 체결      — 전일(d-1) 종가에 결정된 매도를 d 시가에 체결(§6.1).
    1.5 대기 재진입 체결    — R4b(P4): 전일 트리거 확인분을 d 시가(+5% 상한)에 체결.
                              시가라 같은 날 장중 돌파 진입보다 자본을 먼저 쓴다.
    2.  장중 자동스탑(대안)  — stop_fill_model=intraday_touch면 d 장중 저가로 손절 체결.
    3.  피라미딩 2·3차      — 보유 포지션의 트리거 도달 시 d 장중 체결(시장필터 무관, Q11).
    4.  신규 돌파 진입      — ≤d-1 게이트 통과 + d 장중 돌파. RS 내림차순·심볼 사전순 정렬.
    5.  청산 판정(종가)     — 손절(기본)·60MA·시장방어를 d 종가로 판정 → d+1 시가 대기.
    5.5 재진입 판정(종가)   — R4b: 티켓별 50MA 회복 연속 카운트 갱신 → 자격·게이트 통과
                              시 익일 시가 대기(plan/p4_reentry.md Q6).
    6.  자본곡선 기록       — d 종가 마크로 평가·노출·시장상태 스냅샷.

룩어헤드 방지:
    - 진입 게이트(트렌드·RS·시장필터)와 사이징 ATR은 직전 세션(d-1) 값을 쓴다. 돌파
      판정만 d 장중 고가를 본다(돌파는 장중, §6.1).
    - 베이스 구조·품질은 컴포넌트 내부에서 ≤d-1로 확정된다.
    - 청산 판정은 d 종가, 체결은 d+1 시가(기본 손절 모델).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta

import pandas as pd

from ..data.calendar import TradingCalendar
from ..data.datasource import DataSource
from ..domain.config import Config
from ..domain.enums import (
    EntryReason,
    ExitReason,
    FillModelType,
    Market,
    MarketState,
)
from ..domain.trade import ClosedTrade, Fill, Position
from ..execution.cost_model import CostModel
from ..execution.fill_model import DailyBarFillModel
from ..execution.orders import Order
from ..portfolio.portfolio import Portfolio
from ..portfolio.position_sizer import PositionSizer
from ..portfolio.risk_governor import RiskGovernor
from ..rules.base_detector import Base
from ..rules.base_quality import QualityResult
from ..rules.exit_rules import ExitSignal
from .context import (
    BacktestResult,
    BaseStageSnapshot,
    DailyRecord,
    EntryFunnel,
    EventRecord,
    GateBreakdownRow,
    MarketContext,
    PendingReentry,
    ReentryTicket,
    RuleActivation,
    SymbolContext,
    TradePlan,
    TradeRecord,
    build_market_context,
    build_symbol_context,
)

_PYRAMID_REASONS = (EntryReason.PYRAMID_T2, EntryReason.PYRAMID_T3)
# R4b(P4): 재진입 자격을 부여하는 전량 청산 사유 — §6② 추세 이탈만. 손절(STOP)은
# §9 복수 매매 금지 유지, 부분 매도(HALF·방어)는 "전량"이 아니라 자격 사건이 아니다.
_REENTRY_GRANT_REASONS = (ExitReason.TREND_60MA_REST, ExitReason.TREND_60MA_VOLBREAK)


@dataclass(frozen=True)
class GateResult:
    """진입 게이트 4종 개별 판정(트렌드·RS·시장·베이스품질). 관찰·진단용.

    `passed`는 네 게이트 AND로, 리팩터 전 `_entry_gates`의 bool과 동치다(단락만
    제거). 품질은 세부 4요건을 담은 `QualityResult`를 그대로 보관한다.
    """

    trend_ok: bool
    rs_ok: bool
    market_ok: bool
    quality: QualityResult

    @property
    def passed(self) -> bool:
        return self.trend_ok and self.rs_ok and self.market_ok and self.quality.passed


class BacktestEngine:
    def __init__(
        self,
        source: DataSource,
        cfg: Config,
        initial_cash: float = 1.0e8,
        *,
        record_diagnostics: bool = True,
    ) -> None:
        self.source = source
        self.cfg = cfg
        self.initial_cash = float(initial_cash)
        self._record_diag = record_diagnostics

        self.cost = CostModel(cfg.cost)
        self.fill_model = DailyBarFillModel(self.cost, cfg)
        self.sizer = PositionSizer(cfg)

        # 조립 캐시 (run에서 채움)
        self._symctx: dict[str, SymbolContext] = {}
        self._mktctx: dict[Market, MarketContext] = {}
        self._calendar: TradingCalendar | None = None

        # 러닝 상태 (run 1회당 초기화)
        self._portfolio: Portfolio | None = None
        self._governor: RiskGovernor | None = None
        self._plans: dict[str, TradePlan] = {}
        self._pending: list[tuple[str, ExitSignal]] = []
        self._tickets: dict[str, ReentryTicket] = {}
        self._pending_reentries: list[PendingReentry] = []
        self._result: BacktestResult | None = None

    # ------------------------------------------------------------------ #
    # 공개 API
    # ------------------------------------------------------------------ #
    def run(
        self,
        start: date,
        end: date,
        symbols: list[str] | None = None,
    ) -> BacktestResult:
        """[start, end] 구간을 백테스트한다. symbols 생략 시 전체 유니버스.

        단일종목 모드는 `symbols=[sym]`, 포트폴리오 모드는 생략(전체)이다.
        """
        universe = list(symbols) if symbols is not None else self.source.symbols()
        self._prepare(universe)
        assert self._calendar is not None

        self._portfolio = Portfolio(self.initial_cash, self.cfg)
        self._governor = RiskGovernor(self.cfg, self._calendar)
        self._plans = {}
        self._pending = []
        self._tickets = {}
        self._pending_reentries = []
        self._result = BacktestResult(
            start=start, end=end,
            initial_cash=self.initial_cash, final_cash=self.initial_cash,
        )
        if self._record_diag:
            self._result.entry_funnel = {
                sym: EntryFunnel(symbol=sym) for sym in self._symctx
            }

        sessions = self._calendar.sessions_between(start, end)
        for d in sessions:
            prev = self._calendar.shift(d, -1)
            self._fill_pending_exits(d)
            self._fill_pending_reentries(d)
            if self.cfg.stop.fill_model is FillModelType.INTRADAY_TOUCH:
                self._process_intraday_stops(d)
            self._process_pyramids(d, prev)
            self._process_entries(d, prev)
            self._decide_exits(d)
            self._decide_reentries(d)
            self._record_day(d)

        self._result.final_cash = self._portfolio.cash
        if self._record_diag:
            self._snapshot_base_stages(end)
        return self._result

    # ------------------------------------------------------------------ #
    # 조립
    # ------------------------------------------------------------------ #
    def _prepare(self, universe: list[str]) -> None:
        self._symctx = {}
        self._mktctx = {}
        markets: dict[Market, MarketContext] = {}

        for sym in universe:
            market = self.source.meta(sym).market
            if market not in markets:
                index_prices = self.source.load_index(market)
                markets[market] = build_market_context(market, index_prices, self.cfg)
            index_prices = markets[market].index_prices
            prices = self.source.load_prices(sym)
            self._symctx[sym] = build_symbol_context(
                sym, market, prices, index_prices, self.cfg
            )
        self._mktctx = markets

        # 거래일 캘린더 = 지수 CSV 날짜(§4.4). 코스피 우선, 없으면 첫 시장.
        cal_market = Market.KOSPI if Market.KOSPI in markets else next(iter(markets))
        self._calendar = TradingCalendar.from_index(
            markets[cal_market].index_prices.df.index  # type: ignore[arg-type]
        )

    # ------------------------------------------------------------------ #
    # 1. 대기 청산 체결 (전일 결정 → 당일 시가)
    # ------------------------------------------------------------------ #
    def _fill_pending_exits(self, d: date) -> None:
        pf = self._pf
        carry: list[tuple[str, ExitSignal]] = []
        for sym, sig in self._pending:
            pos = pf.positions.get(sym)
            if pos is None:
                continue  # 이미 청산됨
            qty = min(sig.qty, pos.qty)
            if qty <= 0 or sig.reason is None:
                continue
            sc = self._symctx[sym]
            bar = sc.prices.row(d)
            if bar is None:
                carry.append((sym, sig))  # 거래정지 등 → 다음 세션 시가로 이월
                continue
            order = Order.exit(sym, qty, sig.reason, pos.market, stop_price=pos.stop_price)
            fill = self.fill_model.fill_exit(bar, order)
            self._record_sell(sc, pos, fill)
        self._pending = carry

    # ------------------------------------------------------------------ #
    # 1.5 대기 재진입 체결 (R4b, P4) — 전일 트리거 확인 → 당일 시가(+5% 상한)
    # ------------------------------------------------------------------ #
    def _fill_pending_reentries(self, d: date) -> None:
        """전일 종가에 트리거가 확인된 재진입을 d 시가에 체결한다.

        미체결(갭 상한 초과·슬롯/현금 부족·거버너 중단)이면 대기를 버린다 — 티켓은
        남으므로 streak가 유지되는 한 그날 저녁(5.5)에 재확인된다. 거래정지(결측 바)
        만 다음 세션으로 이월한다(대기 청산과 동일).
        """
        if not self._pending_reentries:
            return
        pf, gov = self._pf, self._gov
        queue = sorted(self._pending_reentries, key=lambda p: (-p.rs, p.symbol))
        self._pending_reentries = []
        for p in queue:
            ticket = self._tickets.get(p.symbol)
            if ticket is None or p.symbol in pf.positions:
                continue  # 자격 소멸/이미 보유(방어적) → 폐기
            if gov.new_trades_blocked(d):
                continue  # §7 신규 중단 — 티켓 유지, 저녁 재판정
            sc = self._symctx[p.symbol]
            bar = sc.prices.row(d)
            if bar is None:
                self._pending_reentries.append(p)  # 거래정지 → 다음 세션 이월
                continue
            cap_price = p.ref_price * (1.0 + self.cfg.entry.chase_limit_pct / 100.0)
            if not pf.has_slot() or not pf.can_open(p.qty * cap_price):
                continue  # 자본 경합 → 폐기(티켓 유지)
            order = Order.reentry(
                p.symbol, p.ref_price, p.qty, self.cfg.entry.chase_limit_pct
            )
            fill = self.fill_model.fill_entry_open(bar, order)
            if fill is None:
                self._event(d, p.symbol, "REENTRY_CHASE_SKIP", {
                    "ref_price": p.ref_price, "cap": cap_price,
                })
                continue  # 상한 초과 갭 → 미체결(티켓 유지, 재확인 대상)

            stop = sc.stop.stop_price(fill.price, p.atr)
            pf.apply_buy(p.symbol, sc.market, fill, stop)
            # 1차 트랜치만 — next_tranche_idx를 끝으로 시작해 피라미딩·예약이 없다.
            self._plans[p.symbol] = TradePlan(
                symbol=p.symbol, market=sc.market, pivot=p.ref_price,
                base_stage=0, weight=p.weight, target_notional=p.target_notional,
                tranche_ratios=self.cfg.entry.tranche_ratios,
                first_fill_price=fill.price,
                risk_per_share=max(0.0, fill.price - stop),
                next_tranche_idx=len(self.cfg.entry.tranche_ratios),
                total_entry_cost=fill.cost, total_entry_qty=fill.qty,
                entry_reason=EntryReason.REENTRY_50MA,
            )
            consumed = self._tickets.pop(p.symbol)  # 자격 1회 = 재진입 1회(소진)
            self._event(d, p.symbol, "REENTRY_ENTRY", {
                "price": fill.price, "qty": fill.qty, "ref_price": p.ref_price,
                "granted_on": str(consumed.granted_on),
            })
            if self._record_diag:
                self._result.rule_activations.append(RuleActivation(
                    d, p.symbol, "r4b_reentry_entry", {
                        "granted_on": str(consumed.granted_on),
                        "exit_reason": consumed.exit_reason,
                        "streak": consumed.streak,
                        "ref_price": p.ref_price, "price": fill.price,
                    },
                ))

    # ------------------------------------------------------------------ #
    # 2. 장중 자동스탑 (대안 모델) — 당일 저가 터치 시 당일 체결
    # ------------------------------------------------------------------ #
    def _process_intraday_stops(self, d: date) -> None:
        pf = self._pf
        for sym in list(pf.positions):
            pos = pf.positions[sym]
            sc = self._symctx[sym]
            if not sc.stop.hit(pos, d):
                continue
            bar = sc.prices.row(d)
            if bar is None:
                continue
            order = Order.exit(
                sym, pos.qty, ExitReason.STOP, pos.market, stop_price=pos.stop_price
            )
            fill = self.fill_model.fill_exit(bar, order)
            self._record_sell(sc, pos, fill)

    # ------------------------------------------------------------------ #
    # 3. 피라미딩 2·3차 (당일 장중)
    # ------------------------------------------------------------------ #
    def _process_pyramids(self, d: date, prev: date | None) -> None:
        pf = self._pf
        ratios = self.cfg.entry.tranche_ratios
        triggers = self.cfg.entry.pyramid_triggers_pct
        cap_pct = self.cfg.entry.tranche_price_cap_pct

        for sym, plan in list(self._plans.items()):
            if not plan.pyramid_allowed or plan.exiting or plan.complete:
                continue
            pos = pf.positions.get(sym)
            if pos is None:
                continue
            sc = self._symctx[sym]
            bar = sc.prices.row(d)
            if bar is None:
                continue
            idx = plan.next_tranche_idx
            trigger = plan.first_fill_price * (1.0 + triggers[idx - 1] / 100.0)
            notional = plan.target_notional * ratios[idx]
            qty = int(math.floor(notional / trigger)) if trigger > 0 else 0
            if qty <= 0:
                plan.next_tranche_idx += 1  # 살 수량 없음 → 이 트랜치 건너뜀
                continue
            reason = _PYRAMID_REASONS[min(idx - 1, len(_PYRAMID_REASONS) - 1)]
            order = Order.pyramid(sym, trigger, qty, cap_pct, reason)
            fill = self.fill_model.fill_pyramid(bar, order)
            if fill is None:
                continue  # 트리거 미도달/갭 스킵 → 이후 세션 재시도

            pf.release(sym, notional)
            plan.reserved = max(0.0, plan.reserved - notional)
            updated = pf.apply_buy(sym, sc.market, fill, stop_price=pos.stop_price)
            atr = self._atr_asof(sc, prev)
            if atr is not None:
                new_stop = sc.stop.stop_price(updated.avg_price, atr)
                if self.cfg.stop.no_lower_recalc:
                    # Q11(확정): 손절가는 올라가기만 — 평단 소폭 상승+ATR 급증 조합에서
                    # 재계산 손절가가 기존보다 낮아지는 구멍 봉쇄.
                    if self._record_diag and new_stop < updated.stop_price:
                        self._result.rule_activations.append(RuleActivation(
                            d, sym, "q11_stop_clamp",
                            {"kept": updated.stop_price, "recalc": new_stop},
                        ))
                    new_stop = max(updated.stop_price, new_stop)
                pf.positions[sym] = replace(updated, stop_price=new_stop)
            plan.total_entry_cost += fill.cost
            plan.total_entry_qty += fill.qty
            plan.next_tranche_idx += 1
            self._event(d, sym, "PYRAMID", {
                "tranche_no": idx + 1, "price": fill.price, "qty": fill.qty,
            })

    # ------------------------------------------------------------------ #
    # 4. 신규 돌파 진입 (당일 장중, ≤d-1 게이트)
    # ------------------------------------------------------------------ #
    def _process_entries(self, d: date, prev: date | None) -> None:
        pf, gov = self._pf, self._gov
        if prev is None or gov.new_trades_blocked(d):
            return

        diag = self._record_diag
        funnel = self._result.entry_funnel
        max_stage = self.cfg.base.stage.max_stage
        # R3a(Q5a): 계수가 있으면 max_stage 초과 베이스도 감액 진입 허용(_try_open에서
        # 목표 비중에 곱한다). None이면 현행(초과 = 진입 금지).
        overlimit = self.cfg.base.stage.overlimit_weight_factor

        candidates: list[tuple[str, Base, float]] = []
        for sym, sc in self._symctx.items():
            if sym in pf.positions:
                if diag:
                    funnel[sym].held += 1
                continue  # 보유 종목은 피라미딩 대상, 신규 아님
            if diag:
                funnel[sym].shopped += 1
            base = sc.detector.base_asof(d)
            if base is None:
                continue
            if diag:
                funnel[sym].base_present += 1
            if base.stage > max_stage and overlimit is None:
                continue
            if diag:
                funnel[sym].stage_ok += 1
            if not sc.detector.is_breakout(d, base):
                continue
            gates = self._entry_gates(sc, d, prev, base)
            if diag:
                f = funnel[sym]
                f.breakout += 1
                f.gate_trend_ok += int(gates.trend_ok)
                f.gate_rs_ok += int(gates.rs_ok)
                f.gate_market_ok += int(gates.market_ok)
                f.gate_quality_ok += int(gates.quality.passed)
                self._record_gate_row(d, sym, base, gates)
            if not gates.passed:
                continue
            if diag:
                funnel[sym].gates_all_ok += 1
            rs_val = sc.ind.asof("rs_6m", prev)
            candidates.append((sym, base, rs_val if rs_val is not None else -math.inf))
            self._event(d, sym, "BREAKOUT_CANDIDATE", {
                "pivot": base.pivot, "depth_pct": base.depth_pct,
                "weeks": base.weeks_elapsed, "stage": base.stage,
            })

        # 동일일 다중 신규진입: RS 내림차순, 동점 심볼 사전순(결정론, §6.3).
        candidates.sort(key=lambda x: (-x[2], x[0]))

        marks = self._marks(prev)
        equity = pf.equity(marks)
        for sym, base, _rs in candidates:
            if not pf.has_slot():
                break
            opened = self._try_open(self._symctx[sym], d, prev, base, equity)
            if diag and opened:
                funnel[sym].entered += 1

    def _entry_gates(
        self, sc: SymbolContext, d: date, prev: date, base: Base
    ) -> GateResult:
        """진입 게이트 4종(트렌드·RS·시장필터·베이스품질, 모두 ≤d-1 기준).

        단락 없이 넷 다 평가해 진단(어느 게이트가 막았는지)에 쓴다. `passed`는
        리팩터 전 bool(전부 통과)과 동치다 — 순수 판정이라 순서·부작용 없음.
        """
        return GateResult(
            trend_ok=sc.trend.passes(prev),
            rs_ok=sc.rs.passes(prev),
            market_ok=self._mktctx[sc.market].filter.new_entry_allowed(d),
            quality=sc.quality.passes(d, base),
        )

    def _record_gate_row(
        self, d: date, sym: str, base: Base, gates: GateResult
    ) -> None:
        """돌파(기회)일 1건의 게이트 개별 판정을 진단 로그에 남긴다."""
        q = gates.quality
        bools = (gates.trend_ok, gates.rs_ok, gates.market_ok,
                 q.not_overheated, q.atr_ok, q.contraction_ok, q.dryup_ok)
        self._result.gate_breakdown.append(GateBreakdownRow(
            date=d, symbol=sym, stage=base.stage, depth_pct=base.depth_pct,
            weeks_elapsed=base.weeks_elapsed, pivot=base.pivot,
            trend_ok=gates.trend_ok, rs_ok=gates.rs_ok, market_ok=gates.market_ok,
            overheat_ok=q.not_overheated, atr_ok=q.atr_ok,
            contraction_ok=q.contraction_ok, dryup_ok=q.dryup_ok,
            all_pass=gates.passed, n_failed=sum(1 for b in bools if not b),
        ))

    def _try_open(
        self, sc: SymbolContext, d: date, prev: date | None, base: Base, equity: float
    ) -> bool:
        """신규 진입 1차 트랜치 체결 시도. 실제 체결하면 True, 스킵하면 False."""
        pf = self._pf
        atr = self._atr_asof(sc, prev)
        if atr is None:
            return False
        weight = self.sizer.target_weight(base.pivot, atr)
        if base.stage > self.cfg.base.stage.max_stage:
            # R3a(Q5a): 후기(초과) 베이스는 감액 진입 — 1회 손실이 risk%×계수로 준다.
            # 게이트에서 계수 None인 초과 베이스는 걸렀으므로 여기선 항상 값이 있다.
            weight *= self.cfg.base.stage.overlimit_weight_factor
        target_notional = self.sizer.target_notional(equity, weight)
        ratios = self.cfg.entry.tranche_ratios
        qty = self.sizer.tranche_qty(equity, weight, ratios[0], base.pivot)
        if qty <= 0:
            return False
        cap_price = base.pivot * (1.0 + self.cfg.entry.chase_limit_pct / 100.0)
        if not pf.can_open(qty * cap_price):
            return False

        bar = sc.prices.row(d)
        if bar is None:
            return False
        order = Order.breakout(
            sc.symbol, base.pivot, qty, self.cfg.entry.chase_limit_pct
        )
        fill = self.fill_model.fill_entry(bar, order)
        if fill is None:
            self._event(d, sc.symbol, "CHASE_SKIP", {"pivot": base.pivot})
            return False

        initial_stop = sc.stop.stop_price(fill.price, atr)
        pf.apply_buy(sc.symbol, sc.market, fill, initial_stop)
        self._tickets.pop(sc.symbol, None)  # R4b(Q6-7): 새 베이스 돌파가 자격을 대체
        plan = TradePlan(
            symbol=sc.symbol, market=sc.market, pivot=base.pivot,
            base_stage=base.stage, weight=weight, target_notional=target_notional,
            tranche_ratios=ratios, first_fill_price=fill.price,
            risk_per_share=max(0.0, fill.price - initial_stop),
            total_entry_cost=fill.cost, total_entry_qty=fill.qty,
        )
        self._plans[sc.symbol] = plan
        self._event(d, sc.symbol, "ENTRY", {
            "price": fill.price, "qty": fill.qty, "stage": base.stage,
            "pivot": base.pivot,
        })
        if self._record_diag:
            # §3.3 저표본 개정 추적 — 리셋 경유 진입·핸들 진입을 분리 기록.
            plain = base.stage_no_reset if base.stage_no_reset is not None else base.stage
            if base.stage <= self.cfg.base.stage.max_stage < plain:
                self._result.rule_activations.append(RuleActivation(
                    d, sc.symbol, "r3b_reset_entry",
                    {"stage": base.stage, "stage_no_reset": plain},
                ))
            if base.handle:
                self._result.rule_activations.append(RuleActivation(
                    d, sc.symbol, "r4a_handle_entry",
                    {"pivot": base.pivot, "struct_pivot": base.struct_pivot},
                ))

        # 돌파일 거래량 게이트(1.5×) → 2·3차 예약 여부(§6.1). d 거래량은 종가 확정.
        vol_ma20 = sc.ind.asof("vol_ma20", prev)
        if self.fill_model.volume_confirmed(float(bar["volume"]), vol_ma20):
            plan.pyramid_allowed = True
            reserve_amt = target_notional * sum(ratios[1:])
            pf.reserve(sc.symbol, reserve_amt)
            plan.reserved = reserve_amt
        else:
            self._event(d, sc.symbol, "VOL_FAIL", {
                "volume": float(bar["volume"]), "vol_ma20": vol_ma20,
            })
        return True

    # ------------------------------------------------------------------ #
    # 진단 — 종료 시점 베이스 단계 스냅샷
    # ------------------------------------------------------------------ #
    def _snapshot_base_stages(self, end: date) -> None:
        """종목별 현(종료일) 베이스 단계 + 유효 돌파 이력 요약을 기록한다."""
        for sym, sc in self._symctx.items():
            det = sc.detector
            base = det.base_asof(end)
            bos = det.breakouts
            last = bos[-1] if bos else None
            self._result.base_stages[sym] = BaseStageSnapshot(
                symbol=sym, as_of=end,
                has_base=base is not None,
                stage=base.stage if base else None,
                pivot=base.pivot if base else None,
                depth_pct=base.depth_pct if base else None,
                weeks_elapsed=base.weeks_elapsed if base else None,
                tier=base.tier if base else None,
                n_breakouts=len(bos),
                max_stage_reached=max((b.stage for b in bos), default=0),
                last_breakout_date=last.date if last else None,
                last_breakout_stage=last.stage if last else None,
            )

    # ------------------------------------------------------------------ #
    # 5. 청산 판정 (종가) → 다음 세션 시가 대기
    # ------------------------------------------------------------------ #
    def _decide_exits(self, d: date) -> None:
        pf = self._pf
        stop_close = self.cfg.stop.fill_model is FillModelType.CLOSE_CONFIRMED_NEXT_OPEN
        defense_today = {
            m: ctx.filter.defense_triggered_on(d) for m, ctx in self._mktctx.items()
        }
        for sym in list(pf.positions):
            pos = pf.positions[sym]
            sc = self._symctx[sym]

            # 손절(기본 종가확정) — 최우선. 장중 모델은 step2에서 이미 처리됨.
            if stop_close and sc.stop.hit(pos, d):
                self._queue_exit(sym, ExitSignal(d, ExitReason.STOP, pos.qty))
                continue

            # 60MA 추세 이탈
            sig = sc.trend_exit.evaluate(pos, d)
            if sig is not None:
                if sig.reason is None:
                    pf.positions[sym] = replace(pos, trend_break_date=None)  # 회복
                    continue
                if sig.is_sell:
                    if pos.trend_break_date is None and sig.reason in (
                        ExitReason.TREND_60MA_HALF, ExitReason.TREND_60MA_VOLBREAK
                    ):
                        pf.positions[sym] = replace(pos, trend_break_date=d)
                    self._queue_exit(sym, sig)
                    continue

            # 시장 방어(120MA 이탈 발생일에만)
            if defense_today.get(pos.market):
                state = self._mktctx[pos.market].filter.state_asof(d)
                dsig = sc.defense.evaluate(pos, d, state)
                if dsig is not None and dsig.is_sell:
                    self._queue_exit(sym, dsig)

    def _queue_exit(self, sym: str, sig: ExitSignal) -> None:
        self._pending.append((sym, sig))
        plan = self._plans.get(sym)
        if plan is not None:
            plan.exiting = True

    # ------------------------------------------------------------------ #
    # 5.5 재진입 판정 (R4b, P4 — 종가) → 다음 세션 시가 대기
    # ------------------------------------------------------------------ #
    def _decide_reentries(self, d: date) -> None:
        """티켓별 50MA 회복 연속 카운트를 갱신하고, 트리거 확인 시 익일 대기를 건다.

        판정은 전부 d 종가 확정치(≤d) — 익일 시가 체결이라 룩어헤드가 없다. 실거래
        바가 없는 세션(거래정지)은 카운트하지 않는다(streak 유지). 거버너 중단·게이트
        실패는 그날 대기만 차단하고 streak는 계속 간다(Q6-5).
        """
        rcfg = self.cfg.reentry
        if rcfg.confirm_sessions is None or not self._tickets:
            return
        pf = self._pf
        pending_syms = {p.symbol for p in self._pending_reentries}
        equity: float | None = None  # 첫 후보에서 1회만 평가(마크 비용 절약)
        for sym in sorted(self._tickets):
            ticket = self._tickets[sym]
            if sym in pf.positions:
                continue  # 방어적 — 보유 중 티켓은 정상 흐름에선 없다
            if d > ticket.expires_on:
                del self._tickets[sym]  # 유효 기간 만료 → 자격 소멸(Q6-2)
                continue
            sc = self._symctx[sym]
            bar = sc.prices.row(d)
            if bar is None:
                continue  # 실거래 바 세션만 카운트
            ma = sc.ind.asof(f"ma{rcfg.ma}", d)
            close = float(bar["close"])
            if ma is None or close < ma:
                ticket.streak = 0  # 하회(또는 MA 미확정) → 리셋(Q6-1)
                continue
            ticket.streak += 1
            if ticket.streak < rcfg.confirm_sessions or sym in pending_syms:
                continue
            # 자격 게이트(Q6-5): 템플릿·시장필터 정상 — d 종가 확정치. + 거버너.
            if self._gov.new_trades_blocked(d):
                continue
            if not sc.trend.passes(d):
                continue
            if self._mktctx[sc.market].filter.state_asof(d) is not MarketState.NORMAL:
                continue
            atr = sc.ind.asof("atr14", d)
            if atr is None:
                continue
            weight = self.sizer.target_weight(close, atr)
            if equity is None:
                equity = pf.equity(self._marks(d))
            target_notional = self.sizer.target_notional(equity, weight)
            qty = self.sizer.tranche_qty(
                equity, weight, self.cfg.entry.tranche_ratios[0], close
            )
            if qty <= 0:
                continue
            rs_val = sc.ind.asof("rs_6m", d)
            self._pending_reentries.append(PendingReentry(
                symbol=sym, decided_on=d, ref_price=close, atr=atr,
                weight=weight, target_notional=target_notional, qty=qty,
                rs=rs_val if rs_val is not None else -math.inf,
            ))
            self._event(d, sym, "REENTRY_TRIGGER", {
                "ref_price": close, "ma": ma, "streak": ticket.streak,
                "granted_on": str(ticket.granted_on),
            })

    # ------------------------------------------------------------------ #
    # 6. 자본곡선 기록
    # ------------------------------------------------------------------ #
    def _record_day(self, d: date) -> None:
        pf = self._pf
        marks = self._marks(d)
        holdings = pf.holdings_value(marks)
        equity = pf.cash + holdings
        exposure = (holdings / equity * 100.0) if equity > 0 else 0.0
        states = {m: ctx.filter.state_asof(d) for m, ctx in self._mktctx.items()}
        self._result.equity_curve.append(DailyRecord(
            date=d, cash=pf.cash, holdings_value=holdings, equity=equity,
            n_positions=pf.n_positions, exposure_pct=exposure, market_states=states,
        ))

    # ------------------------------------------------------------------ #
    # 헬퍼
    # ------------------------------------------------------------------ #
    @property
    def _pf(self) -> Portfolio:
        assert self._portfolio is not None
        return self._portfolio

    @property
    def _gov(self) -> RiskGovernor:
        assert self._governor is not None
        return self._governor

    def _atr_asof(self, sc: SymbolContext, prev: date | None) -> float | None:
        """직전 세션(≤d-1) ATR — 사이징·손절 재계산용(룩어헤드 없음)."""
        if prev is None:
            return None
        return sc.ind.asof("atr14", prev)

    def _marks(self, day: date | None) -> dict[str, float]:
        """보유 종목의 day 이하 최근 종가 마크. 없는 심볼은 생략(평단 평가로 대체)."""
        if day is None:
            return {}
        marks: dict[str, float] = {}
        for sym in self._pf.positions:
            row = self._symctx[sym].prices.asof(day)
            if row is not None:
                marks[sym] = float(row["close"])
        return marks

    def _record_sell(self, sc: SymbolContext, pos: Position, fill: Fill) -> None:
        """매도 체결 반영 — 포트폴리오·거버너·트레이드로그·이벤트를 갱신."""
        pf = self._pf
        plan = self._plans.get(sc.symbol)
        entry_cost = (plan.entry_cost_per_share if plan else 0.0) * fill.qty
        risk = plan.risk_per_share if plan else max(0.0, pos.entry_price - pos.stop_price)
        entry_fill = Fill(
            date=pos.entry_date, price=pos.avg_price, qty=fill.qty,
            reason=plan.entry_reason if plan else EntryReason.BREAKOUT_T1,
            cost=entry_cost,
        )
        closed = ClosedTrade(
            symbol=sc.symbol, market=pos.market, tranche_no=1,
            entry_fill=entry_fill, exit_fill=fill,
            risk_per_share=risk,
        )
        self._gov.record_exit(closed)
        self._result.trades.append(TradeRecord(
            closed=closed,
            pivot=plan.pivot if plan else pos.entry_price,
            base_stage=plan.base_stage if plan else 1,
        ))
        remaining = pf.apply_sell(sc.symbol, fill)
        self._event(fill.date, sc.symbol, "EXIT", {
            "reason": str(fill.reason), "price": fill.price, "qty": fill.qty,
        })
        if remaining is None:
            self._plans.pop(sc.symbol, None)
            rcfg = self.cfg.reentry
            if rcfg.confirm_sessions is not None and fill.reason in _REENTRY_GRANT_REASONS:
                # R4b(Q6-7): §6② 전량 청산 → 재진입 자격 부여(심볼당 최신 1개).
                # 카운트는 청산 체결일 당일 종가부터 기산된다(당일 5.5에서 첫 갱신).
                self._tickets[sc.symbol] = ReentryTicket(
                    symbol=sc.symbol, granted_on=fill.date,
                    expires_on=fill.date + timedelta(days=rcfg.window_days or 0),
                    exit_reason=str(fill.reason),
                )

    def _event(self, d: date, sym: str, event: str, detail: dict) -> None:
        self._result.events.append(EventRecord(d, sym, event, detail))
