"""분석 하니스 (계획서 §11 후속과제).

백테스트 엔진 위에 얹는 오프라인 분석 도구. v1은 **파라미터 민감도 스윕** — 규칙 수치가
전부 config로 외부화돼 있다는 구조적 이점을 살려, 축(점 경로)별 값 목록의 데카르트 곱을
돌며 조합마다 백테스트를 재실행하고 성과지표를 표로 모은다.

공개 진입점:
- `apply_overrides` — base Config의 지정 필드만 갈아끼운 새 Config 반환(원본 불변).
- `ParameterGrid` / `run_sweep` / `SweepResult` — 그리드 실행과 결과.
- `write_sweep_csv` — 조합별 1행 CSV(재현성 있는 utf-8-sig).
- `CaptureCriteria` / `capture_record` / `build_capture_set` — 캡처 회귀 세트(Q8).
- `build_capture_report` / `capture_stats` — 세트 × 백테스트 산출물 집계(캡처율).
"""

from __future__ import annotations

from .capture import CaptureCriteria, CaptureRecord, build_capture_set, capture_record
from .capture_report import (
    build_capture_report,
    build_capture_report_from_dir,
    capture_stats,
    format_capture_summary,
    write_capture_report,
)
from .override import OverrideError, apply_overrides
from .sweep import (
    ParameterGrid,
    SweepResult,
    SweepRow,
    format_sweep,
    run_sweep,
    sweep_table,
    write_sweep_csv,
)

__all__ = [
    "CaptureCriteria",
    "CaptureRecord",
    "OverrideError",
    "ParameterGrid",
    "build_capture_set",
    "capture_record",
    "SweepResult",
    "SweepRow",
    "apply_overrides",
    "build_capture_report",
    "build_capture_report_from_dir",
    "capture_stats",
    "format_capture_summary",
    "format_sweep",
    "run_sweep",
    "sweep_table",
    "write_capture_report",
    "write_sweep_csv",
]
