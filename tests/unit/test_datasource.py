"""CsvDataSource end-to-end 왕복 (Phase 0 DoD)."""

from __future__ import annotations

from pathlib import Path

import pytest

from oneil_bt.data.csv_source import CsvDataSource
from oneil_bt.data.datasource import DataSource
from oneil_bt.data.loader import ValidationError
from oneil_bt.data.metadata import MetaRepository
from oneil_bt.domain.enums import Market
from tests.fixtures.synthetic import (
    business_dates,
    ohlcv_frame,
    write_index_csv,
    write_meta_csv,
    write_prices_csv,
)


@pytest.fixture
def source(tmp_path: Path) -> CsvDataSource:
    price_dir = tmp_path / "prices"
    dates = business_dates("2020-01-01", 15)
    for sym, base in (("005930", 100), ("247540", 50)):
        df = ohlcv_frame(dates, [base + i for i in range(15)])
        write_prices_csv(df, price_dir / f"{sym}.csv")

    kospi = tmp_path / "kospi.csv"
    kosdaq = tmp_path / "kosdaq.csv"
    write_index_csv(dates, [2000 + i for i in range(15)], kospi)
    write_index_csv(dates, [900 + i for i in range(15)], kosdaq)

    meta = MetaRepository.from_csv(
        write_meta_csv(
            [
                {"symbol": "005930", "name": "삼성전자", "market": "KOSPI"},
                {"symbol": "247540", "name": "에코프로비엠", "market": "KOSDAQ"},
            ],
            tmp_path / "meta.csv",
        )
    )
    return CsvDataSource(
        price_dir,
        {Market.KOSPI: kospi, Market.KOSDAQ: kosdaq},
        meta,
    )


def test_satisfies_protocol(source: CsvDataSource) -> None:
    assert isinstance(source, DataSource)


def test_symbols_sorted(source: CsvDataSource) -> None:
    assert source.symbols() == ["005930", "247540"]


def test_load_prices_roundtrip(source: CsvDataSource) -> None:
    pf = source.load_prices("005930")
    assert pf.symbol == "005930"
    assert len(pf.df) == 15
    assert pf.df["close"].iloc[0] == 100


def test_prices_cached(source: CsvDataSource) -> None:
    assert source.load_prices("005930") is source.load_prices("005930")


def test_load_index(source: CsvDataSource) -> None:
    idx = source.load_index(Market.KOSPI)
    assert idx.df["close"].iloc[0] == 2000
    assert source.load_index(Market.KOSDAQ).df["close"].iloc[0] == 900


def test_meta_lookup(source: CsvDataSource) -> None:
    assert source.meta("247540").market is Market.KOSDAQ


def test_bad_price_dir_raises(tmp_path: Path) -> None:
    meta = MetaRepository({})
    with pytest.raises(ValidationError):
        CsvDataSource(tmp_path / "does_not_exist", {}, meta)
