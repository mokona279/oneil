"""CSV 일봉 로더 + 검증 (계획서 §4.1, §4.3, §4.4).

인코딩 UTF-8/CP949 자동감지. 로드 시 스키마·정합성 검증을 수행하고 실패 시
ValidationError를 던진다. 통과하면 정렬·중복제거된 PriceFrame을 반환한다.

- load()       : 종목 일봉 (date + OHLCV 필수, value 선택)
- load_index() : 지수 일봉 (date + close 필수, OHLC 없으면 close로 채움)
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Final

import pandas as pd

from ..domain.bar import OPTIONAL_COLUMNS, REQUIRED_COLUMNS, PriceFrame

_ENCODINGS: Final[tuple[str, ...]] = ("utf-8-sig", "cp949")


class ValidationError(Exception):
    """CSV 로드/검증 실패."""


def read_text_autodetect(path: Path) -> str:
    """UTF-8(-sig) → CP949 순으로 디코딩을 시도한다."""
    raw = path.read_bytes()
    last: UnicodeDecodeError | None = None
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError as exc:
            last = exc
    raise ValidationError(f"could not decode {path} as {_ENCODINGS}: {last}")


class CsvBarLoader:
    """종목/지수 일봉 CSV를 PriceFrame으로 로드."""

    def load(self, path: Path | str, symbol: str | None = None) -> PriceFrame:
        """종목 일봉: date + OHLCV 필수, value 선택."""
        path, sym, df = self._read_raw(path, symbol)
        needed = ("date",) + REQUIRED_COLUMNS
        self._require_columns(df, path, needed)

        keep = ["date", *REQUIRED_COLUMNS]
        keep += [c for c in OPTIONAL_COLUMNS if c in df.columns]
        df = df[keep].copy()
        numeric = [*REQUIRED_COLUMNS, *(c for c in OPTIONAL_COLUMNS if c in df.columns)]
        df = self._coerce_dates_numeric(df, path, numeric)
        self._check_ohlc_integrity(df, path)
        return PriceFrame(sym, self._finalize_index(df))

    def load_index(self, path: Path | str, symbol: str | None = None) -> PriceFrame:
        """지수 일봉: date + close 필수. OHLC 없으면 close로, volume 없으면 0으로 채운다."""
        path, sym, df = self._read_raw(path, symbol)
        self._require_columns(df, path, ("date", "close"))

        present = [c for c in REQUIRED_COLUMNS if c in df.columns]
        keep = ["date", *present]
        df = df[keep].copy()
        df = self._coerce_dates_numeric(df, path, present)

        for col in ("open", "high", "low"):
            if col not in df.columns:
                df[col] = df["close"]
        if "volume" not in df.columns:
            df["volume"] = 0.0
        df = df[["date", *REQUIRED_COLUMNS]]
        self._check_ohlc_integrity(df, path)
        return PriceFrame(sym, self._finalize_index(df))

    # ------------------------------------------------------------------ #
    def _read_raw(
        self, path: Path | str, symbol: str | None
    ) -> tuple[Path, str, pd.DataFrame]:
        path = Path(path)
        if not path.exists():
            raise ValidationError(f"csv not found: {path}")
        sym = symbol if symbol is not None else path.stem
        text = read_text_autodetect(path)
        try:
            df = pd.read_csv(StringIO(text))
        except pd.errors.EmptyDataError as exc:
            raise ValidationError(f"{path}: empty csv") from exc
        except pd.errors.ParserError as exc:
            raise ValidationError(f"{path}: csv parse error: {exc}") from exc
        df.columns = [str(c).strip().lower() for c in df.columns]
        return path, sym, df

    def _require_columns(
        self, df: pd.DataFrame, path: Path, needed: tuple[str, ...]
    ) -> None:
        missing = [c for c in needed if c not in df.columns]
        if missing:
            raise ValidationError(f"{path}: missing columns {missing}")

    def _coerce_dates_numeric(
        self, df: pd.DataFrame, path: Path, numeric: list[str]
    ) -> pd.DataFrame:
        dates = pd.to_datetime(df["date"], errors="coerce")
        if dates.isna().any():
            bad = df.loc[dates.isna(), "date"].head(3).tolist()
            raise ValidationError(f"{path}: unparseable date(s) e.g. {bad}")
        df["date"] = dates.dt.normalize()

        for col in numeric:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if df[["date", *numeric]].isna().any().any():
            na_cols = [c for c in numeric if df[c].isna().any()]
            raise ValidationError(f"{path}: missing/NaN values in columns {na_cols}")

        dup = df["date"].duplicated(keep=False)
        if dup.any():
            examples = df.loc[dup, "date"].dt.date.astype(str).unique()[:3].tolist()
            raise ValidationError(f"{path}: duplicate dates e.g. {examples}")

        return df.sort_values("date").reset_index(drop=True)

    def _check_ohlc_integrity(self, df: pd.DataFrame, path: Path) -> None:
        if (df["low"] < 0).any() or (df["high"] < df["low"]).any():
            raise ValidationError(f"{path}: violation of high >= low >= 0")
        for col in ("open", "close"):
            if ((df[col] < df["low"]) | (df[col] > df["high"])).any():
                raise ValidationError(f"{path}: {col} outside [low, high]")
        if (df["volume"] < 0).any():
            raise ValidationError(f"{path}: negative volume")

    def _finalize_index(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.set_index("date")
        df.index.name = "date"
        return df
