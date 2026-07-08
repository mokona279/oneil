"""CSV 출력 공통 유틸 (Phase 7 리포팅).

세 리포터(트레이드 로그·자본곡선·이벤트 목록)가 공유하는 결정론적 CSV 기록기.
- 인코딩은 `utf-8-sig`(BOM 포함) — 엑셀/한글 환경에서 UTF-8을 바로 인식.
- 개행은 `\n` 고정(`newline=""`로 os별 CRLF 변환 차단) → 골든파일 해시 재현성.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path


def write_csv(
    path: Path | str,
    header: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
