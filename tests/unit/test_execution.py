"""체결 프리미티브 (Phase 4A DoD).

§6.2 체결 가정표 전 케이스 — 정상 돌파, 갭업 추격한도 내/초과 미체결, 트랜치 트리거·
상한·갭 스킵, 거래량 게이트, 매수/매도 비용(시장·기간별 세금)을 검증한다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import EntryReason, Market, OrderKind
from oneil_bt.execution.cost_model import CostModel
from oneil_bt.execution.fill_model import DailyBarFillModel
from oneil_bt.execution.orders import Order

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"

# costs.yaml 초안값: commission 1.5bp + slippage 5bp = 편도 6.5bp.
ONE_WAY_BP = 6.5


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


def _bar(
    o: float,
    h: float,
    low: float,
    *,
    v: float = 1_000.0,
    d: str = "2020-07-01",
) -> pd.Series:
    ts = pd.Timestamp(d).normalize()
    return pd.Series(
        {"open": o, "high": h, "low": low, "close": (h + low) / 2, "volume": v},
        name=ts,
    )


# --------------------------------------------------------------------------- #
# CostModel — 매수/매도 비용, 세금 스케줄
# --------------------------------------------------------------------------- #
def test_buy_cost_has_no_tax(cfg: Config) -> None:
    cm = CostModel(cfg.cost)
    # 100 × 10 × 6.5bp = 1000 × 0.00065 = 0.65
    assert cm.buy_cost(100.0, 10, date(2020, 7, 1)) == pytest.approx(0.65)


def test_sell_cost_adds_tax_by_period(cfg: Config) -> None:
    cm = CostModel(cfg.cost)
    # 2020: 세금 25bp(2019-06-03 계단) + 편도 6.5bp = 31.5bp → 1000 × 0.00315 = 3.15
    assert cm.sell_cost(100.0, 10, date(2020, 7, 1), Market.KOSPI) == pytest.approx(3.15)
    # 2024: 세금 18bp → 24.5bp → 2.45
    assert cm.sell_cost(100.0, 10, date(2024, 6, 1), Market.KOSPI) == pytest.approx(2.45)


def test_sell_tax_tier_boundary_is_inclusive(cfg: Config) -> None:
    cm = CostModel(cfg.cost)
    # from_date(2019-06-03) 당일은 새 계단(25bp) 적용, 전날은 이전 계단(30bp).
    on_tier = cm.sell_cost(100.0, 10, date(2019, 6, 3), Market.KOSDAQ)
    before_tier = cm.sell_cost(100.0, 10, date(2019, 6, 2), Market.KOSDAQ)
    assert on_tier == pytest.approx((ONE_WAY_BP + 25) / 1e4 * 1000)
    assert before_tier == pytest.approx((ONE_WAY_BP + 30) / 1e4 * 1000)


def test_sell_tax_before_first_tier_falls_back_to_earliest(cfg: Config) -> None:
    cm = CostModel(cfg.cost)
    # 최초 시행일(2000-01-01)보다 이른 날짜 → 가장 이른 계단(30bp) 방어 적용.
    cost = cm.sell_cost(100.0, 10, date(1999, 1, 1), Market.KOSPI)
    assert cost == pytest.approx((ONE_WAY_BP + 30) / 1e4 * 1000)


# --------------------------------------------------------------------------- #
# Order 팩토리 — 상한 계산
# --------------------------------------------------------------------------- #
def test_breakout_order_caps_at_chase_limit(cfg: Config) -> None:
    o = Order.breakout("TEST", pivot=100.0, qty=10, chase_limit_pct=cfg.entry.chase_limit_pct)
    assert o.kind is OrderKind.STOP_BUY
    assert o.reason is EntryReason.BREAKOUT_T1
    assert o.trigger == pytest.approx(100.0)
    assert o.limit_cap == pytest.approx(105.0)  # +5%


def test_pyramid_order_caps_at_tranche_limit(cfg: Config) -> None:
    o = Order.pyramid(
        "TEST",
        trigger_price=102.5,
        qty=5,
        cap_pct=cfg.entry.tranche_price_cap_pct,
        reason=EntryReason.PYRAMID_T2,
    )
    assert o.kind is OrderKind.LIMIT_BUY
    assert o.trigger == pytest.approx(102.5)
    assert o.limit_cap == pytest.approx(102.5 * 1.03)  # +3%


# --------------------------------------------------------------------------- #
# fill_entry — 1차 돌파
# --------------------------------------------------------------------------- #
def _fill_model(cfg: Config) -> DailyBarFillModel:
    return DailyBarFillModel(CostModel(cfg.cost), cfg)


def test_entry_normal_breakout_fills_at_pivot(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.breakout("TEST", 100.0, 10, 5.0)
    fill = fm.fill_entry(_bar(98.0, 101.0, 97.0), order)
    assert fill is not None
    assert fill.price == pytest.approx(100.0)  # max(O=98, P=100)
    assert fill.qty == 10
    assert fill.reason is EntryReason.BREAKOUT_T1
    assert fill.date == date(2020, 7, 1)
    assert fill.cost == pytest.approx(100.0 * 10 * ONE_WAY_BP / 1e4)


def test_entry_gap_up_within_chase_fills_at_open(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.breakout("TEST", 100.0, 10, 5.0)  # cap = 105
    fill = fm.fill_entry(_bar(103.0, 104.0, 102.0), order)
    assert fill is not None
    assert fill.price == pytest.approx(103.0)  # 갭업이 상한 이내 → 시가 체결


def test_entry_gap_above_chase_with_pullback_fills_at_cap(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.breakout("TEST", 100.0, 10, 5.0)  # cap = 105
    # 갭업 110 > cap, 장중 저가 104 <= cap → 상한(105) 체결.
    fill = fm.fill_entry(_bar(110.0, 112.0, 104.0), order)
    assert fill is not None
    assert fill.price == pytest.approx(105.0)


def test_entry_gap_above_chase_no_pullback_is_unfilled(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.breakout("TEST", 100.0, 10, 5.0)  # cap = 105
    # 갭업 110 > cap, 장중 저가 106 > cap → 추격 한도 초과 미체결.
    assert fm.fill_entry(_bar(110.0, 112.0, 106.0), order) is None


def test_entry_no_breakout_is_unfilled(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.breakout("TEST", 100.0, 10, 5.0)
    assert fm.fill_entry(_bar(98.0, 99.5, 97.0), order) is None  # H < pivot


# --------------------------------------------------------------------------- #
# fill_pyramid — 2·3차 트랜치
# --------------------------------------------------------------------------- #
def test_pyramid_trigger_hit_fills_at_trigger(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.pyramid("TEST", 102.5, 5, 3.0, EntryReason.PYRAMID_T2)
    fill = fm.fill_pyramid(_bar(101.0, 103.0, 100.0), order)
    assert fill is not None
    assert fill.price == pytest.approx(102.5)  # max(O=101, trigger=102.5)
    assert fill.reason is EntryReason.PYRAMID_T2


def test_pyramid_gap_within_cap_fills_at_open(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.pyramid("TEST", 102.5, 5, 3.0, EntryReason.PYRAMID_T3)  # cap ≈ 105.575
    fill = fm.fill_pyramid(_bar(104.0, 105.0, 103.0), order)
    assert fill is not None
    assert fill.price == pytest.approx(104.0)


def test_pyramid_gap_above_cap_is_skipped(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.pyramid("TEST", 102.5, 5, 3.0, EntryReason.PYRAMID_T2)  # cap ≈ 105.575
    # 시가 106 > cap → 그 회차 스킵(장중 복귀 허용 안 함).
    assert fm.fill_pyramid(_bar(106.0, 107.0, 105.0), order) is None


def test_pyramid_trigger_not_reached_is_unfilled(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.pyramid("TEST", 102.5, 5, 3.0, EntryReason.PYRAMID_T2)
    assert fm.fill_pyramid(_bar(100.0, 101.0, 99.0), order) is None  # H < trigger


# --------------------------------------------------------------------------- #
# 거래량 게이트 — 2·3차 예약 여부
# --------------------------------------------------------------------------- #
def test_volume_gate_boundary_and_fail(cfg: Config) -> None:
    fm = _fill_model(cfg)  # breakout_volume_mult = 1.5
    assert fm.volume_confirmed(1_500.0, 1_000.0) is True   # 정확히 1.5배 → 통과
    assert fm.volume_confirmed(1_499.0, 1_000.0) is False  # 미달
    assert fm.volume_confirmed(2_000.0, 1_000.0) is True


def test_volume_gate_missing_average_fails(cfg: Config) -> None:
    fm = _fill_model(cfg)
    assert fm.volume_confirmed(9_999.0, None) is False
    assert fm.volume_confirmed(9_999.0, float("nan")) is False
    assert fm.volume_confirmed(9_999.0, 0.0) is False


# --------------------------------------------------------------------------- #
# 정수주 — 체결 수량은 주문 수량 그대로(사이저가 floor 정수주로 넘긴다)
# --------------------------------------------------------------------------- #
def test_fill_qty_is_integer_from_order(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.breakout("TEST", 100.0, 7, 5.0)
    fill = fm.fill_entry(_bar(100.0, 101.0, 99.0), order)
    assert fill is not None
    assert isinstance(fill.qty, int)
    assert fill.qty == 7
