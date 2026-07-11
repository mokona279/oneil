"""incremental: 오버랩 검증·증분 판단 (계획서 §5.2, §7)."""

from __future__ import annotations

from datetime import date

import pandas as pd

from oneil_fetch.incremental import (
    decide_fetch,
    merge_incremental,
    overlap_fromdate,
)


def _frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000.0] * len(dates),
            "value": [c * 1000 for c in closes],
        }
    )


def test_decide_full_when_no_existing() -> None:
    d = decide_fetch(None, date(2020, 1, 1), date(2020, 12, 31))
    assert d.mode == "full"
    assert d.fromdate == date(2020, 1, 1)


def test_decide_incremental_overlaps_before_last() -> None:
    existing = _frame(["2020-01-02", "2020-01-03"], [100, 101])
    d = decide_fetch(existing, date(2020, 1, 1), date(2020, 2, 1))
    assert d.mode == "incremental"
    # 오버랩 시작일은 기존 마지막(1/3)보다 이르다
    assert d.fromdate < date(2020, 1, 3)


def test_decide_full_when_start_before_existing() -> None:
    existing = _frame(["2020-06-01", "2020-06-02"], [100, 101])
    d = decide_fetch(existing, date(2020, 1, 1), date(2020, 12, 31))
    assert d.mode == "full"  # 앞구간 결손 방지


def test_overlap_fromdate_covers_k_days() -> None:
    # K=10 → 최소 25 달력일 전 (주말·연휴 감안)
    assert overlap_fromdate(date(2020, 1, 31), k=10) == date(2020, 1, 31) - pd.Timedelta(days=25)


def test_merge_append_when_overlap_matches() -> None:
    existing = _frame(["2020-01-02", "2020-01-03"], [100, 101])
    fetched = _frame(["2020-01-03", "2020-01-06", "2020-01-07"], [101, 102, 103])
    result = merge_incremental(existing, fetched)
    assert result.action == "append"
    assert result.appended == 2
    assert result.df["date"].tolist() == [
        "2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"
    ]


def test_merge_refetch_when_overlap_mismatches() -> None:
    existing = _frame(["2020-01-02", "2020-01-03"], [100, 101])
    # 수정 이벤트: 겹친 1/3의 close가 달라짐(분할 소급)
    fetched = _frame(["2020-01-03", "2020-01-06"], [50.5, 51])
    result = merge_incremental(existing, fetched)
    assert result.action == "refetch_full"
    assert result.df is None


def test_merge_refetch_when_no_overlap() -> None:
    existing = _frame(["2020-01-02", "2020-01-03"], [100, 101])
    fetched = _frame(["2020-02-01", "2020-02-02"], [110, 111])
    result = merge_incremental(existing, fetched)
    assert result.action == "refetch_full"


def test_merge_tolerates_tiny_float_noise() -> None:
    existing = _frame(["2020-01-02", "2020-01-03"], [100.0, 101.0])
    fetched = _frame(["2020-01-03", "2020-01-06"], [101.0 + 1e-9, 102.0])
    result = merge_incremental(existing, fetched)
    assert result.action == "append"
