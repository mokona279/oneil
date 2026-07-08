"""트레이드 로그 CSV (계획서 §9, Phase 7).

각 `TradeRecord`(진입·청산 매칭 1행)를 §9 스키마 컬럼으로 전개한다. 부분 청산(60MA
절반→잔량)은 같은 포지션에서 여러 행이 되며, `trade_id`로 묶는다: (심볼, 진입일)이
같으면 동일 포지션의 트랜치/부분청산으로 보고 같은 id를 부여(등장 순서대로 1부터).

진입 비용·수량은 청산 수량에 안분된 매칭 값이다(`ClosedTrade` 회계와 동일).
"""

from __future__ import annotations

from pathlib import Path

from ..engine.context import BacktestResult, TradeRecord
from .writer import write_csv

HEADER = (
    "symbol", "market", "trade_id", "tranche_no",
    "entry_reason", "entry_date", "entry_price", "entry_qty", "entry_cost",
    "exit_reason", "exit_date", "exit_price", "exit_qty", "exit_cost",
    "pnl", "pnl_r", "hold_days", "base_stage", "pivot",
)


def _trade_ids(trades: list[TradeRecord]) -> list[int]:
    """포지션 식별자 부여 — (심볼, 진입일)별로 등장 순서대로 1부터."""
    ids: dict[tuple[str, object], int] = {}
    out: list[int] = []
    for t in trades:
        key = (t.closed.symbol, t.closed.entry_fill.date)
        if key not in ids:
            ids[key] = len(ids) + 1
        out.append(ids[key])
    return out


def to_rows(result: BacktestResult) -> list[tuple]:
    rows: list[tuple] = []
    ids = _trade_ids(result.trades)
    for tid, t in zip(ids, result.trades):
        c = t.closed
        rows.append((
            c.symbol,
            str(c.market),
            tid,
            c.tranche_no,
            str(c.entry_fill.reason),
            c.entry_fill.date.isoformat(),
            round(c.entry_fill.price, 4),
            c.entry_fill.qty,
            round(c.entry_fill.cost, 2),
            str(c.exit_fill.reason),
            c.exit_fill.date.isoformat(),
            round(c.exit_fill.price, 4),
            c.exit_fill.qty,
            round(c.exit_fill.cost, 2),
            round(c.pnl, 2),
            round(c.pnl_r, 4),
            c.hold_days,
            t.base_stage,
            round(t.pivot, 4),
        ))
    return rows


def write(result: BacktestResult, path: Path | str) -> None:
    write_csv(path, HEADER, to_rows(result))
