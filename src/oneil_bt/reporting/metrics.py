"""성과 지표 (계획서 §9, Phase 7).

`BacktestResult`(자본곡선·트레이드)를 읽어 요약 성과를 계산한다. 전부 순수 함수이며
난수·외부상태에 의존하지 않는다(결정론). 리포트(`report.py`)와 CLI 요약이 이를 쓴다.

지표 정의(규칙서 §6 출력):
- 총수익률: (최종자본/초기자본 − 1). `BacktestResult`와 동일.
- CAGR: 자본곡선 첫~마지막 날짜의 달력일을 연으로 환산해 기하평균 성장률.
- MDD: 자본곡선 최고점 대비 최대 낙폭(양수 %).
- 승률: pnl>0 트레이드 비율.
- 손익비(payoff): 평균이익 / |평균손실|. 손실 트레이드가 없으면 0.
- 기대값(R): pnl_r 평균 — 1R(진입 리스크) 대비 트레이드당 기대 손익.
- 평균 보유기간: hold_days 평균(달력일).
- 평균 노출도: 자본곡선 exposure_pct 평균.
- 총 거래비용: 매칭된 진입(안분)·청산 비용 합.
- 청산 분해: 손절/60MA/방어별 건수.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..domain.enums import ExitReason
from ..engine.context import BacktestResult, TradeRecord

# 청산 사유 → 리포트 분류(손절/추세이탈/시장방어).
_EXIT_CATEGORY: dict[ExitReason, str] = {
    ExitReason.STOP: "stop",
    ExitReason.TREND_60MA_HALF: "trend_60ma",
    ExitReason.TREND_60MA_REST: "trend_60ma",
    ExitReason.TREND_60MA_VOLBREAK: "trend_60ma",
    ExitReason.MARKET_DEFENSE_120MA: "market_defense",
}


@dataclass(frozen=True)
class PerformanceMetrics:
    """성과 요약 (계획서 §9)."""

    total_return_pct: float
    cagr_pct: float
    mdd_pct: float
    win_rate_pct: float
    payoff_ratio: float          # 평균이익 / |평균손실|
    expectancy_r: float          # 트레이드당 기대 R
    avg_hold_days: float
    avg_exposure_pct: float
    total_cost: float
    n_trades: int
    n_wins: int
    n_losses: int
    exit_breakdown: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_return_pct": self.total_return_pct,
            "cagr_pct": self.cagr_pct,
            "mdd_pct": self.mdd_pct,
            "win_rate_pct": self.win_rate_pct,
            "payoff_ratio": self.payoff_ratio,
            "expectancy_r": self.expectancy_r,
            "avg_hold_days": self.avg_hold_days,
            "avg_exposure_pct": self.avg_exposure_pct,
            "total_cost": self.total_cost,
            "n_trades": self.n_trades,
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "exit_breakdown": dict(self.exit_breakdown),
        }


def _max_drawdown_pct(equity: list[float]) -> float:
    """자본곡선 최고점 대비 최대 낙폭(양수 %). 빈/단조증가면 0."""
    peak = float("-inf")
    mdd = 0.0
    for eq in equity:
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    return mdd * 100.0


def _cagr_pct(initial: float, final: float, start: date, end: date) -> float:
    """기간 달력일을 연으로 환산한 연복리 성장률(%). 자본≤0·기간≤0이면 0."""
    if initial <= 0 or final <= 0:
        return 0.0
    days = (end - start).days
    if days <= 0:
        return 0.0
    years = days / 365.25
    return ((final / initial) ** (1.0 / years) - 1.0) * 100.0


def compute_metrics(result: BacktestResult) -> PerformanceMetrics:
    trades: list[TradeRecord] = result.trades
    pnls = [t.closed.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    n = len(trades)
    win_rate = (len(wins) / n * 100.0) if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss < 0 else 0.0
    expectancy_r = (sum(t.closed.pnl_r for t in trades) / n) if n else 0.0
    avg_hold = (sum(t.closed.hold_days for t in trades) / n) if n else 0.0
    total_cost = sum(
        t.closed.entry_fill.cost + t.closed.exit_fill.cost for t in trades
    )

    exposures = [rec.exposure_pct for rec in result.equity_curve]
    avg_exposure = sum(exposures) / len(exposures) if exposures else 0.0

    equity = [rec.equity for rec in result.equity_curve]
    if result.equity_curve:
        start = result.equity_curve[0].date
        end = result.equity_curve[-1].date
    else:
        start, end = result.start, result.end
    cagr = _cagr_pct(result.initial_cash, result.final_equity, start, end)

    breakdown: dict[str, int] = {"stop": 0, "trend_60ma": 0, "market_defense": 0}
    for t in trades:
        cat = _EXIT_CATEGORY.get(t.closed.exit_fill.reason)  # type: ignore[arg-type]
        if cat is not None:
            breakdown[cat] += 1

    return PerformanceMetrics(
        total_return_pct=result.total_return_pct,
        cagr_pct=cagr,
        mdd_pct=_max_drawdown_pct(equity),
        win_rate_pct=win_rate,
        payoff_ratio=payoff,
        expectancy_r=expectancy_r,
        avg_hold_days=avg_hold,
        avg_exposure_pct=avg_exposure,
        total_cost=total_cost,
        n_trades=n,
        n_wins=len(wins),
        n_losses=len(losses),
        exit_breakdown=breakdown,
    )


def format_metrics(m: PerformanceMetrics) -> str:
    """성과 요약 텍스트(사람이 읽는 리포트)."""
    b = m.exit_breakdown
    lines = [
        f"총수익률       : {m.total_return_pct:+.2f}%",
        f"CAGR           : {m.cagr_pct:+.2f}%",
        f"MDD            : -{m.mdd_pct:.2f}%",
        f"승률           : {m.win_rate_pct:.1f}%  ({m.n_wins}승 {m.n_losses}패 / {m.n_trades}트레이드)",
        f"손익비         : {m.payoff_ratio:.2f}",
        f"기대값(R)      : {m.expectancy_r:+.3f}R",
        f"평균 보유기간  : {m.avg_hold_days:.1f}일",
        f"평균 노출도    : {m.avg_exposure_pct:.1f}%",
        f"총 거래비용    : {m.total_cost:,.0f}",
        f"청산 분해      : 손절 {b.get('stop', 0)} / 60MA {b.get('trend_60ma', 0)} / 방어 {b.get('market_defense', 0)}",
    ]
    return "\n".join(lines)
