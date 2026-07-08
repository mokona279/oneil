"""육안검증 이벤트 목록 CSV (계획서 §9, Phase 7).

자동 필터가 V자 회복과 정상 조정을 완전히 구분하지 못하므로, 돌파 후보·추격 스킵·
거래량 실패·진입/청산 등의 이벤트를 차트 확인용으로 남긴다. §9 스키마 컬럼(pivot·
depth_pct·weeks·stage)은 이벤트별 detail에서 있으면 채우고 없으면 공란이다.
"""

from __future__ import annotations

from pathlib import Path

from ..engine.context import BacktestResult
from .writer import write_csv

HEADER = ("date", "symbol", "event", "pivot", "depth_pct", "weeks", "stage")


def _num(detail: dict, key: str, ndigits: int) -> object:
    v = detail.get(key)
    return round(v, ndigits) if isinstance(v, (int, float)) else ""


def to_rows(result: BacktestResult) -> list[tuple]:
    rows: list[tuple] = []
    for e in result.events:
        rows.append((
            e.date.isoformat(),
            e.symbol,
            e.event,
            _num(e.detail, "pivot", 4),
            _num(e.detail, "depth_pct", 4),
            _num(e.detail, "weeks", 2),
            e.detail.get("stage", ""),
        ))
    return rows


def write(result: BacktestResult, path: Path | str) -> None:
    write_csv(path, HEADER, to_rows(result))
