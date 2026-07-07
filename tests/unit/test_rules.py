"""셋업 게이트 + 시장필터 (Phase 2 DoD).

각 게이트의 boolean 경계 + 시장필터 정상↔경계↔방어 전이 + 복귀 3거래일 +
진입 판정이 D-1을 쓰는지 확인한다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oneil_bt.domain.bar import PriceFrame
from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import MarketState
from oneil_bt.indicators.indicator_set import IndicatorSet
from oneil_bt.rules.market_filter import MarketFilter, build_market_states
from oneil_bt.rules.overheating import OverheatingFilter
from oneil_bt.rules.rs_filter import RsFilter
from oneil_bt.rules.trend_template import TrendTemplateFilter
from tests.fixtures.synthetic import business_dates, ohlcv_frame

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


def _frame(closes, volumes=2_000, *, symbol="TEST", values=None, spread=0.02) -> PriceFrame:
    dates = business_dates("2020-01-01", len(closes))
    return PriceFrame(symbol, ohlcv_frame(dates, closes, volumes, values=values, spread=spread))


def _iset(cfg: Config, stock: PriceFrame, index: PriceFrame) -> IndicatorSet:
    return IndicatorSet(stock, index, cfg)


# --------------------------------------------------------------------------- #
# 트렌드 템플릿
# --------------------------------------------------------------------------- #
def _healthy_trend(cfg: Config, n: int = 300, turnover: float = 2.0e10):
    closes = list(np.linspace(100.0, 400.0, n))
    stock = _frame(closes, values=[turnover] * n)
    index = _frame(list(np.linspace(100.0, 200.0, n)), symbol="KOSPI")
    ind = _iset(cfg, stock, index)
    return TrendTemplateFilter(stock, ind, cfg), ind


def test_trend_template_passes_healthy_uptrend(cfg: Config) -> None:
    tt, ind = _healthy_trend(cfg)
    d = ind.index[-1].date()
    assert tt.passes(d) is True


def test_trend_template_fails_downtrend(cfg: Config) -> None:
    closes = list(np.linspace(400.0, 100.0, 300))
    stock = _frame(closes, values=[2.0e10] * 300)
    index = _frame(list(np.linspace(200.0, 100.0, 300)), symbol="KOSPI")
    ind = _iset(cfg, stock, index)
    tt = TrendTemplateFilter(stock, ind, cfg)
    assert tt.passes(ind.index[-1].date()) is False


def test_trend_template_turnover_boundary(cfg: Config) -> None:
    # 정확히 임계(1e10)면 통과(>=), 그 아래면 탈락.
    tt_ok, ind_ok = _healthy_trend(cfg, turnover=1.0e10)
    assert tt_ok.passes(ind_ok.index[-1].date()) is True
    tt_no, ind_no = _healthy_trend(cfg, turnover=0.999e10)
    assert tt_no.passes(ind_no.index[-1].date()) is False


def test_trend_template_fails_insufficient_history(cfg: Config) -> None:
    tt, ind = _healthy_trend(cfg)
    early = ind.index[10].date()  # 장기 MA 아직 NaN
    assert tt.passes(early) is False


def test_trend_template_fails_when_close_below_ma(cfg: Config) -> None:
    # 우상향 뒤 마지막 종가만 급락 → 주가>150/200MA 조건 탈락.
    closes = list(np.linspace(100.0, 400.0, 299)) + [180.0]
    stock = _frame(closes, values=[2.0e10] * 300)
    index = _frame(list(np.linspace(100.0, 200.0, 300)), symbol="KOSPI")
    ind = _iset(cfg, stock, index)
    tt = TrendTemplateFilter(stock, ind, cfg)
    assert tt.passes(ind.index[-1].date()) is False


# --------------------------------------------------------------------------- #
# 과열 제외
# --------------------------------------------------------------------------- #
def _overheat(cfg: Config, closes) -> OverheatingFilter:
    stock = _frame(closes)
    index = _frame([100.0] * len(closes), symbol="KOSPI")
    ind = _iset(cfg, stock, index)
    return OverheatingFilter(ind, cfg)


def test_overheating_spike_boundary(cfg: Config) -> None:
    # 20일 수익률 정확히 +50%면 과열(>=), 그 아래면 아님.
    of_hit = _overheat(cfg, [100.0] * 20 + [150.0])
    d = business_dates("2020-01-01", 21)[-1]
    assert of_hit.vertical_spike(d) is True
    of_miss = _overheat(cfg, [100.0] * 20 + [149.0])
    assert of_miss.vertical_spike(d) is False


def test_overheating_excluded_on_spike(cfg: Config) -> None:
    of = _overheat(cfg, [100.0] * 20 + [160.0])
    d = business_dates("2020-01-01", 21)[-1]
    assert of.excluded(d) is True


def test_overheating_not_excluded_when_base_present(cfg: Config) -> None:
    # require_no_base=True: 유효 베이스가 있으면 수직상승이라도 제외 안 함.
    of = _overheat(cfg, [100.0] * 20 + [160.0])
    d = business_dates("2020-01-01", 21)[-1]
    assert of.excluded(d, has_base=True) is False


def test_overheating_not_excluded_without_spike(cfg: Config) -> None:
    of = _overheat(cfg, [100.0] * 20 + [110.0])  # +10%
    d = business_dates("2020-01-01", 21)[-1]
    assert of.excluded(d) is False


# --------------------------------------------------------------------------- #
# 상대강도
# --------------------------------------------------------------------------- #
def _rs(cfg: Config, stock_closes, index_closes) -> RsFilter:
    stock = _frame(stock_closes)
    index = _frame(index_closes, symbol="KOSPI")
    ind = _iset(cfg, stock, index)
    return RsFilter(ind, cfg)


def test_rs_passes_when_stock_beats_index(cfg: Config) -> None:
    rs = _rs(cfg, list(np.linspace(100.0, 300.0, 200)), list(np.linspace(100.0, 150.0, 200)))
    d = business_dates("2020-01-01", 200)[-1]
    assert rs.passes(d) is True


def test_rs_fails_when_stock_lags_index(cfg: Config) -> None:
    rs = _rs(cfg, list(np.linspace(100.0, 150.0, 200)), list(np.linspace(100.0, 300.0, 200)))
    d = business_dates("2020-01-01", 200)[-1]
    assert rs.passes(d) is False


def test_rs_fails_insufficient_history(cfg: Config) -> None:
    rs = _rs(cfg, list(np.linspace(100.0, 300.0, 200)), list(np.linspace(100.0, 150.0, 200)))
    early = business_dates("2020-01-01", 200)[10]  # 126일 룩백 미충족
    assert rs.passes(early) is False


# --------------------------------------------------------------------------- #
# 시장필터 — 상태머신 (복귀 3거래일 히스테리시스)
# --------------------------------------------------------------------------- #
def test_build_market_states_hysteresis() -> None:
    dates = business_dates("2021-01-01", 10)
    idx = pd.DatetimeIndex(pd.to_datetime(dates).normalize())
    entry_ma = pd.Series([100.0] * 10, index=idx)   # 60일선 대용
    defense_ma = pd.Series([50.0] * 10, index=idx)   # 120일선 대용
    close = pd.Series([120, 120, 120, 120, 90, 120, 120, 120, 40, 120], index=idx, dtype=float)
    states = build_market_states(close, entry_ma, defense_ma, recover_days=3)
    expected = [
        MarketState.CAUTION,  # streak 1
        MarketState.CAUTION,  # streak 2
        MarketState.NORMAL,   # streak 3 → 복귀
        MarketState.NORMAL,   # streak 4
        MarketState.CAUTION,  # <60일선 → 리셋
        MarketState.CAUTION,  # 복귀 streak 1
        MarketState.CAUTION,  # streak 2
        MarketState.NORMAL,   # streak 3 → 복귀
        MarketState.DEFENSE,  # <120일선
        MarketState.CAUTION,  # 방어 후 streak 1
    ]
    assert list(states) == expected


def test_build_market_states_warmup_is_defense() -> None:
    dates = business_dates("2021-01-01", 3)
    idx = pd.DatetimeIndex(pd.to_datetime(dates).normalize())
    close = pd.Series([120.0, 120.0, 120.0], index=idx)
    entry_ma = pd.Series([np.nan, 100.0, 100.0], index=idx)
    defense_ma = pd.Series([np.nan, 50.0, 50.0], index=idx)
    states = build_market_states(close, entry_ma, defense_ma, recover_days=1)
    assert states.iloc[0] == MarketState.DEFENSE  # MA 미확정
    assert states.iloc[1] == MarketState.NORMAL   # recover_days=1


# --------------------------------------------------------------------------- #
# 시장필터 — 통합 + as-of/전일 타이밍
# --------------------------------------------------------------------------- #
def _market_filter(cfg: Config, closes) -> MarketFilter:
    index = _frame(closes, symbol="KOSPI")
    ind = _iset(cfg, index, index)
    return MarketFilter(index, ind, cfg)


def test_market_state_normal_in_uptrend(cfg: Config) -> None:
    mf = _market_filter(cfg, list(np.linspace(100.0, 300.0, 200)))
    d = business_dates("2020-01-01", 200)[-1]
    assert mf.state_asof(d) == MarketState.NORMAL


def test_market_state_defense_in_downtrend(cfg: Config) -> None:
    mf = _market_filter(cfg, list(np.linspace(300.0, 100.0, 200)))
    d = business_dates("2020-01-01", 200)[-1]
    assert mf.state_asof(d) == MarketState.DEFENSE


def _timing_filter(cfg: Config) -> tuple[MarketFilter, list[date]]:
    """상태 시리즈를 손으로 주입해 as-of/전일 타이밍 산술만 검증한다."""
    mf = _market_filter(cfg, list(np.linspace(100.0, 300.0, 200)))
    dates = business_dates("2021-06-01", 5)
    idx = pd.DatetimeIndex(pd.to_datetime(dates).normalize())
    mf._index = idx
    mf._state = pd.Series(
        [MarketState.CAUTION, MarketState.NORMAL, MarketState.NORMAL,
         MarketState.DEFENSE, MarketState.DEFENSE],
        index=idx, dtype=object,
    )
    return mf, dates


def test_market_state_asof_picks_last_session(cfg: Config) -> None:
    mf, dates = _timing_filter(cfg)
    assert mf.state_asof(dates[1]) == MarketState.NORMAL
    # 비거래일(주말)은 직전 세션 상태
    assert mf.state_asof(date(2021, 6, 6)) == mf.state_asof(dates[4])


def test_new_entry_allowed_uses_prev_day(cfg: Config) -> None:
    mf, dates = _timing_filter(cfg)
    # dates[2] 진입 판정 → 전일 dates[1]=NORMAL → 허용
    assert mf.new_entry_allowed(dates[2]) is True
    # dates[1] 진입 판정 → 전일 dates[0]=CAUTION → 불허 (당일이 NORMAL이어도)
    assert mf.new_entry_allowed(dates[1]) is False


def test_defense_triggered_only_on_break_day(cfg: Config) -> None:
    mf, dates = _timing_filter(cfg)
    assert mf.defense_triggered_on(dates[3]) is True   # NORMAL→DEFENSE 전이일
    assert mf.defense_triggered_on(dates[4]) is False  # 이미 방어 지속
    assert mf.defense_triggered_on(dates[2]) is False  # 방어 아님
