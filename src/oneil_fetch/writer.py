"""CSV 쓰기 + 엔진 로더로 자기검증 (계획서 §1, §5.5). oneil_bt.data 재사용.

임시 파일에 쓰고 엔진의 실제 검증기를 통과한 뒤에만 원자 교체(os.replace)한다 —
검증 실패가 기존 정상 파일을 파괴하지 않는다(2026-07-17 meta.csv 오염 실측의 재발
방지: 종전에는 제자리 쓰기 후 검증이라 실패 시 오염 파일이 남았다). 인코딩은
BOM 없는 UTF-8(§1.1).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Final, Iterable, Mapping

import pandas as pd

from oneil_bt.data.loader import CsvBarLoader
from oneil_bt.data.metadata import MetaRepository

_PRICE_COLUMNS: Final[tuple[str, ...]] = (
    "date", "open", "high", "low", "close", "volume", "value",
)
_META_COLUMNS: Final[tuple[str, ...]] = (
    "symbol", "name", "market", "listing_date", "shares_out",
)
# 정수로 저장할 컬럼 (KRX는 거래량·거래대금을 정수로 준다 — 지저분한 .0 방지).
_INT_COLUMNS: Final[tuple[str, ...]] = ("volume", "value")


def _format_prices(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in _INT_COLUMNS:
        if c in out.columns:
            out[c] = out[c].round().astype("int64")
    cols = [c for c in _PRICE_COLUMNS if c in out.columns]
    return out[cols]


def _write_atomic(
    frame: pd.DataFrame, path: Path, validate: Callable[[Path], object]
) -> Path:
    """임시 파일에 쓰고 검증 통과 시에만 원자 교체. 실패면 기존 파일 무손상 + 예외 전파."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False, encoding="utf-8")
    try:
        validate(tmp)  # 실패 시 ValidationError 전파 (조용한 오염 방지)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    return path


def write_prices(df: pd.DataFrame, path: Path | str) -> Path:
    """종목 일봉 CSV를 쓰고 CsvBarLoader로 자기검증한다."""
    return _write_atomic(_format_prices(df), Path(path), CsvBarLoader().load)


def write_index(df: pd.DataFrame, path: Path | str) -> Path:
    """지수 일봉(date,close) CSV를 쓰고 load_index로 자기검증한다."""
    return _write_atomic(df[["date", "close"]], Path(path), CsvBarLoader().load_index)


def write_meta(rows: Iterable[Mapping[str, object]], path: Path | str) -> Path:
    """meta.csv를 쓰고 MetaRepository로 자기검증한다."""
    frame = pd.DataFrame(list(rows))
    for c in _META_COLUMNS:
        if c not in frame.columns:
            frame[c] = ""
    frame = frame[list(_META_COLUMNS)]
    return _write_atomic(frame, Path(path), MetaRepository.from_csv)
