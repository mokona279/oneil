"""CSV 쓰기 + 엔진 로더로 자기검증 (계획서 §1, §5.5). oneil_bt.data 재사용.

쓰기 전이 아니라 쓰기 후 산출물을 엔진의 실제 검증기로 통과시켜, 로더가 죽을 파일을
절대 남기지 않는다. 인코딩은 BOM 없는 UTF-8(§1.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Iterable, Mapping

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


def write_prices(df: pd.DataFrame, path: Path | str) -> Path:
    """종목 일봉 CSV를 쓰고 CsvBarLoader로 자기검증한다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _format_prices(df).to_csv(path, index=False, encoding="utf-8")
    CsvBarLoader().load(path)  # 실패 시 ValidationError 전파 (조용한 오염 방지)
    return path


def write_index(df: pd.DataFrame, path: Path | str) -> Path:
    """지수 일봉(date,close) CSV를 쓰고 load_index로 자기검증한다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df[["date", "close"]].to_csv(path, index=False, encoding="utf-8")
    CsvBarLoader().load_index(path)
    return path


def write_meta(rows: Iterable[Mapping[str, object]], path: Path | str) -> Path:
    """meta.csv를 쓰고 MetaRepository로 자기검증한다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(list(rows))
    for c in _META_COLUMNS:
        if c not in frame.columns:
            frame[c] = ""
    frame = frame[list(_META_COLUMNS)]
    frame.to_csv(path, index=False, encoding="utf-8")
    MetaRepository.from_csv(path)
    return path
