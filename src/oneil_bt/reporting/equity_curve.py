"""일별 자본곡선 CSV (계획서 §9, Phase 7).

`DailyRecord` 한 행을 §9 스키마로 기록한다. `market_state`는 시장별 상태 dict을
`KOSPI=NORMAL;KOSDAQ=CAUTION`처럼 시장 사전순으로 직렬화(단일 컬럼, 결정론).
"""

from __future__ import annotations

from pathlib import Path

from ..engine.context import BacktestResult, DailyRecord
from .writer import write_csv

HEADER = (
    "date", "cash", "holdings_value", "equity",
    "n_positions", "exposure_pct", "market_state",
)


def _market_state_str(rec: DailyRecord) -> str:
    return ";".join(
        f"{str(m)}={str(s)}" for m, s in sorted(rec.market_states.items(), key=lambda kv: str(kv[0]))
    )


def to_rows(result: BacktestResult) -> list[tuple]:
    return [
        (
            rec.date.isoformat(),
            round(rec.cash, 2),
            round(rec.holdings_value, 2),
            round(rec.equity, 2),
            rec.n_positions,
            round(rec.exposure_pct, 4),
            _market_state_str(rec),
        )
        for rec in result.equity_curve
    ]


def write(result: BacktestResult, path: Path | str) -> None:
    write_csv(path, HEADER, to_rows(result))
