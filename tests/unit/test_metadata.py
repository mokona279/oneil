"""MetaRepository (Phase 0)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from oneil_bt.data.loader import ValidationError
from oneil_bt.data.metadata import MetaRepository
from oneil_bt.domain.enums import Market
from tests.fixtures.synthetic import write_meta_csv


@pytest.fixture
def meta_csv(tmp_path: Path) -> Path:
    rows = [
        {"symbol": "005930", "name": "삼성전자", "market": "KOSPI",
         "listing_date": "1975-06-11", "shares_out": 5969782550},
        {"symbol": "035720", "name": "카카오", "market": "kospi",
         "listing_date": "", "shares_out": ""},
        {"symbol": "247540", "name": "에코프로비엠", "market": "KOSDAQ",
         "listing_date": "2019-03-05", "shares_out": ""},
    ]
    return write_meta_csv(rows, tmp_path / "meta.csv")


def test_from_csv_and_get(meta_csv: Path) -> None:
    repo = MetaRepository.from_csv(meta_csv)
    m = repo.get("005930")
    assert m.name == "삼성전자"
    assert m.market is Market.KOSPI
    assert m.listing_date == date(1975, 6, 11)
    assert m.shares_out == 5969782550


def test_market_case_insensitive_and_nullable(meta_csv: Path) -> None:
    repo = MetaRepository.from_csv(meta_csv)
    m = repo.get("035720")
    assert m.market is Market.KOSPI
    assert m.listing_date is None
    assert m.shares_out is None


def test_symbols_sorted(meta_csv: Path) -> None:
    repo = MetaRepository.from_csv(meta_csv)
    assert repo.symbols() == ["005930", "035720", "247540"]
    assert repo.has("247540")
    assert not repo.has("000000")


def test_get_missing_raises(meta_csv: Path) -> None:
    repo = MetaRepository.from_csv(meta_csv)
    with pytest.raises(ValidationError):
        repo.get("999999")


def test_invalid_market_raises(tmp_path: Path) -> None:
    p = write_meta_csv(
        [{"symbol": "A", "name": "x", "market": "NYSE"}], tmp_path / "m.csv"
    )
    with pytest.raises(ValidationError, match="invalid market"):
        MetaRepository.from_csv(p)


def test_duplicate_symbol_raises(tmp_path: Path) -> None:
    p = write_meta_csv(
        [
            {"symbol": "A", "name": "x", "market": "KOSPI"},
            {"symbol": "A", "name": "y", "market": "KOSPI"},
        ],
        tmp_path / "m.csv",
    )
    with pytest.raises(ValidationError, match="duplicate symbol"):
        MetaRepository.from_csv(p)


def test_missing_column_raises(tmp_path: Path) -> None:
    p = tmp_path / "m.csv"
    p.write_text("symbol,name\nA,x\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="missing columns"):
        MetaRepository.from_csv(p)
