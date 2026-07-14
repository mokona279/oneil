"""진단 리포터 — 진입 퍼널·게이트 분해·현 베이스 단계 CSV (계획서 §11 후속).

엔진이 run 중 `record_diagnostics=True`로 채운 `BacktestResult`의 진단 필드를
결정론적 CSV로 낸다. 다른 리포터와 동일한 `write_csv`(utf-8-sig+\n)를 재사용해
엑셀/한글 호환·골든 재현성을 맞춘다. 진단 필드가 비어 있으면 헤더만 있는 파일을 낸다.

산출:
- entry_funnel.csv     — 종목별 진입 퍼널(각 게이트 통과 세션 수). "왜 안/샀나" 요약.
- gate_breakdown.csv   — 돌파(기회)일마다 게이트 개별 판정. n_failed=1 은 니어미스.
- base_stage.csv       — 종료 시점 종목별 현 베이스 단계 + 유효 돌파 이력 요약.
- rule_activations.csv — 저표본 개정 발동 로그(§3.3 추적 의무): R3b 리셋 경유 진입·
                         Q11 클램프·R4a 핸들 진입. detail은 정렬 키 JSON(결정론).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..engine.context import BacktestResult
from .writer import write_csv

ENTRY_FUNNEL_HEADER = (
    "symbol", "sessions", "held", "shopped", "base_present", "stage_ok",
    "breakout", "gate_trend_ok", "gate_rs_ok", "gate_market_ok",
    "gate_quality_ok", "gates_all_ok", "entered",
)
GATE_BREAKDOWN_HEADER = (
    "date", "symbol", "stage", "depth_pct", "weeks_elapsed", "pivot",
    "trend_ok", "rs_ok", "market_ok", "overheat_ok", "atr_ok",
    "contraction_ok", "dryup_ok", "all_pass", "n_failed",
)
BASE_STAGE_HEADER = (
    "symbol", "as_of", "has_base", "stage", "pivot", "depth_pct",
    "weeks_elapsed", "tier", "n_breakouts", "max_stage_reached",
    "last_breakout_date", "last_breakout_stage",
)
RULE_ACTIVATIONS_HEADER = ("date", "symbol", "rule", "detail")


def _b(x: bool) -> str:
    return "1" if x else "0"


def _num(x: float | None, fmt: str = "{:.4f}") -> str:
    return "" if x is None else fmt.format(x)


def _int(x: int | None) -> Any:
    return "" if x is None else x


def write_entry_funnel(result: BacktestResult, path: Path | str) -> None:
    sessions = len(result.equity_curve)
    rows = []
    for sym in sorted(result.entry_funnel):
        f = result.entry_funnel[sym]
        rows.append([
            sym, sessions, f.held, f.shopped, f.base_present, f.stage_ok,
            f.breakout, f.gate_trend_ok, f.gate_rs_ok, f.gate_market_ok,
            f.gate_quality_ok, f.gates_all_ok, f.entered,
        ])
    write_csv(path, ENTRY_FUNNEL_HEADER, rows)


def write_gate_breakdown(result: BacktestResult, path: Path | str) -> None:
    # 엔진 방출 순서(세션 오름차순 → 심볼 사전순) = 결정론. 재정렬하지 않는다.
    rows = [
        [
            r.date.isoformat(), r.symbol, r.stage, _num(r.depth_pct),
            _num(r.weeks_elapsed), _num(r.pivot),
            _b(r.trend_ok), _b(r.rs_ok), _b(r.market_ok), _b(r.overheat_ok),
            _b(r.atr_ok), _b(r.contraction_ok), _b(r.dryup_ok),
            _b(r.all_pass), r.n_failed,
        ]
        for r in result.gate_breakdown
    ]
    write_csv(path, GATE_BREAKDOWN_HEADER, rows)


def write_rule_activations(result: BacktestResult, path: Path | str) -> None:
    # 엔진 방출 순서(세션 오름차순) = 결정론. detail은 키 정렬 JSON.
    rows = [
        [
            a.date.isoformat(), a.symbol, a.rule,
            json.dumps(a.detail, ensure_ascii=False, sort_keys=True),
        ]
        for a in result.rule_activations
    ]
    write_csv(path, RULE_ACTIVATIONS_HEADER, rows)


def write_base_stage(result: BacktestResult, path: Path | str) -> None:
    rows = []
    for sym in sorted(result.base_stages):
        s = result.base_stages[sym]
        rows.append([
            sym, s.as_of.isoformat(), _b(s.has_base), _int(s.stage),
            _num(s.pivot), _num(s.depth_pct), _num(s.weeks_elapsed),
            s.tier or "", s.n_breakouts, s.max_stage_reached,
            s.last_breakout_date.isoformat() if s.last_breakout_date else "",
            _int(s.last_breakout_stage),
        ])
    write_csv(path, BASE_STAGE_HEADER, rows)
