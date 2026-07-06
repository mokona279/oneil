"""거래일 캘린더 (계획서 §3.2, §4.4).

거래일 = 지수 CSV의 날짜. 종목은 이 캘린더로 reindex한다.
모든 날짜 연산(shift/lookback)은 이 세션 리스트 위에서 결정론적으로 동작한다.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import date

import pandas as pd


def _as_date(d: date | pd.Timestamp | str) -> date:
    if isinstance(d, date) and not isinstance(d, pd.Timestamp):
        return d
    return pd.Timestamp(d).date()


class TradingCalendar:
    def __init__(self, sessions: Sequence[date | pd.Timestamp | str]) -> None:
        norm = sorted({_as_date(s) for s in sessions})
        if not norm:
            raise ValueError("TradingCalendar requires at least one session")
        self._sessions: list[date] = norm
        self._pos: dict[date, int] = {d: i for i, d in enumerate(norm)}

    @classmethod
    def from_index(cls, index: pd.DatetimeIndex) -> "TradingCalendar":
        return cls([ts.date() for ts in index])

    # ------------------------------------------------------------------ #
    @property
    def sessions(self) -> list[date]:
        return list(self._sessions)

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, d: date | pd.Timestamp | str) -> bool:
        return _as_date(d) in self._pos

    @property
    def first(self) -> date:
        return self._sessions[0]

    @property
    def last(self) -> date:
        return self._sessions[-1]

    def sessions_between(
        self, start: date | pd.Timestamp | str, end: date | pd.Timestamp | str
    ) -> list[date]:
        """[start, end] 폐구간에 속하는 거래일 목록 (양끝이 거래일이 아니어도 됨)."""
        lo = _as_date(start)
        hi = _as_date(end)
        i = bisect_left(self._sessions, lo)
        j = bisect_right(self._sessions, hi)
        return self._sessions[i:j]

    def _index_of(self, d: date | pd.Timestamp | str) -> int:
        """거래일 d의 위치. d가 거래일이 아니면 ValueError."""
        dd = _as_date(d)
        pos = self._pos.get(dd)
        if pos is None:
            raise ValueError(f"{dd} is not a trading session")
        return pos

    def shift(self, d: date | pd.Timestamp | str, n: int) -> date | None:
        """거래일 d에서 n거래일 이동(+/-). 범위를 벗어나면 None.

        d가 거래일이 아니면 방향의 첫 거래일이 첫 스텝이 된다:
        n>0은 d 초과 첫 거래일이 n=1, n<0은 d 미만 마지막 거래일이 n=-1.
        비거래일에서 n=0은 정의되지 않아 None을 반환한다.
        """
        dd = _as_date(d)
        pos = self._pos.get(dd)
        if pos is not None:
            target = pos + n
        elif n > 0:
            hi = bisect_right(self._sessions, dd)        # 첫 세션 > dd
            target = hi + (n - 1)
        elif n < 0:
            lo = bisect_left(self._sessions, dd) - 1     # 마지막 세션 < dd
            target = lo + (n + 1)
        else:
            return None
        if 0 <= target < len(self._sessions):
            return self._sessions[target]
        return None

    def lookback_window(self, d: date | pd.Timestamp | str, n: int) -> list[date]:
        """d(포함) 이하에서 가장 최근 n개 거래일을 오름차순으로 반환.

        가능한 세션이 n개 미만이면 있는 만큼만 반환한다.
        """
        if n <= 0:
            return []
        dd = _as_date(d)
        end = bisect_right(self._sessions, dd)   # dd 이하 세션 개수
        start = max(0, end - n)
        return self._sessions[start:end]

    def calendar_days_between(
        self, a: date | pd.Timestamp | str, b: date | pd.Timestamp | str
    ) -> int:
        """달력일 차이 (b - a).days. 베이스 기간(달력일) 계산용 (§5)."""
        return (_as_date(b) - _as_date(a)).days
