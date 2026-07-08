"""손절 규칙 (Phase 4B DoD).

손절가 산출(2×ATR·-10% 캡·평단 갱신 재계산)과 발동 판정(종가확정/장중 모델),
그리고 청산 체결(D+1 시가 / 갭하락 대안 모델)을 검증한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import ExitReason, FillModelType, Market, StopMethod
from oneil_bt.domain.trade import Position
from oneil_bt.execution.cost_model import CostModel
from oneil_bt.execution.fill_model import DailyBarFillModel
from oneil_bt.execution.orders import Order
from oneil_bt.rules.stop_rule import StopRule
from tests.fixtures.synthetic import business_dates, ohlcv_frame, price_frame

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


def _pos(avg: float, stop: float, qty: int = 10) -> Position:
    return Position(
        symbol="TEST",
        market=Market.KOSPI,
        entry_date=date(2020, 1, 6),
        entry_price=avg,
        avg_price=avg,
        qty=qty,
        stop_price=stop,
    )


def _prices(closes: list[float], lows: list[float] | None = None):
    dates = business_dates("2020-01-06", len(closes))
    df = ohlcv_frame(dates, closes)
    if lows is not None:
        df["low"] = lows
    return price_frame("TEST", df), dates


# --------------------------------------------------------------------------- #
# stop_price — 2×ATR 및 -10% 캡
# --------------------------------------------------------------------------- #
def test_stop_price_uses_2atr_when_within_cap(cfg: Config) -> None:
    frame, _ = _prices([100.0])
    sr = StopRule(frame, cfg)
    # 2×ATR = 7 → 손절폭 7% < 10% 캡 → 100 - 7 = 93.
    assert sr.stop_price(100.0, 3.5) == pytest.approx(93.0)


def test_stop_price_clamps_at_minus_10pct_cap(cfg: Config) -> None:
    frame, _ = _prices([100.0])
    sr = StopRule(frame, cfg)
    # 2×ATR = 16 → 손절폭 16% > 10% → 캡 바닥 90 으로 클램프.
    assert sr.stop_price(100.0, 8.0) == pytest.approx(90.0)


def test_stop_price_recomputes_after_avg_rises(cfg: Config) -> None:
    frame, _ = _prices([100.0])
    sr = StopRule(frame, cfg)
    # 평단 100 → 손절 93. 피라미딩으로 평단 110 → 손절 103 (같은 ATR).
    assert sr.stop_price(100.0, 3.5) == pytest.approx(93.0)
    assert sr.stop_price(110.0, 3.5) == pytest.approx(103.0)


def test_stop_price_fixed_pct_method(cfg: Config) -> None:
    frame, _ = _prices([100.0])
    fixed_cfg = replace(cfg, stop=replace(cfg.stop, method=StopMethod.FIXED_PCT))
    sr = StopRule(frame, fixed_cfg)
    # 고정 8% → 92. (캡 10% 바닥 90 보다 위이므로 92 유지)
    assert sr.stop_price(100.0, 3.5) == pytest.approx(92.0)


# --------------------------------------------------------------------------- #
# hit — 종가확정(기본) vs 장중(대안)
# --------------------------------------------------------------------------- #
def test_hit_on_close_confirmed_model(cfg: Config) -> None:
    # 종가 92 <= 손절 93 → 발동. (기본 모델은 종가 기준)
    frame, dates = _prices([100.0, 92.0], lows=[98.0, 91.0])
    sr = StopRule(frame, cfg)
    pos = _pos(100.0, 93.0)
    assert sr.hit(pos, dates[1]) is True
    assert sr.hit(pos, dates[0]) is False  # 종가 100 > 93


def test_hit_ignores_intraday_low_in_close_model(cfg: Config) -> None:
    # 장중 저가 91 <= 93 이지만 종가 95 > 93 → 기본 모델은 미발동.
    frame, dates = _prices([100.0, 95.0], lows=[98.0, 91.0])
    sr = StopRule(frame, cfg)
    assert sr.hit(_pos(100.0, 93.0), dates[1]) is False


def test_hit_on_intraday_touch_model(cfg: Config) -> None:
    intraday = replace(
        cfg, stop=replace(cfg.stop, fill_model=FillModelType.INTRADAY_TOUCH)
    )
    # 장중 저가 91 <= 93 → 발동(종가 95 무관).
    frame, dates = _prices([100.0, 95.0], lows=[98.0, 91.0])
    sr = StopRule(frame, intraday)
    assert sr.hit(_pos(100.0, 93.0), dates[1]) is True


def test_hit_false_when_no_bar(cfg: Config) -> None:
    frame, dates = _prices([100.0, 92.0])
    sr = StopRule(frame, cfg)
    # 프레임에 없는 날짜(주말) → 실거래 바 없음 → 판정 보류(False).
    assert sr.hit(_pos(100.0, 93.0), date(2020, 1, 11)) is False


# --------------------------------------------------------------------------- #
# fill_exit — 손절 체결일·체결가
# --------------------------------------------------------------------------- #
def _fill_model(cfg: Config) -> DailyBarFillModel:
    return DailyBarFillModel(CostModel(cfg.cost), cfg)


def _bar(o: float, h: float, low: float, *, d: str = "2020-07-02") -> pd.Series:
    ts = pd.Timestamp(d).normalize()
    return pd.Series(
        {"open": o, "high": h, "low": low, "close": (h + low) / 2, "volume": 1_000.0},
        name=ts,
    )


def test_stop_fill_close_confirmed_uses_next_open(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.exit("TEST", 10, ExitReason.STOP, Market.KOSPI, stop_price=93.0)
    # 기본 모델: D+1 시가 전량. 갭 여부 무관 → 시가 90 체결.
    fill = fm.fill_exit(_bar(90.0, 91.0, 89.0), order)
    assert fill.price == pytest.approx(90.0)
    assert fill.qty == 10
    assert fill.reason is ExitReason.STOP
    assert fill.date == date(2020, 7, 2)


def test_stop_fill_intraday_gap_down_fills_at_open(cfg: Config) -> None:
    intraday = replace(
        cfg, stop=replace(cfg.stop, fill_model=FillModelType.INTRADAY_TOUCH)
    )
    fm = _fill_model(intraday)
    order = Order.exit("TEST", 10, ExitReason.STOP, Market.KOSPI, stop_price=93.0)
    # 갭하락 시가 90 < 손절 93 → min(90, 93) = 90 체결.
    fill = fm.fill_exit(_bar(90.0, 92.0, 88.0), order)
    assert fill.price == pytest.approx(90.0)


def test_stop_fill_intraday_touch_fills_at_stop(cfg: Config) -> None:
    intraday = replace(
        cfg, stop=replace(cfg.stop, fill_model=FillModelType.INTRADAY_TOUCH)
    )
    fm = _fill_model(intraday)
    order = Order.exit("TEST", 10, ExitReason.STOP, Market.KOSPI, stop_price=93.0)
    # 시가 96 > 손절 93, 장중 저가 91 → 손절가 93 체결.
    fill = fm.fill_exit(_bar(96.0, 97.0, 91.0), order)
    assert fill.price == pytest.approx(93.0)


def test_exit_fill_sell_cost_includes_tax(cfg: Config) -> None:
    fm = _fill_model(cfg)
    order = Order.exit("TEST", 10, ExitReason.STOP, Market.KOSPI, stop_price=93.0)
    fill = fm.fill_exit(_bar(90.0, 91.0, 89.0), order)
    # 2020 KOSPI: 편도 6.5bp + 세금 25bp = 31.5bp → 90*10*0.00315 = 2.835.
    assert fill.cost == pytest.approx(90.0 * 10 * 31.5 / 1e4)
