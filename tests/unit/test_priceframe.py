"""PriceFrame 불변 래퍼 (Phase 0)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from oneil_bt.domain.bar import PriceFrame
from tests.fixtures.synthetic import business_dates, ohlcv_frame


@pytest.fixture(scope="module")
def pf() -> PriceFrame:
    dates = business_dates("2020-01-01", 10)
    closes = [100 + i for i in range(10)]
    return PriceFrame("TEST", ohlcv_frame(dates, closes))


def test_dates_and_columns(pf: PriceFrame) -> None:
    assert isinstance(pf.dates, pd.DatetimeIndex)
    for c in ("open", "high", "low", "close", "volume"):
        assert c in pf.df.columns


def test_row_exact(pf: PriceFrame) -> None:
    d = pf.dates[3].date()
    row = pf.row(d)
    assert row is not None
    assert row["close"] == 103
    assert pf.row(date(2019, 1, 1)) is None


def test_asof_returns_latest_leq(pf: PriceFrame) -> None:
    # 토요일(거래일 아님) as-of는 직전 금요일
    sat = date(2020, 1, 4)
    row = pf.asof(sat)
    assert row is not None
    assert row["close"] == 102  # 2020-01-03(금), 3번째 세션
    assert pf.asof(date(2019, 12, 31)) is None


def test_slice_closed_interval(pf: PriceFrame) -> None:
    s = [d.date() for d in pf.dates]
    sub = pf.slice(s[2], s[5])
    assert len(sub.df) == 4
    assert sub.dates[0].date() == s[2]
    assert sub.dates[-1].date() == s[5]


def test_post_init_rejects_non_datetime_index() -> None:
    df = pd.DataFrame(
        {"open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]},
        index=[0],
    )
    with pytest.raises(ValueError):
        PriceFrame("X", df)


def test_post_init_rejects_missing_column() -> None:
    idx = pd.DatetimeIndex(pd.to_datetime(["2020-01-01"]), name="date")
    df = pd.DataFrame({"open": [1], "high": [1], "low": [1], "close": [1]}, index=idx)
    with pytest.raises(ValueError):
        PriceFrame("X", df)


def test_post_init_rejects_duplicate_dates() -> None:
    idx = pd.DatetimeIndex(pd.to_datetime(["2020-01-01", "2020-01-01"]), name="date")
    df = pd.DataFrame(
        {"open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [1, 1], "volume": [1, 1]},
        index=idx,
    )
    with pytest.raises(ValueError):
        PriceFrame("X", df)
