"""CsvBarLoader 로드·검증·인코딩 (Phase 0)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from oneil_bt.data.loader import CsvBarLoader, ValidationError, read_text_autodetect
from tests.fixtures.synthetic import business_dates, ohlcv_frame, write_prices_csv


@pytest.fixture
def loader() -> CsvBarLoader:
    return CsvBarLoader()


def _write_valid(path: Path, *, encoding: str = "utf-8-sig", n: int = 12) -> None:
    dates = business_dates("2020-01-01", n)
    df = ohlcv_frame(dates, [100 + i for i in range(n)], values=[1e9] * n)
    write_prices_csv(df, path, encoding=encoding)


def test_roundtrip(loader: CsvBarLoader, tmp_path: Path) -> None:
    p = tmp_path / "A.csv"
    _write_valid(p)
    pf = loader.load(p)
    assert pf.symbol == "A"
    assert len(pf.df) == 12
    assert "value" in pf.df.columns
    assert pf.df["close"].iloc[0] == 100


def test_cp949_encoding(loader: CsvBarLoader, tmp_path: Path) -> None:
    p = tmp_path / "K.csv"
    _write_valid(p, encoding="cp949")
    pf = loader.load(p)
    assert len(pf.df) == 12


def test_autodetect_reads_cp949_text(tmp_path: Path) -> None:
    p = tmp_path / "t.txt"
    p.write_bytes("가나다".encode("cp949"))
    assert read_text_autodetect(p) == "가나다"


def test_unsorted_is_autosorted(loader: CsvBarLoader, tmp_path: Path) -> None:
    dates = business_dates("2020-01-01", 5)
    df = ohlcv_frame(dates, [100, 101, 102, 103, 104])
    shuffled = df.iloc[[3, 0, 4, 1, 2]]
    p = tmp_path / "S.csv"
    write_prices_csv(shuffled, p)
    pf = loader.load(p)
    assert pf.df.index.is_monotonic_increasing
    assert pf.df["close"].tolist() == [100, 101, 102, 103, 104]


def test_missing_column_raises(loader: CsvBarLoader, tmp_path: Path) -> None:
    p = tmp_path / "M.csv"
    p.write_text("date,open,high,low,close\n2020-01-01,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="missing columns"):
        loader.load(p)


def test_duplicate_date_raises(loader: CsvBarLoader, tmp_path: Path) -> None:
    p = tmp_path / "D.csv"
    p.write_text(
        "date,open,high,low,close,volume\n"
        "2020-01-01,10,11,9,10,100\n"
        "2020-01-01,10,11,9,10,100\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="duplicate"):
        loader.load(p)


def test_high_lt_low_raises(loader: CsvBarLoader, tmp_path: Path) -> None:
    p = tmp_path / "H.csv"
    p.write_text(
        "date,open,high,low,close,volume\n2020-01-01,10,8,9,10,100\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="high >= low"):
        loader.load(p)


def test_close_out_of_range_raises(loader: CsvBarLoader, tmp_path: Path) -> None:
    p = tmp_path / "C.csv"
    p.write_text(
        "date,open,high,low,close,volume\n2020-01-01,10,11,9,20,100\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="close outside"):
        loader.load(p)


def test_nan_raises(loader: CsvBarLoader, tmp_path: Path) -> None:
    p = tmp_path / "N.csv"
    p.write_text(
        "date,open,high,low,close,volume\n2020-01-01,10,11,9,,100\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="missing/NaN"):
        loader.load(p)


def test_bad_date_raises(loader: CsvBarLoader, tmp_path: Path) -> None:
    p = tmp_path / "B.csv"
    p.write_text(
        "date,open,high,low,close,volume\nnotadate,10,11,9,10,100\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="date"):
        loader.load(p)


def test_missing_file_raises(loader: CsvBarLoader, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="not found"):
        loader.load(tmp_path / "nope.csv")
