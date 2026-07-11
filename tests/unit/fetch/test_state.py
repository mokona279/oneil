"""state: 체크포인트 저장/복원·실패목록 (계획서 §7)."""

from __future__ import annotations

from pathlib import Path

from oneil_fetch.state import FetchState


def test_roundtrip(tmp_path: Path) -> None:
    st = FetchState()
    st.mark_completed("005930")
    st.mark_failed("000660", "timeout")
    st.last_end = "2020-12-31"
    st.save(tmp_path)

    loaded = FetchState.load(tmp_path)
    assert loaded.is_completed("005930")
    assert loaded.failed["000660"] == "timeout"
    assert loaded.last_end == "2020-12-31"


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    st = FetchState.load(tmp_path)
    assert st.completed == set()
    assert st.failed == {}


def test_mark_completed_clears_previous_failure() -> None:
    st = FetchState()
    st.mark_failed("005930", "err")
    st.mark_completed("005930")
    assert st.is_completed("005930")
    assert "005930" not in st.failed


def test_reset_clears(tmp_path: Path) -> None:
    st = FetchState()
    st.mark_completed("005930")
    st.mark_failed("000660", "x")
    st.reset()
    assert st.completed == set()
    assert st.failed == {}
