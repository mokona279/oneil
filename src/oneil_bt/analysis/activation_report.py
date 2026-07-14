"""저표본 개정 발동 집계기 — §3.3 추적 의무 (P2 승인 조건, P3~P5 상시).

`rule_activations.csv`(진단)와 `trades.csv`를 조인해 규칙별 발동 건수·관련 트레이드
손익을 집계한다. 매 Phase의 후보·최종 전 유니버스 실행마다 이 표를 트레이드오프
표에 병기한다(개선계획 §3.3 의무 ①).

집계 의미(주의):
- 진입형 규칙(r3b_reset_entry, r4a_handle_entry): 발동일 = 진입일 → 그 트레이드들의
  손익 합이 곧 "그 규칙이 연 트레이드"의 직접 귀속이다. 단, 자본 경합의 2차 효과
  (다른 트레이드의 수량 변화)는 포함하지 않는다 — 순효과는 반사실(twin) 실행 차분으로
  별도 측정한다.
- q11_stop_clamp: 발동일은 피라미딩 재계산일이라 트레이드 진입일과 다르다 — 발동일이
  보유 구간 [진입일, 청산일]에 속하는 트레이드로 조인한다. 이 트레이드 손익은 "클램프가
  개입한 포지션"의 손익이지 클램프의 기여가 아니다(기여는 twin 차분).

입력은 순수 DataFrame(오프라인 CSV든 인메모리든 동일 경로) — capture_report와 동일 관례.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..reporting.writer import write_csv

ACTIVATION_REPORT_HEADER = (
    "rule", "n_activations", "n_symbols", "n_trades",
    "sum_pnl", "sum_r", "contribution_pct",
)

_ENTRY_RULES = ("r3b_reset_entry", "r4a_handle_entry")


def build_activation_report(
    activations_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    initial_cash: float,
) -> pd.DataFrame:
    """규칙별 1행 요약. 발동이 없는 규칙은 행을 내지 않는다(0행 = 무발동 증빙).

    activations_df: rule_activations.csv 스키마(date, symbol, rule[, detail]).
    trades_df: trades.csv 스키마(symbol, entry_date, exit_date, pnl, pnl_r 사용).
    """
    rows = []
    if len(activations_df) == 0:
        return pd.DataFrame(rows, columns=list(ACTIVATION_REPORT_HEADER))
    tr = trades_df.copy()
    for rule, grp in activations_df.groupby("rule", sort=True):
        if rule in _ENTRY_RULES:
            # 진입일 조인 — 부분청산으로 트레이드 행이 여러 개일 수 있어 전부 귀속.
            keys = set(zip(grp["symbol"], grp["date"]))
            hit = tr[[
                (s, d) in keys for s, d in zip(tr["symbol"], tr["entry_date"])
            ]] if len(tr) else tr
        else:
            # 보유 구간 조인(클램프 등 경로 개입형).
            mask = pd.Series(False, index=tr.index)
            for sym, d in zip(grp["symbol"], grp["date"]):
                mask |= (
                    (tr["symbol"] == sym)
                    & (tr["entry_date"] <= d) & (d <= tr["exit_date"])
                )
            hit = tr[mask] if len(tr) else tr
        rows.append(dict(
            rule=rule,
            n_activations=len(grp),
            n_symbols=grp["symbol"].nunique(),
            n_trades=len(hit),
            sum_pnl=round(float(hit["pnl"].sum()), 2) if len(hit) else 0.0,
            sum_r=round(float(hit["pnl_r"].sum()), 4) if len(hit) else 0.0,
            contribution_pct=(
                round(float(hit["pnl"].sum()) / initial_cash * 100.0, 4)
                if len(hit) and initial_cash > 0 else 0.0
            ),
        ))
    return pd.DataFrame(rows, columns=list(ACTIVATION_REPORT_HEADER))


def build_activation_report_from_dir(
    run_dir: Path | str, initial_cash: float
) -> pd.DataFrame:
    """백테스트 산출 폴더(rule_activations.csv·trades.csv)에서 집계."""
    run_dir = Path(run_dir)
    acts = pd.read_csv(run_dir / "rule_activations.csv", dtype={"symbol": str})
    trades = pd.read_csv(run_dir / "trades.csv", dtype={"symbol": str})
    return build_activation_report(acts, trades, initial_cash)


def write_activation_report(report: pd.DataFrame, path: Path | str) -> None:
    write_csv(path, ACTIVATION_REPORT_HEADER, report.itertuples(index=False))
