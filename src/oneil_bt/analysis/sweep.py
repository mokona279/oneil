"""파라미터 민감도 스윕 러너 (계획서 §11 후속과제).

축(config 점 경로)마다 시도할 값 목록을 받아 데카르트 곱의 각 조합으로 백테스트를
재실행하고, 조합별 성과지표를 한 행으로 모은다. 규칙 수치가 전부 config로 외부화돼
있으므로(구조는 v1에서 확보) 이 그리드 실행기만 얹으면 민감도 분석이 가능하다.

- **결정론**: 축 순서·값 순서를 보존한다(`itertools.product`) → 재현 가능한 행 순서.
  엔진 자체도 결정론이므로 동일 그리드는 언제나 동일 표를 낸다.
- **격리**: base `Config`와 `source`는 불변. 조합마다 오버라이드를 적용한 새 Config로
  새 엔진을 만든다(지표 캐시가 config에 의존하므로 조합 간 공유하지 않는다).

CSV는 축 열 + 지표 열로 조합당 1행. 리포팅과 동일한 `write_csv`(utf-8-sig)를 재사용해
엑셀/한글 호환과 골든 재현성을 맞춘다.
"""

from __future__ import annotations

import itertools
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ..data.datasource import DataSource
from ..domain.config import Config
from ..engine.engine import BacktestEngine
from ..reporting.metrics import PerformanceMetrics, compute_metrics
from ..reporting.writer import write_csv
from .capture_report import capture_stats
from .override import apply_overrides


# --------------------------------------------------------------------------- #
# 그리드 정의
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ParameterGrid:
    """축(config 점 경로) → 시도할 값들. 축 순서가 곧 열·조합 순서다."""

    axes: tuple[tuple[str, tuple[Any, ...]], ...]

    @staticmethod
    def from_mapping(m: Mapping[str, Sequence[Any]]) -> "ParameterGrid":
        axes = tuple((str(name), tuple(values)) for name, values in m.items())
        for name, values in axes:
            if not values:
                raise ValueError(f"그리드 축 '{name}' 에 값이 없다")
        return ParameterGrid(axes)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.axes)

    def combinations(self) -> list[dict[str, Any]]:
        """축별 값의 데카르트 곱을 축 순서 유지한 오버라이드 dict 목록으로."""
        if not self.axes:
            return [{}]
        names = self.names
        value_lists = [values for _, values in self.axes]
        return [dict(zip(names, combo)) for combo in itertools.product(*value_lists)]

    def __len__(self) -> int:
        total = 1
        for _, values in self.axes:
            total *= len(values)
        return total


# --------------------------------------------------------------------------- #
# 결과
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SweepRow:
    """한 파라미터 조합의 결과 — 오버라이드 + 성과지표 + 엔진 레벨 부가값.

    capture_* 는 캡처 세트(개선계획 §3.3)를 넘긴 스윕에서만 채워진다(없으면 None →
    CSV에서 빈 칸). 캡처율로 정렬하려면 세트를 넘긴 스윕이어야 한다.
    """

    overrides: dict[str, Any]
    metrics: PerformanceMetrics
    final_equity: float
    max_exposure_pct: float
    capture_rate: float | None = None
    capture_sum_r: float | None = None


@dataclass(frozen=True)
class SweepResult:
    axes: tuple[str, ...]
    rows: tuple[SweepRow, ...]

    def ranked(
        self, key: str = "total_return_pct", *, reverse: bool = True
    ) -> list[SweepRow]:
        """성과지표(또는 부가 필드) 기준 정렬. 동점은 원래(그리드) 순서 유지(안정정렬)."""
        return sorted(self.rows, key=lambda r: _metric_value(r, key), reverse=reverse)


# 조합당 CSV로 낼 지표 열: (열이름, 행→값). 축 열 뒤에 이 순서로 붙는다.
_METRIC_COLUMNS: tuple[tuple[str, Callable[[SweepRow], Any]], ...] = (
    ("total_return_pct", lambda r: r.metrics.total_return_pct),
    ("cagr_pct", lambda r: r.metrics.cagr_pct),
    ("mdd_pct", lambda r: r.metrics.mdd_pct),
    ("win_rate_pct", lambda r: r.metrics.win_rate_pct),
    ("payoff_ratio", lambda r: r.metrics.payoff_ratio),
    ("expectancy_r", lambda r: r.metrics.expectancy_r),
    ("avg_hold_days", lambda r: r.metrics.avg_hold_days),
    ("avg_exposure_pct", lambda r: r.metrics.avg_exposure_pct),
    ("max_exposure_pct", lambda r: r.max_exposure_pct),
    ("total_cost", lambda r: r.metrics.total_cost),
    ("n_trades", lambda r: r.metrics.n_trades),
    ("n_wins", lambda r: r.metrics.n_wins),
    ("n_losses", lambda r: r.metrics.n_losses),
    ("n_stop", lambda r: r.metrics.exit_breakdown.get("stop", 0)),
    ("final_equity", lambda r: r.final_equity),
    ("capture_rate", lambda r: "" if r.capture_rate is None else r.capture_rate),
    ("capture_sum_r", lambda r: "" if r.capture_sum_r is None else r.capture_sum_r),
)

