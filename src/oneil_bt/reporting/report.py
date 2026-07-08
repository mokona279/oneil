"""리포트 조립·출력 (계획서 §9, Phase 7).

`BacktestResult`를 받아 출력 디렉토리에 4종 산출물을 쓴다:
- `trades.csv`      — 트레이드 로그(§9)
- `equity_curve.csv`— 일별 자본곡선(§9)
- `events.csv`      — 육안검증 이벤트 목록(§9)
- `metrics.txt` / `metrics.json` — 성과 요약(사람이 읽는 텍스트 + 기계 판독 JSON)

`write_report`는 파일을 쓰고, 쓴 파일 경로 dict과 계산된 `PerformanceMetrics`를 담은
`Report`를 돌려준다. CLI는 이를 표준출력 요약에 재사용한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..engine.context import BacktestResult
from . import equity_curve, event_list, trade_log
from .metrics import PerformanceMetrics, compute_metrics, format_metrics


@dataclass(frozen=True)
class Report:
    metrics: PerformanceMetrics
    paths: dict[str, Path]

    def summary(self) -> str:
        return format_metrics(self.metrics)


def write_report(result: BacktestResult, out_dir: Path | str) -> Report:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths = {
        "trades": out / "trades.csv",
        "equity_curve": out / "equity_curve.csv",
        "events": out / "events.csv",
        "metrics_txt": out / "metrics.txt",
        "metrics_json": out / "metrics.json",
    }

    trade_log.write(result, paths["trades"])
    equity_curve.write(result, paths["equity_curve"])
    event_list.write(result, paths["events"])

    metrics = compute_metrics(result)
    paths["metrics_txt"].write_text(format_metrics(metrics) + "\n", encoding="utf-8")
    paths["metrics_json"].write_text(
        json.dumps(metrics.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return Report(metrics=metrics, paths=paths)
