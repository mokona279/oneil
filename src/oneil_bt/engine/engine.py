"""백테스트 엔진 — 일별 이벤트 루프 (규칙서 §8, 계획서 §6, Phase 6).

전 컴포넌트를 조립해 하루를 §6.3 우선순위로 처리한다. 판정/체결 시점은 §6.1 타이밍
계약을 따른다. 결정론(난수 없음·정렬 고정)이라 동일 입력·설정이면 동일 결과다.

하루 처리 순서(세션일 d):
    1. 대기 청산 체결       — 전일(d-1) 종가에 결정된 매도를 d 시가에 체결(§6.1).
    2. 장중 자동스탑(대안)   — stop_fill_model=intraday_touch면 d 장중 저가로 손절 체결.
    3. 피라미딩 2·3차       — 보유 포지션의 트리거 도달 시 d 장중 체결(시장필터 무관, Q11).
    4. 신규 돌파 진입       — ≤d-1 게이트 통과 + d 장중 돌파. RS 내림차순·심볼 사전순 정렬.
    5. 청산 판정(종가)      — 손절(기본)·60MA·시장방어를 d 종가로 판정 → d+1 시가 대기.
    6. 자본곡선 기록        — d 종가 마크로 평가·노출·시장상태 스냅샷.

룩어헤드 방지:
    - 진입 게이트(트렌드·RS·시장필터)와 사이징 ATR은 직전 세션(d-1) 값을 쓴다. 돌파
      판정만 d 장중 고가를 본다(돌파는 장중, §6.1).
    - 베이스 구조·품질은 컴포넌트 내부에서 ≤d-1로 확정된다.
    - 청산 판정은 d 종가, 체결은 d+1 시가(기본 손절 모델).
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

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
from ..rules.exit_rules import ExitSignal
from .context import (
    BacktestResult,
    DailyRecord,
    EventRecord,
    MarketContext,
    SymbolContext,
    TradePlan,
    TradeRecord,
    build_market_context,
    build_symbol_context,
)

_PYRAMID_REASONS = (EntryReason.PYRAMID_T2, EntryReason.PYRAMID_T3)


class BacktestEngine:
    def __init__(
        self,
        source: DataSource,
        cfg: Config,
        initial_cash: float = 1.0e8,
    ) -> None:
        self.source = source
        self.cfg = cfg
        self.initial_cash = float(initial_cash)

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
        self._result = BacktestResult(
            start=start, end=end,
            initial_cash=self.initial_cash, final_cash=self.initial_cash,
        )

        sessions = self._calendar.sessions_between(start, end)
        for d in sessions:
            prev = self._calendar.shift(d, -1)
            self._fill_pending_exits(d)
            if self.cfg.stop.fill_model is FillModelType.INTRADAY_TOUCH:
                self._process_intraday_stops(d)
            self._process_pyramids(d, prev)
            self._process_entries(d, prev)
            self._decide_exits(d)
            self._record_day(d)

        self._result.final_cash = self._portfolio.cash
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

        candidates: list[tuple[str, Base, float]] = []
        for sym, sc in self._symctx.items():
            if sym in pf.positions:
                continue  # 보유 종목은 피라미딩 대상, 신규 아님
            base = sc.detector.base_asof(d)
            if base is None or base.stage > self.cfg.base.stage.max_stage:
                continue
            if not sc.detector.is_breakout(d, base):
                continue
            if not self._entry_gates(sc, d, prev, base):
                continue
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
            self._try_open(self._symctx[sym], d, prev, base, equity)

    def _entry_gates(
        self, sc: SymbolContext, d: date, prev: date, base: Base
    ) -> bool:
        """진입 게이트 — 트렌드·RS·시장필터·베이스 품질(모두 ≤d-1 기준)."""
        if not sc.trend.passes(prev):
            return False
        if not sc.rs.passes(prev):
            return False
        if not self._mktctx[sc.market].filter.new_entry_allowed(d):
            return False
        return sc.quality.passes(d, base).passed

    def _try_open(
        self, sc: SymbolContext, d: date, prev: date | None, base: Base, equity: float
    ) -> None:
        pf = self._pf
        atr = self._atr_asof(sc, prev)
        if atr is None:
            return
        weight = self.sizer.target_weight(base.pivot, atr)
        target_notional = self.sizer.target_notional(equity, weight)
        ratios = self.cfg.entry.tranche_ratios
        qty = self.sizer.tranche_qty(equity, weight, ratios[0], base.pivot)
        if qty <= 0:
            return
        cap_price = base.pivot * (1.0 + self.cfg.entry.chase_limit_pct / 100.0)
        if not pf.can_open(qty * cap_price):
            return

        bar = sc.prices.row(d)
        if bar is None:
            return
        order = Order.breakout(
            sc.symbol, base.pivot, qty, self.cfg.entry.chase_limit_pct
        )
        fill = self.fill_model.fill_entry(bar, order)
        if fill is None:
            self._event(d, sc.symbol, "CHASE_SKIP", {"pivot": base.pivot})
            return

        initial_stop = sc.stop.stop_price(fill.price, atr)
        pf.apply_buy(sc.symbol, sc.market, fill, initial_stop)
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
            reason=EntryReason.BREAKOUT_T1, cost=entry_cost,
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

    def _event(self, d: date, sym: str, event: str, detail: dict) -> None:
        self._result.events.append(EventRecord(d, sym, event, detail))
