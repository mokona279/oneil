"""캡처 리포트 집계기 — 세트 × 백테스트 산출물 (개선계획 §3.3·§7, P0-T3).

캡처 세트(대시세 종목)의 각 종목에 대해 "전략이 참여했는가"를 집계한다:
- entered            — 진입 발생 여부 (§2 합격 게이트)
- n_trades / sum_r   — 트레이드 수·합산 R
- contribution_pct   — 계좌 기여 %p (합산 pnl / 초기자본)
- bottleneck         — 미진입 시 퍼널에서 처음 0이 된 단계명

요약에는 캡처율(세트 중 진입 비율)과 배수 티어별(≥2/3/4/5×) 캡처율을 함께 낸다 —
세트 임계(Q8) 재상정과 무관하게 어느 티어로든 읽을 수 있게 한다.

입력은 순수 DataFrame(오프라인 CSV든 인메모리 결과든 동일 경로):
- capture_df — `build_capture_set` 출력(capture_set.csv). turnover_ok 행만 세트 본체.
- trades_df  — trades.csv 스키마(symbol, pnl, pnl_r 사용).
- funnel_df  — entry_funnel.csv 스키마(없으면 bottleneck은 "n/a").

`capture_stats`는 스윕이 조합마다 호출하는 최소 통계(캡처율·합산 R)다.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from pathlib import Path

import pandas as pd

from ..reporting.writer import write_csv

CAPTURE_REPORT_HEADER = (
    "symbol", "first_achieved", "max_multiple", "entered",
    "n_trades", "sum_r", "contribution_pct", "bottleneck",
)

# entry_funnel.csv의 깔때기 순서(잔존 카운트) — 처음 0이 된 필드가 병목이다.
_FUNNEL_ORDER = (
    "shopped", "base_present", "stage_ok", "breakout",
    "gate_trend_ok", "gate_rs_ok", "gate_market_ok", "gate_quality_ok",
    "gates_all_ok",
)

MULTIPLE_TIERS = (2.0, 3.0, 4.0, 5.0)


def bottleneck_from_funnel(row: pd.Series | dict) -> str:
    """퍼널 잔존 카운트에서 처음 0이 된 단계명. 게이트까지 다 통과했는데
    미진입이면 포트폴리오 제약(슬롯·현금)이다."""
    for field in _FUNNEL_ORDER:
        if int(row[field]) == 0:
            return field
    return "portfolio_constraint"


def capture_stats(
    trades: Iterable[tuple[str, float]],
    capture_symbols: Collection[str],
) -> tuple[float, float]:
    """(캡처율 %, 캡처 종목 합산 R). trades는 (symbol, pnl_r) 쌍.

    스윕이 조합마다 호출한다 — 진단 기록 없이 trades만으로 계산한다.
    """
    symbols = set(capture_symbols)
    if not symbols:
        return 0.0, 0.0
    entered: set[str] = set()
    sum_r = 0.0
    for sym, pnl_r in trades:
        if sym in symbols:
            entered.add(sym)
            sum_r += pnl_r
    return len(entered) / len(symbols) * 100.0, sum_r


def build_capture_report(
    capture_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    funnel_df: pd.DataFrame | None,
    initial_cash: float,
    *,
    require_turnover: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """종목별 리포트 DataFrame + 요약 dict.

    요약: n_set, n_entered, capture_rate_pct, capture_sum_r,
          tier_rates {배수: 캡처율}, bottlenecks {단계명: 종목 수(미진입만)}.
    """
    core = capture_df[capture_df["turnover_ok"]] if require_turnover else capture_df
    by_sym = (
        trades_df.groupby("symbol").agg(n_trades=("pnl_r", "size"),
                                        sum_r=("pnl_r", "sum"),
                                        pnl=("pnl", "sum"))
        if len(trades_df)
        else pd.DataFrame(columns=["n_trades", "sum_r", "pnl"])
    )
    funnel_by_sym = funnel_df.set_index("symbol") if funnel_df is not None else None

    rows = []
    for rec in core.sort_values("symbol").itertuples():
        sym = str(rec.symbol)
        entered = sym in by_sym.index
        n_trades = int(by_sym.loc[sym, "n_trades"]) if entered else 0
        sum_r = float(by_sym.loc[sym, "sum_r"]) if entered else 0.0
        contribution = (
            float(by_sym.loc[sym, "pnl"]) / initial_cash * 100.0 if entered else 0.0
        )
        if entered:
            bottleneck = ""
        elif funnel_by_sym is None:
            bottleneck = "n/a"
        elif sym not in funnel_by_sym.index:
            bottleneck = "not_in_run"
        else:
            bottleneck = bottleneck_from_funnel(funnel_by_sym.loc[sym])
        rows.append(dict(
            symbol=sym,
            first_achieved=rec.first_achieved,
            max_multiple=float(rec.max_multiple),
            entered=entered,
            n_trades=n_trades,
            sum_r=round(sum_r, 4),
            contribution_pct=round(contribution, 4),
            bottleneck=bottleneck,
        ))
    report = pd.DataFrame(rows, columns=list(CAPTURE_REPORT_HEADER))

    n_set = len(report)
    n_entered = int(report["entered"].sum()) if n_set else 0
    tier_rates = {}
    for tier in MULTIPLE_TIERS:
        sub = report[report["max_multiple"] >= tier]
        tier_rates[tier] = (
            float(sub["entered"].mean() * 100.0) if len(sub) else 0.0
        )
    missed = report[~report["entered"]]
    summary = dict(
        n_set=n_set,
        n_entered=n_entered,
        capture_rate_pct=(n_entered / n_set * 100.0) if n_set else 0.0,
        capture_sum_r=float(report["sum_r"].sum()),
        tier_rates=tier_rates,
        bottlenecks=missed["bottleneck"].value_counts().to_dict(),
    )
    return report, summary


def build_capture_report_from_dir(
    run_dir: Path | str,
    capture_csv: Path | str,
    initial_cash: float,
) -> tuple[pd.DataFrame, dict]:
    """백테스트 산출 폴더(trades.csv·entry_funnel.csv)와 capture_set.csv에서 집계."""
    run_dir = Path(run_dir)
    capture_df = pd.read_csv(capture_csv, dtype={"symbol": str})
    trades_df = pd.read_csv(run_dir / "trades.csv", dtype={"symbol": str})
    funnel_path = run_dir / "entry_funnel.csv"
    funnel_df = (
        pd.read_csv(funnel_path, dtype={"symbol": str})
        if funnel_path.exists()
        else None
    )
    return build_capture_report(capture_df, trades_df, funnel_df, initial_cash)


def write_capture_report(report: pd.DataFrame, path: Path | str) -> None:
    write_csv(path, CAPTURE_REPORT_HEADER, report.itertuples(index=False))


def format_capture_summary(summary: dict) -> str:
    """요약 텍스트(사람이 읽는 리포트)."""
    tiers = "  ".join(
        f"≥{tier:g}×: {rate:.1f}%" for tier, rate in summary["tier_rates"].items()
    )
    lines = [
        f"캡처 세트       : {summary['n_set']}종목",
        f"캡처율          : {summary['capture_rate_pct']:.1f}%  ({summary['n_entered']}종목 진입)",
        f"티어별 캡처율   : {tiers}",
        f"캡처 합산 R     : {summary['capture_sum_r']:+.1f}R",
    ]
    if summary["bottlenecks"]:
        top = ", ".join(
            f"{name} {cnt}" for name, cnt in
            sorted(summary["bottlenecks"].items(), key=lambda kv: -kv[1])
        )
        lines.append(f"미진입 병목     : {top}")
    return "\n".join(lines)