_METRIC_GETTERS: dict[str, Callable[[SweepRow], Any]] = dict(_METRIC_COLUMNS)


def _metric_value(row: SweepRow, key: str) -> Any:
    getter = _METRIC_GETTERS.get(key)
    if getter is None:
        raise KeyError(
            f"알 수 없는 정렬 지표 '{key}'. 가능: {', '.join(_METRIC_GETTERS)}"
        )
    return getter(row)


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #
def run_sweep(
    source: DataSource,
    base_cfg: Config,
    grid: ParameterGrid,
    start: date,
    end: date,
    *,
    initial_cash: float = 1.0e8,
    symbols: list[str] | None = None,
    capture_symbols: Collection[str] | None = None,
) -> SweepResult:
    """그리드의 모든 조합으로 백테스트를 돌려 조합별 성과지표를 수집한다.

    capture_symbols(캡처 세트, 개선계획 §3.3)를 주면 조합마다 캡처율·캡처 합산 R을
    trades만으로 계산해 함께 담는다(진단 기록 불필요).
    """
    rows: list[SweepRow] = []
    for overrides in grid.combinations():
        cfg = apply_overrides(base_cfg, overrides)
        result = BacktestEngine(source, cfg, initial_cash=initial_cash).run(
            start, end, symbols=symbols
        )
        metrics = compute_metrics(result)
        max_expo = max(
            (rec.exposure_pct for rec in result.equity_curve), default=0.0
        )
        capture_rate: float | None = None
        capture_sum_r: float | None = None
        if capture_symbols is not None:
            capture_rate, capture_sum_r = capture_stats(
                ((t.closed.symbol, t.closed.pnl_r) for t in result.trades),
                capture_symbols,
            )
        rows.append(
            SweepRow(
                overrides=dict(overrides),
                metrics=metrics,
                final_equity=result.final_equity,
                max_exposure_pct=max_expo,
                capture_rate=capture_rate,
                capture_sum_r=capture_sum_r,
            )
        )
    return SweepResult(axes=grid.names, rows=tuple(rows))


# --------------------------------------------------------------------------- #
# 표 직렬화 (CSV·콘솔 공용)
# --------------------------------------------------------------------------- #
def _render(value: Any) -> Any:
    """축 값이 Enum이면 사람이 읽는 원값(.value)으로. 그 외는 그대로."""
    return value.value if isinstance(value, Enum) else value


def sweep_table(result: SweepResult) -> tuple[list[str], list[list[Any]]]:
    """(헤더, 행들) — 축 열 + 지표 열. CSV 기록과 콘솔 출력이 공유한다."""
    header = list(result.axes) + [name for name, _ in _METRIC_COLUMNS]
    rows: list[list[Any]] = []
    for row in result.rows:
        line: list[Any] = [_render(row.overrides[axis]) for axis in result.axes]
        line += [getter(row) for _, getter in _METRIC_COLUMNS]
        rows.append(line)
    return header, rows


def write_sweep_csv(result: SweepResult, path: Path | str) -> None:
    header, rows = sweep_table(result)
    write_csv(path, header, rows)


def format_sweep(
    result: SweepResult,
    *,
    sort_key: str = "total_return_pct",
    reverse: bool = True,
    columns: Iterable[str] = ("total_return_pct", "cagr_pct", "mdd_pct",
                             "win_rate_pct", "expectancy_r", "n_trades"),
) -> str:
    """랭킹 콘솔 표(고정폭). 축 열 + 지정 지표 열, sort_key 기준 정렬."""
    ranked = result.ranked(sort_key, reverse=reverse)
    cols = list(columns)
    header = list(result.axes) + cols
    lines_data: list[list[str]] = [header]
    for row in ranked:
        cells = [str(_render(row.overrides[axis])) for axis in result.axes]
        cells += [_fmt_num(_METRIC_GETTERS[c](row)) for c in cols]
        lines_data.append(cells)

    widths = [max(len(r[i]) for r in lines_data) for i in range(len(header))]
    out = []
    for i, row in enumerate(lines_data):
        out.append("  ".join(cell.rjust(widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            out.append("  ".join("-" * widths[j] for j in range(len(header))))
    return "\n".join(out)


def _fmt_num(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)
