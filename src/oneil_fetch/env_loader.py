""".env 로더 — KRX_ID/KRX_PW 등 자격증명을 os.environ에 주입 (계획서 외 운영 편의).

pykrx(패치본)는 KRX 데이터 엔드포인트에 로그인 세션이 필요하고, 자격증명을 환경변수
KRX_ID/KRX_PW로 읽는다(pykrx/website/comm/auth.py). pykrx import 전에 env를 세팅해야
하므로, CLI 진입 직후 이 로더로 .env를 읽어 os.environ에 넣는다.

python-dotenv 대신 자체 파서를 쓰는 이유: 사용자의 .env가 `set KEY=VALUE`(윈도우 cmd
관례) 형태일 수 있는데, python-dotenv는 `set ` 접두어를 키의 일부로 잘못 파싱한다. 여기서는
`set `/`export ` 접두어와 따옴표를 모두 벗겨 관대하게 처리한다.

보안: 값은 로그로 출력하지 않는다. 로드된 '키 이름'만 반환한다.
"""

from __future__ import annotations

import os
from pathlib import Path


def _strip_prefix(line: str) -> str:
    for prefix in ("export ", "set "):
        if line.startswith(prefix):
            return line[len(prefix):]
    return line


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_env_file(path: Path | str, *, override: bool = False) -> list[str]:
    """.env를 파싱해 os.environ에 넣는다. 로드된 키 이름 목록을 반환(값은 반환 안 함).

    - `#` 주석·빈 줄 무시
    - `set `/`export ` 접두어 허용
    - 값의 양끝 따옴표 제거
    - override=False면 이미 있는 환경변수는 건드리지 않는다.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f".env not found: {path}")

    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = _strip_prefix(raw.strip())
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        if not override and key in os.environ:
            loaded.append(key)
            continue
        os.environ[key] = _unquote(value)
        loaded.append(key)
    return loaded
