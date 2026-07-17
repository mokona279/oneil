"""meta_builder: FDR 조인·zero-pad·로더 round-trip + 상장 목록 이탈 폴백 (계획서 §7)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from oneil_bt.data.loader import ValidationError
from oneil_bt.data.metadata import MetaRepository
from oneil_fetch.meta_builder import (
    build_meta_rows,
    load_meta_fallback,
    normalize_listing_dates,
)
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


# --------------------------------------------------------------------------- #
# 상장 목록 이탈 폴백 (2026-07-17 012510 실측 — 거래정지·상폐 절차 종목)
# --------------------------------------------------------------------------- #
def test_market_fallback_reuses_previous_meta() -> None:
    fallback = {
        "012510": {
            "symbol": "012510", "name": "더존비즈온", "market": "KOSPI",
            "listing_date": "1988-11-11", "shares_out": "29123883",
        }
    }
    result = build_meta_rows(
        ["012510"], names={}, markets={},  # 당일 상장 목록에 없음
        listing_dates={}, shares_out={}, fallback=fallback,
    )
    assert result.market_fallback == ["012510"]
    assert result.market_missing == []
    row = result.rows[0]
    assert row["market"] == "KOSPI"
    assert row["name"] == "더존비즈온"
    assert row["listing_date"] == "1988-11-11"
    assert row["shares_out"] == 29123883


def test_market_fallback_does_not_override_fresh_values() -> None:
    fallback = {"005930": {"market": "KOSDAQ", "name": "옛이름", "shares_out": "1"}}
    result = build_meta_rows(
        ["005930"], names={"005930": "삼성전자"}, markets={"005930": "KOSPI"},
        listing_dates={"005930": "1975-06-11"}, shares_out={"005930": 5969782550},
        fallback=fallback,
    )
    assert result.market_fallback == []
    row = result.rows[0]
    assert row["market"] == "KOSPI"
    assert row["name"] == "삼성전자"
    assert row["shares_out"] == 5969782550


def test_market_missing_without_fallback_is_reported() -> None:
    result = build_meta_rows(
        ["012510"], names={}, markets={}, listing_dates={}, shares_out={},
    )
    assert result.market_missing == ["012510"]
    assert result.rows[0]["market"] == ""


def test_load_meta_fallback_tolerates_broken_rows(tmp_path: Path) -> None:
    p = tmp_path / "meta.csv"
    p.write_text(
        "symbol,name,market,listing_date,shares_out\n"
        "005930,삼성전자,KOSPI,1975-06-11,5969782550\n"
        "012510,더존비즈온,,,\n",  # 오염 행 — 관대하게 읽되 정상 행은 살린다
        encoding="utf-8",
    )
    rows = load_meta_fallback(p)
    assert rows["005930"]["market"] == "KOSPI"
    assert rows["012510"]["market"] == ""  # 빈 값 그대로 (폴백으론 못 쓰지만 읽기는 성공)
    assert load_meta_fallback(tmp_path / "missing.csv") == {}


def test_write_meta_failure_preserves_existing_file(tmp_path: Path) -> None:
    """검증 실패가 기존 정상 meta.csv를 파괴하지 않는다(원자 교체)."""
    path = tmp_path / "meta.csv"
    good = build_meta_rows(
        ["005930"], names={"005930": "삼성전자"}, markets={"005930": "KOSPI"},
        listing_dates={"005930": "1975-06-11"}, shares_out={"005930": 5969782550},
    )
    write_meta(good.rows, path)
    before = path.read_text(encoding="utf-8")

    bad = build_meta_rows(["012510"], names={}, markets={}, listing_dates={}, shares_out={})
    with pytest.raises(ValidationError):
        write_meta(bad.rows, path)  # market 빈칸 → MetaRepository 검증 실패

    assert path.read_text(encoding="utf-8") == before  # 기존 파일 무손상
    assert not list(tmp_path.glob("*.tmp"))  # 임시 파일 잔존 없음
