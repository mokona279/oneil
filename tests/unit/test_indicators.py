"""지표 레이어 (Phase 1 DoD).

손계산 대조 + True Range 갭 경계 + 52주 창 길이 + RS 정의 + 룩어헤드 회귀.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oneil_bt.domain.bar import PriceFrame
from oneil_bt.domain.config import Config
from oneil_bt.indicators.atr import AverageTrueRange, true_range
from oneil_bt.indicators.base import asof_value
from oneil_bt.indicators.indicator_set import IndicatorSet
from oneil_bt.indicators.moving_average import MovingAverage
from oneil_bt.indicators.relative_strength import RelativeStrength
from oneil_bt.indicators.rolling_extremes import RollingHigh, RollingLow
from tests.fixtures.synthetic import business_dates, ohlcv_frame

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


def _frame(closes, volumes=1_000, *, symbol="TEST", values=None, spread=0.02) -> PriceFrame:
    dates = business_dates("2020-01-01", len(closes))
    return PriceFrame(symbol, ohlcv_frame(dates, closes, volumes, values=values, spread=spread))


# --------------------------------------------------------------------------- #
# 이동평균
# --------------------------------------------------------------------------- #
def test_ma_known_values_and_warmup() -> None:
    closes = [10.0, 20.0, 30.0, 40.0, 50.0]
    ma = MovingAverage(3).compute(_frame(closes))
    assert ma.iloc[0] != ma.iloc[0]  # NaN (창 미충족)
    assert np.isnan(ma.iloc[1])
    assert ma.iloc[2] == pytest.approx(20.0)  # (10+20+30)/3
    assert ma.iloc[3] == pytest.approx(30.0)  # (20+30+40)/3
    assert ma.iloc[4] == pytest.approx(40.0)  # (30+40+50)/3
    assert ma.name == "ma3"


def test_ma_uses_only_past() -> None:
    ma = MovingAverage(2).compute(_frame([10.0, 12.0, 14.0]))
    # 값@D2 = (12+14)/2, D0 데이터를 포함하지 않는다.
    assert ma.iloc[2] == pytest.approx(13.0)


# --------------------------------------------------------------------------- #
# ATR / True Range
# --------------------------------------------------------------------------- #
def test_true_range_first_bar_is_high_low() -> None:
    # spread=0 이라 high=low=close → TR[0]=0
    df = ohlcv_frame(business_dates("2020-01-01", 2), [100.0, 110.0], spread=0.0)
    tr = true_range(df)
    assert tr.iloc[0] == pytest.approx(0.0)  # 첫 바 high-low
    # 둘째 바: high=low=close=110, prev_close=100 → max(0, |110-100|, |110-100|)=10
    assert tr.iloc[1] == pytest.approx(10.0)


def test_true_range_gap_down_uses_prev_close() -> None:
    # 명시적 OHLC로 갭다운 구성
    idx = pd.DatetimeIndex(pd.to_datetime(business_dates("2020-01-01", 2)).normalize(), name="date")
    df = pd.DataFrame(
        {
            "open": [100.0, 80.0],
            "high": [102.0, 85.0],
            "low": [98.0, 78.0],
            "close": [100.0, 82.0],
            "volume": [1_000.0, 1_000.0],
        },
        index=idx,
    )
    tr = true_range(df)
    # 둘째 바: high-low=7, |85-100|=15, |78-100|=22 → 22
    assert tr.iloc[1] == pytest.approx(22.0)


def test_atr_is_sma_of_true_range() -> None:
    closes = [100.0, 110.0, 120.0]
    frame = _frame(closes, spread=0.0)
    tr = true_range(frame.df)
    atr = AverageTrueRange(3).compute(frame)
    assert np.isnan(atr.iloc[1])
    assert atr.iloc[2] == pytest.approx(tr.iloc[:3].mean())
    assert atr.name == "atr3"


# --------------------------------------------------------------------------- #
# 52주 고저
# --------------------------------------------------------------------------- #
def test_rolling_extremes_window_and_values() -> None:
    closes = [10.0, 30.0, 20.0, 5.0, 25.0]
    frame = _frame(closes, spread=0.1)  # high=close*1.1, low=close*0.9
    hi = RollingHigh(3).compute(frame)
    lo = RollingLow(3).compute(frame)
    assert np.isnan(hi.iloc[1])  # 창 미충족
    # 창3@idx2 = max(high[0..2]) = max(11, 33, 22) = 33
    assert hi.iloc[2] == pytest.approx(33.0)
    # 창3@idx4 = max(high[2..4]) = max(22, 5.5, 27.5) = 27.5
    assert hi.iloc[4] == pytest.approx(27.5)
    # low 창3@idx4 = min(low[2..4]) = min(18, 4.5, 22.5) = 4.5
    assert lo.iloc[4] == pytest.approx(4.5)


def test_rolling_high_uses_intraday_high_not_close() -> None:
    frame = _frame([10.0, 10.0, 10.0], spread=0.2)  # high=12
    hi = RollingHigh(2).compute(frame)
    assert hi.iloc[2] == pytest.approx(12.0)  # 종가 10이 아닌 장중 고가 12


# --------------------------------------------------------------------------- #
# 상대강도
# --------------------------------------------------------------------------- #
def test_rs_return_diff() -> None:
    stock = _frame([100.0, 100.0, 130.0])       # 2일 수익률 +30%
    index = _frame([100.0, 100.0, 110.0], symbol="KOSPI")  # 지수 +10%
    rs = RelativeStrength(2, "return_diff").compute(stock, index)
    assert rs.iloc[2] == pytest.approx(0.30 - 0.10)
    assert rs.name == "rs_2"


def test_rs_rejects_unknown_method() -> None:
    with pytest.raises(ValueError):
        RelativeStrength(2, "ratio")


def test_rs_index_aligned_to_stock_dates() -> None:
    # 지수에 여분 날짜가 있어도 종목 날짜에 정렬되어야 한다.
    stock = _frame([100.0, 100.0, 120.0])
    idx_dates = business_dates("2020-01-01", 4)  # 하루 더 김
    index = PriceFrame("KOSPI", ohlcv_frame(idx_dates, [100.0, 100.0, 110.0, 200.0]))
    rs = RelativeStrength(2, "return_diff").compute(stock, index)
    assert len(rs) == 3  # 종목 길이에 맞춰짐
    assert rs.iloc[2] == pytest.approx(0.20 - 0.10)


# --------------------------------------------------------------------------- #
# asof 유틸
# --------------------------------------------------------------------------- #
def test_asof_value_picks_last_le() -> None:
    frame = _frame([10.0, 20.0, 30.0])
    s = frame.df["close"]
    from datetime import date

    assert asof_value(s, date(2020, 1, 2)) == pytest.approx(20.0)  # 2020-01-02
    # 주말(비거래일)은 직전 거래일 값
    assert asof_value(s, date(2020, 1, 4)) == pytest.approx(30.0)  # 토요일 → 금요일
    # 시작 이전이면 None
    assert asof_value(s, date(2019, 12, 1)) is None


def test_asof_value_nan_returns_none() -> None:
    ma = MovingAverage(3).compute(_frame([10.0, 20.0, 30.0]))
    from datetime import date

    assert asof_value(ma, date(2020, 1, 1)) is None  # 창 미충족 → NaN → None


# --------------------------------------------------------------------------- #
# IndicatorSet 통합
# --------------------------------------------------------------------------- #
def _long_set(cfg: Config, n: int = 300) -> IndicatorSet:
    closes = list(np.linspace(100.0, 400.0, n))  # 우상향
    stock = _frame(closes, volumes=2_000)
    index = _frame(list(np.linspace(100.0, 200.0, n)), symbol="KOSPI")
    return IndicatorSet(stock, index, cfg)


def test_indicator_set_fields_present_and_indexed(cfg: Config) -> None:
    iset = _long_set(cfg)
    for name in ("ma50", "ma60", "ma120", "ma150", "ma200", "atr14",
                 "high_52w", "low_52w", "turnover_20d", "vol_ma20",
                 "ret_20d", "rs_6m"):
        s = getattr(iset, name)
        assert isinstance(s, pd.Series)
        assert s.index.equals(iset.index)
    # 200MA는 200바째부터 값이 생긴다.
    assert np.isnan(iset.ma200.iloc[198])
    assert not np.isnan(iset.ma200.iloc[199])


def test_turnover_prefers_value_column(cfg: Config) -> None:
    closes = [100.0] * 25
    values = [7.0] * 25  # 명시적 거래대금
    stock = _frame(closes, volumes=1_000, values=values)
    index = _frame([100.0] * 25, symbol="KOSPI")
    iset = IndicatorSet(stock, index, cfg)
    # value 컬럼(7)을 쓰고 close*volume(100000)을 쓰지 않는다.
    assert iset.turnover_20d.iloc[24] == pytest.approx(7.0)


def test_turnover_falls_back_to_close_volume(cfg: Config) -> None:
    stock = _frame([100.0] * 25, volumes=1_000)  # value 없음
    index = _frame([100.0] * 25, symbol="KOSPI")
    iset = IndicatorSet(stock, index, cfg)
    assert iset.turnover_20d.iloc[24] == pytest.approx(100_000.0)


def test_ma200_rising_true_when_uptrend(cfg: Config) -> None:
    iset = _long_set(cfg)
    d = iset.index[-1].date()
    assert iset.ma200_rising(d) is True


def test_ma200_rising_false_when_downtrend(cfg: Config) -> None:
    closes = list(np.linspace(400.0, 100.0, 300))  # 우하향
    stock = _frame(closes)
    index = _frame(list(np.linspace(200.0, 100.0, 300)), symbol="KOSPI")
    iset = IndicatorSet(stock, index, cfg)
    d = iset.index[-1].date()
    assert iset.ma200_rising(d) is False


def test_ma200_rising_false_when_insufficient_history(cfg: Config) -> None:
    iset = _long_set(cfg)
    early = iset.index[10].date()  # 200MA 아직 NaN
    assert iset.ma200_rising(early) is False


def test_ma200_rising_alt_lookback_or(cfg: Config) -> None:
    # R2a(Q3): 장기 하락 후 완만한 턴 — 200MA가 일간으로는 상승 전환했지만 20일
    # 룩백은 계단 후행으로 아직 하락. 보조 룩백(5일) OR 설정 시에만 상승 판정.
    closes = [400.0 - t for t in range(260)] + [141.0 + 12.0 * (t + 1) for t in range(20)]
    stock = _frame(closes)
    index = _frame([100.0] * len(closes), symbol="KOSPI")
    d = stock.df.index[-1].date()

    assert IndicatorSet(stock, index, cfg).ma200_rising(d) is False  # 현행(20일 단일)

    from oneil_bt.analysis import apply_overrides

    cfg_alt = apply_overrides(cfg, {"trend.ma200_rising_lookback_alt": 5})
    assert IndicatorSet(stock, index, cfg_alt).ma200_rising(d) is True


def test_indicator_set_asof_helper(cfg: Config) -> None:
    iset = _long_set(cfg)
    d = iset.index[-1].date()
    assert iset.asof("ma50", d) == pytest.approx(float(iset.ma50.iloc[-1]))
    with pytest.raises(AttributeError):
        iset.asof("symbol", d)  # 시리즈가 아님


# --------------------------------------------------------------------------- #
# 룩어헤드 회귀: 미래 바를 조작해도 과거 지표 값 불변
# --------------------------------------------------------------------------- #
def test_lookahead_regression(cfg: Config) -> None:
    n = 300
    closes = list(np.linspace(100.0, 400.0, n))
    index_closes = list(np.linspace(100.0, 200.0, n))
    stock = _frame(closes, volumes=2_000)
    index = _frame(index_closes, symbol="KOSPI")
    iset = IndicatorSet(stock, index, cfg)

    cut = 250
    baseline = {
        name: getattr(iset, name).iloc[:cut].copy()
        for name in ("ma50", "ma200", "atr14", "high_52w", "low_52w",
                     "turnover_20d", "ret_20d", "rs_6m")
    }

    # 마지막 50바만 극단값으로 조작(과거 [:cut]은 원본 그대로 슬라이스)
    stock2 = _frame(closes[:cut] + [9_999.0] * (n - cut), volumes=2_000)
    index2 = _frame(index_closes[:cut] + [9_999.0] * (n - cut), symbol="KOSPI")
    iset2 = IndicatorSet(stock2, index2, cfg)

    for name, before in baseline.items():
        after = getattr(iset2, name).iloc[:cut]
        pd.testing.assert_series_equal(before, after, check_names=True)
