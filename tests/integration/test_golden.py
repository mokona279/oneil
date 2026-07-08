"""골든 회귀 — 결정론 해시 + 파라미터 민감도 (계획서 §8 Phase 8, §10).

- **결정론**: 동일 입력·설정으로 2회 실행 → 완전히 동일한 산출(자본곡선·트레이드·이벤트).
- **골든 해시**: 산출 다이제스트를 리터럴로 고정 → 엔진·데이터의 의도치 않은 변화가
  회귀로 잡힌다. `data_example/generate.py`나 규칙 로직을 의도적으로 바꿨다면 아래
  GOLDEN 상수를 새 값으로 갱신한다(그 변경이 정당한지 리뷰가 판단).
- **민감도**: 설정 1개(비중 상한)를 바꾸면 결과가 달라진다 → 파라미터가 실제로 배선됨.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from oneil_bt.data.csv_source import CsvDataSource
from oneil_bt.domain.config import Config
from oneil_bt.engine.context import BacktestResult
from oneil_bt.engine.engine import BacktestEngine

from .conftest import END, START

INITIAL_CASH = 1.0e8

# 결정론 골든 다이제스트. data_example/규칙 로직을 의도적으로 바꾸면 갱신한다.
GOLDEN_DIGEST = "340947b2a57a6228188af4890d29a710c6ba37332f67e27849c7786b34467357"


def _digest(result: BacktestResult) -> str:
    """자본곡선·트레이드·이벤트를 결정론적 문자열로 직렬화해 SHA-256."""
    parts: list[str] = []
    for rec in result.equity_curve:
        parts.append(f"E|{rec.date}|{rec.equity:.4f}|{rec.cash:.4f}|{rec.n_positions}")
    for t in result.trades:
        c = t.closed
        parts.append(
            f"T|{c.symbol}|{c.entry_fill.date}|{c.exit_fill.date}|"
            f"{c.pnl:.4f}|{c.exit_fill.reason}|{c.is_stop}"
        )
    for e in result.events:
        parts.append(f"V|{e.date}|{e.symbol}|{e.event}")
    blob = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _run(source: CsvDataSource, cfg: Config) -> BacktestResult:
    return BacktestEngine(source, cfg, initial_cash=INITIAL_CASH).run(START, END)


# --------------------------------------------------------------------------- #
# 결정론 — 2회 실행 동일
# --------------------------------------------------------------------------- #
def test_two_runs_are_bit_identical(source: CsvDataSource, cfg: Config) -> None:
    r1, r2 = _run(source, cfg), _run(source, cfg)
    assert _digest(r1) == _digest(r2)
    # 세부까지: 트레이드·이벤트 수, 최종자본.
    assert len(r1.trades) == len(r2.trades)
    assert [e.event for e in r1.events] == [e.event for e in r2.events]
    assert r1.final_equity == pytest.approx(r2.final_equity)


# --------------------------------------------------------------------------- #
# 골든 해시 — 회귀 감시
# --------------------------------------------------------------------------- #
def test_matches_golden_digest(source: CsvDataSource, cfg: Config) -> None:
    assert _digest(_run(source, cfg)) == GOLDEN_DIGEST


# --------------------------------------------------------------------------- #
# 파라미터 민감도 — 비중 상한을 바꾸면 결과가 달라진다
# --------------------------------------------------------------------------- #
def test_parameter_change_alters_result(source: CsvDataSource, cfg: Config) -> None:
    base = _run(source, cfg)
    tighter = replace(cfg, sizing=replace(cfg.sizing, max_weight_pct=5.0))
    changed = _run(source, tighter)

    assert _digest(base) != _digest(changed), "비중 상한 변경이 결과에 반영돼야 한다"
    # 상한을 20%→5%로 조이면 매수 규모가 줄어 노출도 최댓값이 낮아진다.
    base_max_expo = max(r.exposure_pct for r in base.equity_curve)
    changed_max_expo = max(r.exposure_pct for r in changed.equity_curve)
    assert changed_max_expo < base_max_expo
