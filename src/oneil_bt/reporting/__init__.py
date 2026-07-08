"""리포팅 레이어 (계획서 §9, Phase 7).

백테스트 산출물(`BacktestResult`)을 트레이드 로그·자본곡선·이벤트 목록 CSV와 성과
지표로 변환한다. 공개 진입점은 `write_report`(전체 출력)와 `compute_metrics`/
`format_metrics`(지표만).
"""

from __future__ import annotations

from .metrics import PerformanceMetrics, compute_metrics, format_metrics
from .report import Report, write_report

__all__ = [
    "PerformanceMetrics",
    "Report",
    "compute_metrics",
    "format_metrics",
    "write_report",
]
