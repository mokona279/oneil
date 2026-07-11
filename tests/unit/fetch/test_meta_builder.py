"""meta_builder: FDR 조인·zero-pad·로더 round-trip (계획서 §7)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from oneil_bt.data.metadata import MetaRepository
from oneil_fetch.meta_builder import build_meta_rows, normalize_listing_dates
from oneil_fetch.writer import write_meta


def test_normalize_listing_dates_zero_pads_and_isoformats() -> None:
    fdr = pd.DataFrame(
        {"Code": [5930, "660"], "ListingDate": ["1975-06-11", pd.Timestamp("1996-12-26")]}
    )
    out = normalize_listing_dates(fdr)
    assert out["005930"] == "1975-06-11"
    assert out["000660"] == "1996-12-26"


def test_normalize_listing_dates_skips_unparseable() -> None:
    fdr = pd.DataFrame({"Code": ["005930"], "ListingDate": [None]})
    assert normalize_listing_dates(fdr) == {}


def test_build_rows_records_missing_listing_date() -> None:
    result = build_meta_rows(
        ["005930", "000660"],
        names={"005930": "삼성전자", "000660": "SK하이닉스"},
        markets={"005930": "KOSPI", "000660": "KOSPI"},
        listing_dates={"005930": "1975-06-11"},  # 000660 결측
        shares_out={"005930": 5969782550},
    )
    assert result.missing_listing_date == ["000660"]
    row0 = result.rows[0]
    assert row0["listing_date"] == "1975-06-11"
    assert row0["shares_out"] == 5969782550
    assert result.rows[1]["shares_out"] == ""  # shares 결측 → 빈칸


def test_written_meta_passes_repository(tmp_path: Path) -> None:
    result = build_meta_rows(
        ["005930", "035720"],
        names={"005930": "삼성전자", "035720": "카카오"},
        markets={"005930": "KOSPI", "035720": "KOSDAQ"},
        listing_dates={"005930": "1975-06-11", "035720": "2017-07-10"},
        shares_out={"005930": 5969782550, "035720": 443000000},
    )
    path = tmp_path / "meta.csv"
    write_meta(result.rows, path)  # 내부 MetaRepository.from_csv 자기검증
    repo = MetaRepository.from_csv(path)
    assert repo.get("005930").name == "삼성전자"
    assert str(repo.get("035720").market) == "KOSDAQ"
    assert repo.get("005930").shares_out == 5969782550
