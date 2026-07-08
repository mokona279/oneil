"""리포팅 (Phase 7 DoD).

성과 지표 수치(총수익·CAGR·MDD·승률·손익비·기대값R·보유·노출·비용·청산분해)와 CSV
스키마를 검증한다. 결과 자료구조를 손으로 조립해 기대값을 손계산과 대조하고, 엔진 산출
결과에도 리포터가 무해하게 동작하는지(무신호 자본보존) 확인한다.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from oneil_bt.domain.enums import EntryReason, ExitReason, Market, MarketState
from oneil_bt.domain.trade import ClosedTrade, Fill
from oneil_bt.engine.context import (
    BacktestResult,
    DailyRecord,
    EventRecord,
    TradeRecord,
)
from oneil_bt.reporting import compute_metrics, write_report
from oneil_bt.reporting import trade_log


# --------------------------------------------------------------------------- #
# 손계산 대조용 결과 조립
# --------------------------------------------------------------------------- #
def _trade(
    symbol: str,
    entry_date: date,
    exit_date: date,
    entry_px: float,
    exit_px: float,
    qty: int,
    entry_cost: float,
    exit_cost: float,
    reason: ExitReason,
    risk_per_share: float,
    *,
    pivot: float = 0.0,
    base_stage: int = 1,
) -> TradeRecord:
    closed = ClosedTrade(
        symbol=symbol,
        market=Market.KOSPI,
        tranche_no=1,
        entry_fill=Fill(entry_date, entry_px, qty, EntryReason.BREAKOUT_T1, entry_cost),
        exit_fill=Fill(exit_date, exit_px, qty, reason, exit_cost),
        risk_per_share=risk_per_share,
    )
    return TradeRecord(closed=closed, pivot=pivot or entry_px, base_stage=base_stage)


def _sample_result() -> BacktestResult:
    # 승 트레이드: pnl = (120-100)*10 - 20 - 20 = 160,  pnl_r = 160/(10*10) = 1.6
    win = _trade(
        "AAA", date(2020, 1, 1), date(2020, 1, 11),
        100.0, 120.0, 10, 20.0, 20.0, ExitReason.TREND_60MA_HALF, 10.0,
    )
    # 패 트레이드(손절): pnl = (92-100)*10 - 20 - 18 = -118, pnl_r = -118/100 = -1.18
    loss = _trade(
        "BBB", date(2020, 2, 1), date(2020, 2, 6),
        100.0, 92.0, 10, 20.0, 18.0, ExitReason.STOP, 10.0,
    )

    curve_dates = [date(2020, 1, 1), date(2020, 5, 1), date(2020, 9, 1), date(2021, 1, 1)]
    equities = [1_000_000.0, 1_100_000.0, 900_000.0, 1_200_000.0]
    exposures = [0.0, 50.0, 40.0, 60.0]
    curve = [
        DailyRecord(
            date=d, cash=e, holdings_value=0.0, equity=e,
            n_positions=0, exposure_pct=x,
            market_states={Market.KOSPI: MarketState.NORMAL},
        )
        for d, e, x in zip(curve_dates, equities, exposures)
    ]
    events = [
        EventRecord(date(2020, 1, 1), "AAA", "BREAKOUT_CANDIDATE",
                    {"pivot": 100.0, "depth_pct": 12.5, "weeks": 5.0, "stage": 1}),
        EventRecord(date(2020, 2, 6), "BBB", "EXIT",
                    {"reason": "STOP", "price": 92.0, "qty": 10}),
    ]
    return BacktestResult(
        start=date(2020, 1, 1), end=date(2021, 1, 1),
        initial_cash=1_000_000.0, final_cash=1_200_000.0,
        equity_curve=curve, trades=[win, loss], events=events,
    )


# --------------------------------------------------------------------------- #
# 지표 수치 대조
# --------------------------------------------------------------------------- #
def test_metrics_match_hand_computation() -> None:
    m = compute_metrics(_sample_result())

    assert m.total_return_pct == pytest.approx(20.0)          # 1.2e6/1e6 - 1
    assert m.mdd_pct == pytest.approx(200_000 / 1_100_000 * 100.0)  # 18.18%
    assert m.win_rate_pct == pytest.approx(50.0)
    assert m.payoff_ratio == pytest.approx(160.0 / 118.0)
    assert m.expectancy_r == pytest.approx((1.6 - 1.18) / 2)  # 0.21
    assert m.avg_hold_days == pytest.approx((10 + 5) / 2)     # 7.5
    assert m.avg_exposure_pct == pytest.approx((0 + 50 + 40 + 60) / 4)  # 37.5
    assert m.total_cost == pytest.approx(20 + 20 + 20 + 18)   # 78
    assert m.n_trades == 2 and m.n_wins == 1 and m.n_losses == 1


def test_cagr_annualizes_over_curve_span() -> None:
    m = compute_metrics(_sample_result())
    # 2020-01-01 ~ 2021-01-01 = 366일(윤년) → 약 1.002년
    years = 366 / 365.25
    expected = ((1_200_000 / 1_000_000) ** (1 / years) - 1) * 100.0
    assert m.cagr_pct == pytest.approx(expected)


def test_exit_breakdown_counts_by_category() -> None:
    m = compute_metrics(_sample_result())
    assert m.exit_breakdown == {"stop": 1, "trend_60ma": 1, "market_defense": 0}


def test_no_trades_metrics_are_safe() -> None:
    empty = BacktestResult(
        start=date(2020, 1, 1), end=date(2020, 2, 1),
        initial_cash=5.0e7, final_cash=5.0e7,
        equity_curve=[
            DailyRecord(date(2020, 1, 1), 5e7, 0.0, 5e7, 0, 0.0,
                        {Market.KOSPI: MarketState.NORMAL}),
        ],
    )
    m = compute_metrics(empty)
    assert m.n_trades == 0
    assert m.win_rate_pct == 0.0
    assert m.payoff_ratio == 0.0
    assert m.expectancy_r == 0.0
    assert m.mdd_pct == 0.0


# --------------------------------------------------------------------------- #
# CSV 스키마·내용
# --------------------------------------------------------------------------- #
def test_write_report_produces_all_files(tmp_path: Path) -> None:
    report = write_report(_sample_result(), tmp_path)
    for key in ("trades", "equity_curve", "events", "metrics_txt", "metrics_json"):
        assert report.paths[key].exists()

    with report.paths["trades"].open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == list(trade_log.HEADER)
    assert len(rows) == 1 + 2  # 헤더 + 트레이드 2

    with report.paths["equity_curve"].open(encoding="utf-8-sig", newline="") as f:
        eq_rows = list(csv.reader(f))
    assert len(eq_rows) == 1 + 4
    # market_state 직렬화
    assert eq_rows[1][-1] == "KOSPI=NORMAL"

    meta = json.loads(report.paths["metrics_json"].read_text(encoding="utf-8"))
    assert meta["n_trades"] == 2
    assert meta["exit_breakdown"]["stop"] == 1


def test_trade_id_groups_by_symbol_and_entry_date() -> None:
    # 같은 종목·같은 진입일의 부분청산 2건 → 동일 trade_id, 재진입은 새 id
    partial1 = _trade("AAA", date(2020, 1, 1), date(2020, 1, 10),
                      100.0, 110.0, 5, 10.0, 10.0, ExitReason.TREND_60MA_HALF, 10.0)
    partial2 = _trade("AAA", date(2020, 1, 1), date(2020, 1, 15),
                      100.0, 105.0, 5, 10.0, 10.0, ExitReason.TREND_60MA_REST, 10.0)
    reentry = _trade("AAA", date(2020, 3, 1), date(2020, 3, 10),
                     100.0, 108.0, 10, 20.0, 20.0, ExitReason.STOP, 10.0)
    result = BacktestResult(
        start=date(2020, 1, 1), end=date(2020, 3, 10),
        initial_cash=1e6, final_cash=1e6,
        trades=[partial1, partial2, reentry],
    )
    rows = trade_log.to_rows(result)
    trade_ids = [r[2] for r in rows]  # trade_id 컬럼
    assert trade_ids == [1, 1, 2]
