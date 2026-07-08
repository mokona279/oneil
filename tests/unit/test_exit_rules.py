"""청산 규칙 (Phase 4B DoD).

60MA 이탈(절반→3거래일 회복/실패 분기·거래량 급증 전량), 시장 방어(절반, 8주 예외),
8주 룰(fast-gain 보호 → ③ 정지) 를 검증한다. 판정은 종가 기준·룩어헤드 없음(§6.1).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from oneil_bt.domain.bar import PriceFrame
from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import ExitReason, Market, MarketState
from oneil_bt.domain.trade import Position
from oneil_bt.indicators.indicator_set import IndicatorSet
from oneil_bt.rules.exit_rules import (
    EightWeekGuard,
    MarketDefenseRule,
    TrendExitRule,
)
from tests.fixtures.synthetic import business_dates, ohlcv_frame, price_frame

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"

WARMUP = 60  # 60MA 확정에 필요한 최소 세션


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


def _pos(
    *,
    qty: int = 10,
    entry_date: date = date(2020, 1, 6),
    entry_price: float = 100.0,
    trend_break_date: date | None = None,
) -> Position:
    return Position(
        symbol="TEST",
        market=Market.KOSPI,
        entry_date=entry_date,
        entry_price=entry_price,
        avg_price=entry_price,
        qty=qty,
        stop_price=entry_price * 0.93,
        trend_break_date=trend_break_date,
    )


def _trend_rule(
    cfg: Config,
    closes: list[float],
    volumes: list[float] | None = None,
) -> tuple[TrendExitRule, list[date]]:
    dates = business_dates("2020-01-06", len(closes))
    vols = volumes if volumes is not None else [1_000.0] * len(closes)
    df = ohlcv_frame(dates, closes, vols)
    frame = price_frame("TEST", df)
    index = PriceFrame("KOSPI", ohlcv_frame(dates, 100.0))
    ind = IndicatorSet(frame, index, cfg)
    return TrendExitRule(frame, ind, cfg), dates


# --------------------------------------------------------------------------- #
# TrendExitRule — 60MA 이탈 → 절반
# --------------------------------------------------------------------------- #
def test_trend_break_triggers_half(cfg: Config) -> None:
    closes = [100.0] * WARMUP + [90.0]  # 마지막 날 종가 90 < 60MA(≈100)
    rule, dates = _trend_rule(cfg, closes)
    sig = rule.evaluate(_pos(qty=10), dates[-1])
    assert sig is not None
    assert sig.reason is ExitReason.TREND_60MA_HALF
    assert sig.qty == 5  # floor(10 * 0.5)
    assert sig.is_sell is True


def test_no_signal_while_above_ma(cfg: Config) -> None:
    closes = [100.0] * (WARMUP + 1)
    rule, dates = _trend_rule(cfg, closes)
    assert rule.evaluate(_pos(), dates[-1]) is None


# --------------------------------------------------------------------------- #
# TrendExitRule — 3거래일 회복 실패 → 잔량, 회복 → 취소
# --------------------------------------------------------------------------- #
def test_trend_rest_after_three_sessions_without_recovery(cfg: Config) -> None:
    # break 이후 3거래일 연속 미회복 → 3번째 날 잔량 전량.
    closes = [100.0] * WARMUP + [90.0, 90.0, 90.0, 90.0]
    rule, dates = _trend_rule(cfg, closes)
    break_day = dates[WARMUP]
    pos = _pos(qty=5, trend_break_date=break_day)  # 절반 매도 후 잔량 5
    # break+1, break+2 → 아직 카운트다운(미도달).
    assert rule.evaluate(pos, dates[WARMUP + 1]) is None
    assert rule.evaluate(pos, dates[WARMUP + 2]) is None
    # break+3 → 3거래일 경과, 미회복 → 잔량 전량.
    sig = rule.evaluate(pos, dates[WARMUP + 3])
    assert sig is not None
    assert sig.reason is ExitReason.TREND_60MA_REST
    assert sig.qty == 5


def test_trend_recovery_clears_pending_exit(cfg: Config) -> None:
    # break 다음날 종가가 60MA 위로 회복 → 잔량 청산 취소(reason=None).
    closes = [100.0] * WARMUP + [90.0, 100.0]
    rule, dates = _trend_rule(cfg, closes)
    pos = _pos(qty=5, trend_break_date=dates[WARMUP])
    sig = rule.evaluate(pos, dates[WARMUP + 1])
    assert sig is not None
    assert sig.reason is None
    assert sig.is_sell is False


# --------------------------------------------------------------------------- #
# TrendExitRule — 거래량 급증 이탈 → 전량 (옵션)
# --------------------------------------------------------------------------- #
def test_volbreak_full_sells_all_on_volume_surge(cfg: Config) -> None:
    volbreak_cfg = replace(cfg, exit=replace(cfg.exit, volbreak_full=True))
    closes = [100.0] * WARMUP + [90.0]
    vols = [1_000.0] * WARMUP + [5_000.0]  # 20일평균(≈1200)×2 초과
    rule, dates = _trend_rule(volbreak_cfg, closes, vols)
    sig = rule.evaluate(_pos(qty=10), dates[-1])
    assert sig is not None
    assert sig.reason is ExitReason.TREND_60MA_VOLBREAK
    assert sig.qty == 10  # 전량


# --------------------------------------------------------------------------- #
# EightWeekGuard — 돌파 후 3주 내 +20% → 8주 보호
# --------------------------------------------------------------------------- #
def _guard(cfg: Config, closes: list[float]) -> tuple[EightWeekGuard, list[date]]:
    dates = business_dates("2020-01-06", len(closes))
    frame = price_frame("TEST", ohlcv_frame(dates, closes))
    return EightWeekGuard(frame, cfg), dates


def test_eight_week_protected_after_fast_gain(cfg: Config) -> None:
    # 진입가 100, 5거래일 뒤 종가 125 → high(≈127.5) ≥ 120 달성 → 보호.
    closes = [100.0, 105.0, 110.0, 118.0, 125.0] + [125.0] * 40
    guard, dates = _guard(cfg, closes)
    pos = _pos(entry_date=dates[0], entry_price=100.0)
    # 진입 후 30일차(8주 이내) → 보호 유지.
    assert guard.protected(pos, dates[30]) is True


def test_eight_week_not_protected_without_fast_gain(cfg: Config) -> None:
    # +20% 미달(최고 115) → 보호 없음.
    closes = [100.0, 105.0, 110.0, 112.0, 113.0] + [113.0] * 40
    guard, dates = _guard(cfg, closes)
    pos = _pos(entry_date=dates[0], entry_price=100.0)
    assert guard.protected(pos, dates[30]) is False


def test_eight_week_expires_after_min_hold(cfg: Config) -> None:
    closes = [100.0, 105.0, 110.0, 118.0, 125.0] + [125.0] * 60
    guard, dates = _guard(cfg, closes)
    pos = _pos(entry_date=dates[0], entry_price=100.0)
    # fast-gain 달성(4일차 +25%) 이후 & 8주 이내 → 보호.
    assert guard.protected(pos, dates[5]) is True
    # 60거래일 뒤(달력일 > 56) → 최소보유 경과 → 해제.
    assert (dates[59] - dates[0]).days >= cfg.exit.eight_week.min_hold_days
    assert guard.protected(pos, dates[59]) is False


# --------------------------------------------------------------------------- #
# MarketDefenseRule — 방어 절반, 8주 룰 예외
# --------------------------------------------------------------------------- #
def test_market_defense_halves_unprotected(cfg: Config) -> None:
    # fast-gain 없는 종목 → 보호 안 됨 → DEFENSE에서 절반.
    closes = [100.0] * 45
    guard, dates = _guard(cfg, closes)
    rule = MarketDefenseRule(guard, cfg)
    pos = _pos(qty=10, entry_date=dates[0], entry_price=100.0)
    sig = rule.evaluate(pos, dates[30], MarketState.DEFENSE)
    assert sig is not None
    assert sig.reason is ExitReason.MARKET_DEFENSE_120MA
    assert sig.qty == 5


def test_market_defense_skips_protected_position(cfg: Config) -> None:
    # 8주 룰 보호 종목은 ③ 정지 → None.
    closes = [100.0, 105.0, 110.0, 118.0, 125.0] + [125.0] * 40
    guard, dates = _guard(cfg, closes)
    rule = MarketDefenseRule(guard, cfg)
    pos = _pos(qty=10, entry_date=dates[0], entry_price=100.0)
    assert guard.protected(pos, dates[30]) is True  # 전제 확인
    assert rule.evaluate(pos, dates[30], MarketState.DEFENSE) is None


def test_market_defense_no_signal_when_not_defense(cfg: Config) -> None:
    closes = [100.0] * 45
    guard, dates = _guard(cfg, closes)
    rule = MarketDefenseRule(guard, cfg)
    pos = _pos(qty=10, entry_date=dates[0], entry_price=100.0)
    assert rule.evaluate(pos, dates[30], MarketState.NORMAL) is None
    assert rule.evaluate(pos, dates[30], MarketState.CAUTION) is None
