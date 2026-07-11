"""cli: 오케스트레이션 end-to-end (FakeClient) (계획서 §7).

--dry-run 계획, --symbols 우선, 실패 종목 계속진행 + exit code 1, 산출물 로더 통과.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from oneil_bt.data.csv_source import CsvDataSource
from oneil_bt.data.metadata import MetaRepository
from oneil_bt.domain.enums import Market
from oneil_fetch.cli import build_parser, run_fetch
from tests.unit.fetch.fakes import FakeClient, krx_index_frame, krx_ohlcv_frame

_DATES = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"]


def _cap_frame(tickers: list[str], shares: list[int]) -> pd.DataFrame:
    return pd.DataFrame({"상장주식수": shares}, index=tickers)


def make_client(*, drop_ohlcv: set[str] | None = None) -> FakeClient:
    drop = drop_ohlcv or set()
    all_syms = {
        "005930": 100.0,
        "000660": 90.0,
        "035720": 50.0,
    }
    ohlcv = {
        s: krx_ohlcv_frame(_DATES, [base + i for i in range(len(_DATES))])
        for s, base in all_syms.items()
        if s not in drop
    }
    indices = {
        "1001": krx_index_frame(_DATES, [2000 + i for i in range(len(_DATES))]),
        "2001": krx_index_frame(_DATES, [700 + i for i in range(len(_DATES))]),
    }
    tickers = {
        "KOSPI": ["005930", "000660", "005935"],  # 005935=우선주
        "KOSDAQ": ["035720", "900100스팩자리"],     # 스팩은 이름으로 제외
    }
    tickers["KOSDAQ"] = ["035720", "123450"]
    names = {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
        "005935": "삼성전자우",
        "035720": "카카오",
        "123450": "대신스팩1호",
    }
    market_caps = {
        "KOSPI": _cap_frame(["005930", "000660"], [5969782550, 728002365]),
        "KOSDAQ": _cap_frame(["035720"], [443000000]),
    }
    listing = pd.DataFrame(
        {
            "Code": ["005930", "000660", "035720"],
            "ListingDate": ["1975-06-11", "1996-12-26", "2017-07-10"],
        }
    )
    return FakeClient(
        ohlcv=ohlcv,
        indices=indices,
        tickers=tickers,
        names=names,
        market_caps=market_caps,
        listing=listing,
    )


def _args(*extra: str, out: Path) -> object:
    base = ["--start", "2020-01-01", "--end", "2020-01-08", "--out", str(out)]
    return build_parser().parse_args(base + list(extra))


def test_dry_run_prints_plan_no_fetch(tmp_path: Path) -> None:
    client = make_client()
    report, code = run_fetch(client, _args("--dry-run", out=tmp_path))
    assert code == 0
    assert report["dry_run"] is True
    # 보통주 필터+스팩 제외 → 005930,000660,035720
    assert report["universe_size"] == 3
    assert not client.ohlcv_calls  # 종목 시세 호출 없음
    assert not (tmp_path / "prices").exists()


def test_symbols_flag_takes_priority(tmp_path: Path) -> None:
    client = make_client()
    report, code = run_fetch(client, _args("--symbols", "005930", out=tmp_path))
    assert code == 0
    assert report["universe_size"] == 1
    assert (tmp_path / "prices" / "005930.csv").exists()
    assert not (tmp_path / "prices" / "000660.csv").exists()


def test_full_run_produces_engine_loadable_dataset(tmp_path: Path) -> None:
    client = make_client()
    report, code = run_fetch(client, _args(out=tmp_path))
    assert code == 0
    assert report["succeeded"] == 3

    # 엔진이 실제로 로드 가능해야 한다 (자기검증의 최종 확인)
    source = CsvDataSource(
        price_dir=tmp_path / "prices",
        index_paths={
            Market.KOSPI: tmp_path / "kospi.csv",
            Market.KOSDAQ: tmp_path / "kosdaq.csv",
        },
        meta=MetaRepository.from_csv(tmp_path / "meta.csv"),
    )
    pf = source.load_prices("005930")
    assert len(pf.df) == len(_DATES)
    assert "value" in pf.df.columns
    # meta가 prices 심볼 전수를 덮는다 (§1.3)
    repo = MetaRepository.from_csv(tmp_path / "meta.csv")
    for sym in ("005930", "000660", "035720"):
        assert repo.has(sym)


def test_failed_symbol_continues_and_exit_code_1(tmp_path: Path) -> None:
    client = make_client(drop_ohlcv={"000660"})  # 000660 시세 없음 → 실패
    report, code = run_fetch(client, _args(out=tmp_path))
    assert code == 1
    assert "000660" in report["failed"]
    # 나머지는 성공
    assert (tmp_path / "prices" / "005930.csv").exists()
    assert (tmp_path / "prices" / "035720.csv").exists()
    assert report["succeeded"] == 2


def test_index_files_written(tmp_path: Path) -> None:
    client = make_client()
    run_fetch(client, _args("--symbols", "005930", out=tmp_path))
    assert (tmp_path / "kospi.csv").exists()
    assert (tmp_path / "kosdaq.csv").exists()


def test_rerun_skips_up_to_date(tmp_path: Path) -> None:
    client = make_client()
    run_fetch(client, _args("--symbols", "005930", out=tmp_path))
    calls_before = len(client.ohlcv_calls)
    # 2회차: 이미 최신 → 시세 재호출 없이 스킵
    report2, _ = run_fetch(client, _args("--symbols", "005930", out=tmp_path))
    assert report2["skipped_up_to_date"] == 1
    assert len(client.ohlcv_calls) == calls_before
