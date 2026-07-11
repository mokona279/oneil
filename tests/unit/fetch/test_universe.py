"""universe: 보통주 필터·스팩 제외 (계획서 §5.4, §7)."""

from __future__ import annotations

from oneil_fetch.universe import is_common_stock, select_universe


def test_is_common_stock() -> None:
    assert is_common_stock("005930")   # 삼성전자 보통주
    assert not is_common_stock("005935")  # 삼성전자 우선주
    assert not is_common_stock("00593")   # 6자리 아님


def test_select_filters_preferred_by_default() -> None:
    tickers = ["005930", "005935", "000660", "000661"]
    names = {t: "" for t in tickers}
    assert select_universe(tickers, names) == ["000660", "005930"]


def test_select_excludes_spac() -> None:
    tickers = ["005930", "123450"]
    names = {"005930": "삼성전자", "123450": "미래에셋스팩1호"}
    assert select_universe(tickers, names) == ["005930"]


def test_include_non_common_keeps_preferred_but_still_drops_spac() -> None:
    tickers = ["005930", "005935", "123450"]
    names = {"005930": "삼성전자", "005935": "삼성전자우", "123450": "케이비스팩"}
    assert select_universe(tickers, names, include_non_common=True) == [
        "005930", "005935"
    ]


def test_select_dedups_and_sorts() -> None:
    tickers = ["000660", "005930", "000660"]
    names = {t: "" for t in tickers}
    assert select_universe(tickers, names) == ["000660", "005930"]
