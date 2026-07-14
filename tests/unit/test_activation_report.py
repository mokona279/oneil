"""저표본 개정 발동 집계기 — §3.3 추적 의무 (P3 신설).

합성 DataFrame으로 조인·집계 정확성만 검증한다(엔진 무관). rule_activations.csv /
trades.csv 스키마의 부분집합(사용 컬럼)만 만든다.
"""

from __future__ import annotations

import pandas as pd

from oneil_bt.analysis import build_activation_report

ACTS_DF = pd.DataFrame(
    dict(
        date=["2025-12-30", "2023-05-19", "2020-12-01", "2024-01-05"],
        symbol=["000660", "005930", "083310", "111111"],
        rule=["r3b_reset_entry", "r4a_handle_entry", "q11_stop_clamp",
              "r4a_handle_entry"],  # 111111 발동은 미체결(트레이드 없음) 케이스
        detail=["{}"] * 4,
    )
)

TRADES_DF = pd.DataFrame(
    dict(
        symbol=["000660", "000660", "005930", "083310", "222222"],
        # 000660은 부분청산 2행(동일 진입일) — 전부 귀속돼야 한다.
        entry_date=["2025-12-30", "2025-12-30", "2023-05-19", "2020-11-20",
                    "2024-03-02"],
        exit_date=["2026-04-01", "2026-04-03", "2023-08-01", "2020-12-11",
                   "2024-05-01"],
        pnl=[4_000_000.0, 4_000_000.0, 1_200_000.0, -1_510_000.0, 500_000.0],
        pnl_r=[4.79, 4.79, 1.2, -1.51, 0.5],
    )
)


def test_entry_rules_join_on_entry_date() -> None:
    report = build_activation_report(ACTS_DF, TRADES_DF, initial_cash=1.0e8)
    by = report.set_index("rule")
    r3b = by.loc["r3b_reset_entry"]
    assert r3b["n_activations"] == 1
    assert r3b["n_trades"] == 2                      # 부분청산 2행 전부 귀속
    assert r3b["sum_pnl"] == 8_000_000.0
    assert r3b["contribution_pct"] == 8.0            # 8백만/1억 = +8%p
    r4a = by.loc["r4a_handle_entry"]
    assert r4a["n_activations"] == 2                 # 발동 2건(1건은 미체결)
    assert r4a["n_symbols"] == 2
    assert r4a["n_trades"] == 1                      # 조인되는 트레이드는 1건뿐
    assert r4a["sum_r"] == 1.2


def test_clamp_joins_on_holding_window() -> None:
    # 클램프 발동일(2020-12-01)은 진입일이 아니라 보유 구간 안 — 구간 조인.
    report = build_activation_report(ACTS_DF, TRADES_DF, initial_cash=1.0e8)
    q11 = report.set_index("rule").loc["q11_stop_clamp"]
    assert q11["n_activations"] == 1
    assert q11["n_trades"] == 1
    assert q11["sum_pnl"] == -1_510_000.0


def test_empty_activations_yields_empty_report() -> None:
    empty = pd.DataFrame(columns=["date", "symbol", "rule", "detail"])
    report = build_activation_report(empty, TRADES_DF, initial_cash=1.0e8)
    assert len(report) == 0                          # 0행 = 무발동 증빙
