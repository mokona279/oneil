"""TradingCalendar shift/window/경계 (Phase 0)."""

from __future__ import annotations

from datetime import date

import pytest

from oneil_bt.data.calendar import TradingCalendar
from tests.fixtures.synthetic import business_dates


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    # 2020-01-01(수)부터 영업일 10개
    return TradingCalendar(business_dates("2020-01-01", 10))


def test_dedup_and_sort() -> None:
    days = business_dates("2020-01-01", 5)
    cal = TradingCalendar(list(reversed(days)) + [days[0]])  # 역순 + 중복
    assert cal.sessions == days
    assert len(cal) == 5


def test_contains_and_bounds(cal: TradingCalendar) -> None:
    sessions = cal.sessions
    assert sessions[0] in cal
    assert date(2020, 1, 4) not in cal  # 토요일
    assert cal.first == sessions[0]
    assert cal.last == sessions[-1]


def test_sessions_between(cal: TradingCalendar) -> None:
    s = cal.sessions
    got = cal.sessions_between(s[2], s[5])
    assert got == s[2:6]
    # 양끝이 거래일이 아니어도 폐구간 포함
    got2 = cal.sessions_between(date(2019, 12, 1), date(2030, 1, 1))
    assert got2 == s


def test_shift_forward_back(cal: TradingCalendar) -> None:
    s = cal.sessions
    assert cal.shift(s[0], 3) == s[3]
    assert cal.shift(s[5], -2) == s[3]
    assert cal.shift(s[0], 0) == s[0]


def test_shift_out_of_range_returns_none(cal: TradingCalendar) -> None:
    s = cal.sessions
    assert cal.shift(s[-1], 1) is None
    assert cal.shift(s[0], -1) is None


def test_shift_non_session(cal: TradingCalendar) -> None:
    # 토요일(2020-01-04): 방향의 첫 거래일이 첫 스텝
    sat = date(2020, 1, 4)
    mon = date(2020, 1, 6)
    fri = date(2020, 1, 3)
    assert cal.shift(sat, 1) == mon    # 초과 첫 거래일
    assert cal.shift(sat, -1) == fri   # 미만 마지막 거래일
    assert cal.shift(sat, 0) is None   # 비거래일 n=0은 미정의


def test_lookback_window(cal: TradingCalendar) -> None:
    s = cal.sessions
    assert cal.lookback_window(s[4], 3) == s[2:5]
    # 앞이 부족하면 있는 만큼
    assert cal.lookback_window(s[1], 5) == s[0:2]
    assert cal.lookback_window(s[0], 0) == []


def test_calendar_days_between(cal: TradingCalendar) -> None:
    assert cal.calendar_days_between(date(2020, 1, 1), date(2020, 1, 15)) == 14
    assert cal.calendar_days_between(date(2020, 1, 15), date(2020, 1, 1)) == -14


def test_non_session_index_raises(cal: TradingCalendar) -> None:
    with pytest.raises(ValueError):
        cal._index_of(date(2020, 1, 4))
