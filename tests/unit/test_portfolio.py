"""포트폴리오 레이어 (Phase 5 DoD).

사이저(비중=risk/손절%·상한20·트랜치 정수주), 포트폴리오(현금·슬롯·예약·회계
항등식), 리스크거버너(연속손절 3회→차단·해제)를 검증한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import EntryReason, ExitReason, Market
from oneil_bt.domain.trade import ClosedTrade, Fill
from oneil_bt.data.calendar import TradingCalendar
from oneil_bt.portfolio.portfolio import Portfolio
from oneil_bt.portfolio.position_sizer import PositionSizer
from oneil_bt.portfolio.risk_governor import RiskGovernor
from tests.fixtures.synthetic import business_dates

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


def _buy(price: float, qty: int, cost: float = 0.0, d: date = date(2020, 1, 6)) -> Fill:
    return Fill(date=d, price=price, qty=qty, reason=EntryReason.BREAKOUT_T1, cost=cost)


def _sell(
    price: float, qty: int, reason: ExitReason = ExitReason.STOP,
    cost: float = 0.0, d: date = date(2020, 2, 3),
) -> Fill:
    return Fill(date=d, price=price, qty=qty, reason=reason, cost=cost)


# --------------------------------------------------------------------------- #
# PositionSizer — 비중 = risk / 손절% (상한 20%)
# --------------------------------------------------------------------------- #
def test_target_weight_scales_inverse_to_stop_distance(cfg: Config) -> None:
    sz = PositionSizer(cfg)
    # 2×ATR = 10 → 손절폭 10% → 비중 1/10 = 0.10.
    assert sz.target_weight(100.0, 5.0) == pytest.approx(0.10)
    # 2×ATR = 5 → 손절폭 5% → 비중 1/5 = 0.20 (= 상한).
    assert sz.target_weight(100.0, 2.5) == pytest.approx(0.20)


def test_target_weight_capped_at_max_weight(cfg: Config) -> None:
    sz = PositionSizer(cfg)
    # 2×ATR = 2 → 손절폭 2% → 원래 0.50 이지만 상한 0.20 으로 클램프.
    assert sz.target_weight(100.0, 1.0) == pytest.approx(0.20)


def test_stop_distance_clamped_at_max_stop_pct(cfg: Config) -> None:
    sz = PositionSizer(cfg)
    # 2×ATR = 16 → 16% > 10% 캡 → 손절폭 10%로 클램프 → 비중 0.10.
    assert sz.stop_distance_pct(100.0, 8.0) == pytest.approx(10.0)
    assert sz.target_weight(100.0, 8.0) == pytest.approx(0.10)


def test_tranche_qty_floors_to_whole_shares(cfg: Config) -> None:
    sz = PositionSizer(cfg)
    # 자본 1,000,000 × 비중 0.10 × 1차 0.5 = 50,000 → /105 = 476.19 → 476주.
    assert sz.tranche_qty(1_000_000, 0.10, 0.5, 105.0) == 476
    # 정확히 나눠떨어지는 경우.
    assert sz.tranche_qty(1_000_000, 0.10, 0.5, 100.0) == 500


def test_tranche_qty_zero_on_bad_inputs(cfg: Config) -> None:
    sz = PositionSizer(cfg)
    assert sz.tranche_qty(0, 0.1, 0.5, 100.0) == 0
    assert sz.tranche_qty(1_000_000, 0.1, 0.5, 0.0) == 0


# --------------------------------------------------------------------------- #
# Portfolio — 매수/매도·회계 항등식
# --------------------------------------------------------------------------- #
def test_apply_buy_creates_position_and_debits_cash(cfg: Config) -> None:
    pf = Portfolio(1_000_000, cfg)
    pf.apply_buy("A", Market.KOSPI, _buy(100.0, 500, cost=150.0), stop_price=90.0)
    assert pf.cash == pytest.approx(1_000_000 - (100.0 * 500 + 150.0))
    pos = pf.positions["A"]
    assert pos.qty == 500 and pos.avg_price == pytest.approx(100.0)
    assert pos.entry_price == pytest.approx(100.0) and pos.stop_price == pytest.approx(90.0)


def test_accounting_identity_equity_conserved_minus_cost(cfg: Config) -> None:
    pf = Portfolio(1_000_000, cfg)
    pf.apply_buy("A", Market.KOSPI, _buy(100.0, 500, cost=150.0), stop_price=90.0)
    # 마크가 체결가와 같으면 자본은 매수 비용만큼만 줄어든다(현금+평가=자본).
    eq = pf.equity({"A": 100.0})
    assert eq == pytest.approx(1_000_000 - 150.0)
    assert eq == pytest.approx(pf.cash + pf.holdings_value({"A": 100.0}))


def test_apply_buy_pyramid_updates_avg_and_qty(cfg: Config) -> None:
    pf = Portfolio(1_000_000, cfg)
    pf.apply_buy("A", Market.KOSPI, _buy(100.0, 100), stop_price=90.0)
    pf.apply_buy("A", Market.KOSPI, _buy(110.0, 100), stop_price=99.0)
    pos = pf.positions["A"]
    assert pos.qty == 200
    assert pos.avg_price == pytest.approx(105.0)   # (100*100 + 110*100)/200
    assert pos.entry_price == pytest.approx(100.0)  # 1차가 유지
    assert pos.stop_price == pytest.approx(99.0)    # 갱신값 반영


def test_apply_sell_partial_then_full(cfg: Config) -> None:
    pf = Portfolio(1_000_000, cfg)
    pf.apply_buy("A", Market.KOSPI, _buy(100.0, 200), stop_price=90.0)
    cash_after_buy = pf.cash
    left = pf.apply_sell("A", _sell(120.0, 100, cost=50.0))
    assert left is not None and left.qty == 100
    assert pf.cash == pytest.approx(cash_after_buy + (120.0 * 100 - 50.0))
    gone = pf.apply_sell("A", _sell(120.0, 100))
    assert gone is None and "A" not in pf.positions


def test_apply_sell_rejects_oversell(cfg: Config) -> None:
    pf = Portfolio(1_000_000, cfg)
    pf.apply_buy("A", Market.KOSPI, _buy(100.0, 100), stop_price=90.0)
    with pytest.raises(ValueError):
        pf.apply_sell("A", _sell(120.0, 200))


# --------------------------------------------------------------------------- #
# Portfolio — 슬롯·현금 제약 (§6.3)
# --------------------------------------------------------------------------- #
def test_slot_limit_blocks_new_open_at_max_positions(cfg: Config) -> None:
    pf = Portfolio(10_000_000, cfg)
    for i in range(cfg.portfolio.max_positions):  # 8종목
        pf.apply_buy(f"S{i}", Market.KOSPI, _buy(100.0, 10), stop_price=90.0)
    assert pf.n_positions == 8
    assert pf.has_slot() is False
    assert pf.can_open(1.0) is False  # 슬롯 없음 → 현금 무관 거부


def test_can_open_rejects_when_cash_insufficient(cfg: Config) -> None:
    pf = Portfolio(1_000, cfg)
    assert pf.can_open(2_000) is False   # 현금 부족
    assert pf.can_open(500) is True      # 슬롯 여유 + 현금 충분


# --------------------------------------------------------------------------- #
# Portfolio — 예약 현금 (피라미딩 2·3차)
# --------------------------------------------------------------------------- #
def test_reserved_cash_reduces_available_and_blocks_open(cfg: Config) -> None:
    pf = Portfolio(100_000, cfg)
    pf.reserve("A", 60_000)  # 2·3차 몫 예약
    assert pf.reserved_cash == pytest.approx(60_000)
    assert pf.available_cash == pytest.approx(40_000)
    assert pf.can_open(50_000) is False  # 예약분 제외하면 부족
    assert pf.can_open(40_000) is True


def test_release_restores_available_cash(cfg: Config) -> None:
    pf = Portfolio(100_000, cfg)
    pf.reserve("A", 60_000)
    pf.release("A", 30_000)              # 2차 체결분 해제
    assert pf.available_cash == pytest.approx(70_000)
    pf.release("A")                      # 나머지 전액 해제
    assert pf.reserved_cash == pytest.approx(0.0)


def test_selling_out_clears_reservation(cfg: Config) -> None:
    pf = Portfolio(100_000, cfg)
    pf.apply_buy("A", Market.KOSPI, _buy(100.0, 100), stop_price=90.0)
    pf.reserve("A", 20_000)
    pf.apply_sell("A", _sell(120.0, 100))
    assert pf.reserved_cash == pytest.approx(0.0)  # 청산 시 예약도 정리


def test_reserve_toggle_off_ignores_reservation() -> None:
    base = Config.load(RULES, COSTS)
    cfg_off = replace(base, sizing=replace(base.sizing, reserve_pyramid_cash=False))
    pf = Portfolio(100_000, cfg_off)
    pf.reserve("A", 60_000)
    assert pf.reserved_cash == pytest.approx(0.0)
    assert pf.available_cash == pytest.approx(100_000)


# --------------------------------------------------------------------------- #
# RiskGovernor — 연속손절 3회 → 차단·해제
# --------------------------------------------------------------------------- #
def _closed(reason: ExitReason, d: date) -> ClosedTrade:
    return ClosedTrade(
        symbol="A",
        market=Market.KOSPI,
        tranche_no=1,
        entry_fill=_buy(100.0, 10, d=date(2020, 1, 6)),
        exit_fill=_sell(95.0, 10, reason=reason, d=d),
        risk_per_share=10.0,
    )


def test_three_consecutive_stops_block_new_trades(cfg: Config) -> None:
    sessions = business_dates("2020-01-06", 40)
    cal = TradingCalendar(sessions)
    gov = RiskGovernor(cfg, cal)
    for d in sessions[:3]:
        gov.record_exit(_closed(ExitReason.STOP, d))
    # 3번째 손절일(sessions[2]) 기준 10거래일 뒤까지 차단.
    halt_end = cal.shift(sessions[2], cfg.risk_governor.halt_days)
    assert gov.new_trades_blocked(sessions[3]) is True
    assert gov.new_trades_blocked(halt_end) is False  # 종료일 도달 → 해제
    assert gov.consecutive_stops == 0                 # 발동 후 카운터 리셋


def test_non_stop_exit_resets_consecutive_counter(cfg: Config) -> None:
    sessions = business_dates("2020-01-06", 10)
    gov = RiskGovernor(cfg, TradingCalendar(sessions))
    gov.record_exit(_closed(ExitReason.STOP, sessions[0]))
    gov.record_exit(_closed(ExitReason.STOP, sessions[1]))
    gov.record_exit(_closed(ExitReason.TREND_60MA_HALF, sessions[2]))  # 손절 아님 → 리셋
    gov.record_exit(_closed(ExitReason.STOP, sessions[3]))
    assert gov.consecutive_stops == 1
    assert gov.new_trades_blocked(sessions[4]) is False


def test_disabled_governor_never_blocks() -> None:
    base = Config.load(RULES, COSTS)
    cfg_off = replace(base, risk_governor=replace(base.risk_governor, enabled=False))
    sessions = business_dates("2020-01-06", 10)
    gov = RiskGovernor(cfg_off, TradingCalendar(sessions))
    for d in sessions[:5]:
        gov.record_exit(_closed(ExitReason.STOP, d))
    assert gov.new_trades_blocked(sessions[6]) is False


# --------------------------------------------------------------------------- #
# ClosedTrade — 손익·R·is_stop
# --------------------------------------------------------------------------- #
def test_closed_trade_pnl_and_r(cfg: Config) -> None:
    ct = ClosedTrade(
        symbol="A", market=Market.KOSPI, tranche_no=1,
        entry_fill=_buy(100.0, 10, cost=15.0, d=date(2020, 1, 6)),
        exit_fill=_sell(120.0, 10, reason=ExitReason.TREND_60MA_REST,
                        cost=35.0, d=date(2020, 2, 6)),
        risk_per_share=10.0,
    )
    # 손익 = (120-100)*10 − 진입비용15 − 청산비용35 = 200 − 50 = 150.
    assert ct.pnl == pytest.approx(150.0)
    assert ct.pnl_r == pytest.approx(150.0 / (10.0 * 10))  # 1.5R
    assert ct.hold_days == 31
    assert ct.is_stop is False
