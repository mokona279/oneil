"""캡처 리포트 집계기 — 진입 판정·병목·요약·스윕 통계 (개선계획 §3.3, P0-T3).

합성 DataFrame으로 집계 정확성만 검증한다(엔진 무관). trades.csv/entry_funnel.csv
스키마의 부분집합(사용 컬럼)만 만든다.
"""

from __future__ import annotations

import pandas as pd

from oneil_bt.analysis import (
    build_capture_report,
    capture_stats,
    format_capture_summary,
    load_capture_symbols,
)

CAPTURE_DF = pd.DataFrame(
    dict(
        symbol=["000111", "000222", "000333", "000444"],
        first_achieved=["2020-05-04"] * 4,
        max_multiple=[2.5, 4.5, 2.1, 3.2],
        turnover_ok=[True, True, True, False],  # 000444는 유동성 미달 → 세트 제외
        sessions=[500] * 4,
    )
)

TRADES_DF = pd.DataFrame(
    dict(
        symbol=["000111", "000111", "999999"],  # 000111 두 트레이드 + 세트 밖 종목
        pnl=[5_000_000.0, -1_000_000.0, 2_000_000.0],
        pnl_r=[5.0, -1.0, 2.0],
    )
)

FUNNEL_DF = pd.DataFrame(
    dict(
        symbol=["000111", "000222", "000333"],
        shopped=[100, 100, 100],
        base_present=[50, 40, 30],
        stage_ok=[50, 40, 30],
        breakout=[3, 2, 0],  # 000333은 돌파 자체가 없었다
        gate_trend_ok=[3, 2, 0],
        gate_rs_ok=[3, 0, 0],  # 000222는 RS 게이트에서 전멸
        gate_market_ok=[3, 0, 0],
        gate_quality_ok=[2, 0, 0],
        gates_all_ok=[2, 0, 0],
        entered=[2, 0, 0],
    )
)


def test_report_rows_and_summary() -> None:
    report, summary = build_capture_report(
        CAPTURE_DF, TRADES_DF, FUNNEL_DF, initial_cash=1.0e8
    )
    # turnover_ok=False 인 000444는 세트 본체에서 제외.
    assert report["symbol"].tolist() == ["000111", "000222", "000333"]

    r111 = report.set_index("symbol").loc["000111"]
    assert bool(r111["entered"]) is True
    assert r111["n_trades"] == 2
    assert r111["sum_r"] == 4.0
    assert r111["contribution_pct"] == 4.0  # (5백만−1백만)/1억 = +4%p
    assert r111["bottleneck"] == ""

    by = report.set_index("symbol")
    assert by.loc["000222", "bottleneck"] == "gate_rs_ok"
    assert by.loc["000333", "bottleneck"] == "breakout"

    assert summary["n_set"] == 3
    assert summary["n_entered"] == 1
    assert round(summary["capture_rate_pct"], 2) == 33.33
    assert summary["capture_sum_r"] == 4.0
    # 티어: ≥2× = 3종목 중 1 진입, ≥4× = 000222뿐(미진입) → 0%.
    assert round(summary["tier_rates"][2.0], 2) == 33.33
    assert summary["tier_rates"][4.0] == 0.0
    assert summary["bottlenecks"] == {"gate_rs_ok": 1, "breakout": 1}


def test_report_without_funnel_marks_na() -> None:
    report, _ = build_capture_report(
        CAPTURE_DF, TRADES_DF, None, initial_cash=1.0e8
    )
    missed = report[~report["entered"]]
    assert set(missed["bottleneck"]) == {"n/a"}


def test_report_empty_trades() -> None:
    empty = pd.DataFrame(columns=["symbol", "pnl", "pnl_r"])
    report, summary = build_capture_report(
        CAPTURE_DF, empty, FUNNEL_DF, initial_cash=1.0e8
    )
    assert not report["entered"].any()
    assert summary["capture_rate_pct"] == 0.0
    assert summary["capture_sum_r"] == 0.0


def test_capture_stats_counts_only_set_symbols() -> None:
    trades = [("000111", 5.0), ("000111", -1.0), ("999999", 2.0)]
    rate, sum_r = capture_stats(trades, {"000111", "000222", "000333"})
    assert round(rate, 2) == 33.33
    assert sum_r == 4.0
    assert capture_stats(trades, set()) == (0.0, 0.0)


def test_format_capture_summary_smoke() -> None:
    _, summary = build_capture_report(
        CAPTURE_DF, TRADES_DF, FUNNEL_DF, initial_cash=1.0e8
    )
    text = format_capture_summary(summary)
    assert "캡처율" in text and "33.3%" in text


def test_load_capture_symbols_filters_turnover_and_tier(tmp_path) -> None:
    # Q8(b): 스윕 캘리브레이션은 max_multiple ≥ 4 & turnover_ok 부분집합을 쓴다.
    path = tmp_path / "capture_set.csv"
    CAPTURE_DF.to_csv(path, index=False, encoding="utf-8-sig")
    # turnover_ok만 (티어 없음): 000444(유동성 미달) 제외.
    assert load_capture_symbols(path) == ["000111", "000222", "000333"]
    # ≥4× 티어: 000222(4.5×)만 남는다.
    assert load_capture_symbols(path, min_multiple=4.0) == ["000222"]
