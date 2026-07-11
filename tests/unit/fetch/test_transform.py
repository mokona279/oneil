"""transform: rename·정제·로더 round-trip (계획서 §7)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from oneil_bt.data.loader import CsvBarLoader
from oneil_fetch.transform import clean_bars, normalize_index, normalize_ohlcv
from oneil_fetch.writer import write_prices
from tests.unit.fetch.fakes import krx_index_frame, krx_ohlcv_frame


def test_normalize_rename_and_date_column() -> None:
    raw = krx_ohlcv_frame(["2020-01-02", "2020-01-03"], [100.0, 101.0])
    df = normalize_ohlcv(raw)
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "value"]
    assert df["date"].tolist() == ["2020-01-02", "2020-01-03"]
    assert df["close"].tolist() == [100.0, 101.0]


def test_normalize_sorts_and_dedups() -> None:
    raw = krx_ohlcv_frame(["2020-01-03", "2020-01-02", "2020-01-03"], [3, 2, 9])
    df = normalize_ohlcv(raw)
    assert df["date"].tolist() == ["2020-01-02", "2020-01-03"]
    # 중복은 마지막(keep='last') 유지 → close=9
    assert df.iloc[-1]["close"] == 9


def test_clean_fixes_trading_halt_row() -> None:
    # 거래정지: O=H=L=0, close는 직전가 유지, volume=0
    raw = krx_ohlcv_frame(["2020-01-02", "2020-01-03"], [100.0, 100.0])
    df = normalize_ohlcv(raw)
    df.loc[1, ["open", "high", "low", "volume"]] = 0.0  # close=100 유지
    cleaned, stats = clean_bars(df)
    assert stats.halt_fixed == 1
    row = cleaned.iloc[1]
    assert row["open"] == row["high"] == row["low"] == row["close"] == 100.0


def test_clean_drops_nonpositive_close() -> None:
    raw = krx_ohlcv_frame(["2020-01-02", "2020-01-03"], [100.0, 50.0])
    df = normalize_ohlcv(raw)
    df.loc[1, ["open", "high", "low", "close"]] = 0.0
    df.loc[1, "volume"] = 500.0  # halt 패턴 아님(volume>0) → close<=0 삭제 경로
    cleaned, stats = clean_bars(df)
    assert stats.dropped_nonpositive == 1
    assert len(cleaned) == 1


def test_clean_clamps_tiny_rounding_violation() -> None:
    # 수정주가 반올림: 종가가 고가보다 1원 큰 아티팩트 → 삭제가 아니라 클램프
    raw = krx_ohlcv_frame(["2020-01-02", "2020-01-03"], [20000.0, 20000.0])
    df = normalize_ohlcv(raw)
    df.loc[1, "high"] = 20372.0
    df.loc[1, "close"] = 20373.0  # close가 high보다 1원 위 (0.005%)
    cleaned, stats = clean_bars(df)
    assert stats.clamped == 1
    assert stats.dropped_integrity == 0
    assert len(cleaned) == 2
    row = cleaned.iloc[1]
    assert row["high"] == 20373.0  # high가 close를 감싸도록 클램프
    assert row["low"] <= row["open"] and row["close"] <= row["high"]


def test_clean_drops_gross_integrity_break() -> None:
    raw = krx_ohlcv_frame(["2020-01-02", "2020-01-03"], [100.0, 101.0])
    df = normalize_ohlcv(raw)
    df.loc[1, "high"] = 50.0  # high << low (큰 붕괴) → 삭제
    cleaned, stats = clean_bars(df)
    assert stats.dropped_integrity == 1
    assert stats.clamped == 0
    assert len(cleaned) == 1


def test_cleaned_output_passes_loader(tmp_path: Path) -> None:
    raw = krx_ohlcv_frame(
        ["2020-01-02", "2020-01-03", "2020-01-06"], [100.0, 101.0, 102.0]
    )
    df, _ = clean_bars(normalize_ohlcv(raw))
    path = tmp_path / "005930.csv"
    write_prices(df, path)  # 내부에서 CsvBarLoader.load 자기검증
    pf = CsvBarLoader().load(path)
    assert len(pf.df) == 3
    assert "value" in pf.df.columns


def test_normalize_index_keeps_date_close() -> None:
    raw = krx_index_frame(["2020-01-02", "2020-01-03"], [2000.0, 2010.0])
    df = normalize_index(raw)
    assert list(df.columns) == ["date", "close"]
    assert df["close"].tolist() == [2000.0, 2010.0]
