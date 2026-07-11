"""점 경로 기반 Config 오버라이드 (민감도 스윕 하니스).

민감도 스윕은 하나의 base `Config`를 두고 규칙 수치 몇 개만 바꿔 반복 재실행한다. `Config`는
중첩 frozen dataclass라 in-place 변경이 불가능하므로, `"sizing.max_weight_pct"` 같은 점
경로로 지정한 필드만 갈아끼운 **새 Config**를 만든다(원본은 항상 불변으로 남는다).

`dataclasses.replace`를 경로의 각 단계에 재귀 적용한다. 잘못된 경로(오타)나 dataclass가
아닌 중간 노드는 조용히 넘기지 않고 `OverrideError`로 즉시 실패한다 — 오타가 곧 아무것도
안 바꾸는 no-op 스윕이 되어 "파라미터가 결과에 영향 없다"는 잘못된 결론을 내는 것을 막는다.

값 보정: `replace`는 타입을 강제하지 않으므로, YAML/CLI에서 온 값이 기존 필드 타입과
어긋나면 소비 코드가 조용히 깨질 수 있다(특히 Enum 비교). 그래서 기존 필드 값의 타입에
맞춰 Enum·bool·float·tuple만 최소 보정한다.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


class OverrideError(Exception):
    """존재하지 않는 필드 경로 등 오버라이드 지정 오류."""


def _coerce(existing: Any, value: Any) -> Any:
    """새 값을 기존 필드 값의 타입에 맞춰 최소 보정한다.

    `replace`는 타입 검증을 하지 않으므로, 여기서 소비 코드가 기대하는 타입으로 맞춘다.
    수치(int/float)와 문자열은 원칙적으로 호출측이 올바른 타입을 주지만, Enum 필드에
    원문 문자열을 주면 `==` 비교가 조용히 깨지므로 반드시 감싼다.
    """
    if isinstance(existing, Enum) and not isinstance(value, Enum):
        return type(existing)(value)
    # bool은 int의 하위 타입이라 float 분기보다 먼저 처리한다.
    if isinstance(existing, bool):
        return bool(value)
    if isinstance(existing, float) and isinstance(value, (int, float)):
        return float(value)
    if isinstance(existing, tuple) and not isinstance(value, tuple):
        return tuple(value)
    return value


def _set_path(obj: T, parts: tuple[str, ...], value: Any) -> T:
    if not dataclasses.is_dataclass(obj):
        raise OverrideError(
            f"'{parts[0]}' 에서 더 내려갈 수 없다: {type(obj).__name__} 는 dataclass가 아니다"
        )
    field = parts[0]
    names = {f.name for f in dataclasses.fields(obj)}
    if field not in names:
        raise OverrideError(
            f"{type(obj).__name__} 에 없는 config 필드 '{field}' (경로 오타?)"
        )
    current = getattr(obj, field)
    if len(parts) == 1:
        new_value = _coerce(current, value)
    else:
        new_value = _set_path(current, parts[1:], value)
    return dataclasses.replace(obj, **{field: new_value})  # type: ignore[type-var]


def apply_overrides(cfg: T, overrides: Mapping[str, Any]) -> T:
    """`{"sizing.max_weight_pct": 5.0}` 형태의 오버라이드를 적용한 새 Config 반환.

    원본 `cfg`는 변경하지 않는다. 오버라이드는 삽입 순서대로 순차 적용된다(같은 경로가
    중복되면 마지막 값이 이긴다).
    """
    out = cfg
    for path, value in overrides.items():
        parts = tuple(p for p in path.split(".") if p)
        if not parts:
            raise OverrideError(f"빈 오버라이드 경로: {path!r}")
        out = _set_path(out, parts, value)
    return out
