"""_state/fetch_state.json 읽기/쓰기 (계획서 §1, §5.1). 체크포인트·실패목록.

전 종목 최초 수집은 수 시간 걸린다. 완료 심볼을 기록해 중단 후 재실행 시 이어받는다.
엔진은 _state/를 읽지 않는다 (스크립트 내부 상태).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_STATE_FILENAME = "fetch_state.json"


@dataclass
class FetchState:
    """수집 진행 상태. completed=완료 심볼, failed=심볼→사유, last_end=마지막 요청 종료일."""

    completed: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)
    last_end: str | None = None

    # ------------------------------------------------------------------ #
    @staticmethod
    def state_path(state_dir: Path | str) -> Path:
        return Path(state_dir) / _STATE_FILENAME

    @classmethod
    def load(cls, state_dir: Path | str) -> "FetchState":
        path = cls.state_path(state_dir)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            completed=set(raw.get("completed", [])),
            failed=dict(raw.get("failed", {})),
            last_end=raw.get("last_end"),
        )

    def save(self, state_dir: Path | str) -> Path:
        path = self.state_path(state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "completed": sorted(self.completed),
            "failed": self.failed,
            "last_end": self.last_end,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    # ------------------------------------------------------------------ #
    def mark_completed(self, symbol: str) -> None:
        self.completed.add(symbol)
        self.failed.pop(symbol, None)

    def mark_failed(self, symbol: str, reason: str) -> None:
        self.failed[symbol] = reason
        self.completed.discard(symbol)

    def is_completed(self, symbol: str) -> bool:
        return symbol in self.completed

    def reset(self) -> None:
        """--full-refresh 용: 진행 상태 초기화."""
        self.completed.clear()
        self.failed.clear()
