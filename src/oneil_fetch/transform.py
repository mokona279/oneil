"""한글컬럼 rename · dtype 정규화 · 이상행 정제 (계획서 §2.1, §5.3). 순수 함수.

pykrx 원본(한글 컬럼, 날짜 index)을 엔진 로더가 받는 스키마
(date,open,high,low,close,volume,value)로 바꾸고, KRX 원본에 실제로 존재하는
로더 검증을 깨는 행들을 규칙 기반으로 정제한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pandas as pd

# pykrx 한글 → 엔진 스키마 (§2.1).
RENAME_MAP: Final[dict[str, str]] = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
    "거래대금": "value",
}

_OHLCV_COLS: Final[tuple[str, ...]] = ("open", "high", "low", "close", "volume", "value")


# 정합 위반이 이 상대오차(종가 대비) 이하이면 수정주가 반올림 아티팩트로 보고 클램프,
# 초과면 진짜 KRX 원본 오류로 보고 삭제한다. 실측상 위반은 대개 ±1원(≈0.005%)이다.
_CLAMP_REL_TOL = 0.005


@dataclass(frozen=True)
class CleanStats:
    """정제로 보정·삭제된 행 수 (리포트 집계용, §5.3)."""

    halt_fixed: int = 0          # 거래정지 행 보정 (O=H=L=0 → close 복제)
    clamped: int = 0             # 수정주가 반올림으로 O/C가 [L,H] 밖 → high/low 클램프
    dropped_nonpositive: int = 0  # close<=0 행 삭제
    dropped_integrity: int = 0    # 큰 정합 붕괴 삭제 (KRX 원본 오류)
    dropped_nan: int = 0          # NaN 포함 행 삭제

    @property
    def total_touched(self) -> int:
        return (
            self.halt_fixed
            + self.clamped
            + self.dropped_nonpositive
            + self.dropped_integrity
            + self.dropped_nan
        )


def normalize_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    """pykrx OHLCV 원본 → date + open..value 컬럼 프레임 (정렬·중복제거).

    - 한글 컬럼 rename, 날짜 index를 'date'(ISO 문자열) 컬럼으로.
    - 숫자 강제(coerce), 날짜 오름차순 정렬 + 중복 날짜 제거(마지막 유지).
    """
    df = raw.rename(columns=RENAME_MAP).copy()
    df["date"] = pd.to_datetime(df.index).normalize().strftime("%Y-%m-%d")
    cols = ["date", *(c for c in _OHLCV_COLS if c in df.columns)]
    df = df[cols].reset_index(drop=True)
    for c in _OHLCV_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.drop_duplicates(subset="date", keep="last")
    return df.sort_values("date").reset_index(drop=True)


def clean_bars(df: pd.DataFrame) -> tuple[pd.DataFrame, CleanStats]:
    """로더 검증을 깨는 행을 규칙 기반으로 정제 (§5.3).

    처리 순서:
      1. 거래정지 행(volume==0 & open==high==low==0, close는 직전가 유지) →
         open=high=low=close 로 보정.
      2. NaN 포함 행 삭제.
      3. close<=0 행 삭제 (정상 시세일 수 없음).
      4. 정합 위반(O/C가 [L,H] 밖):
         - 위반폭이 종가 대비 _CLAMP_REL_TOL 이하 → high/low 클램프(수정주가 반올림 보정).
           수정주가는 필드별로 독립 반올림돼 종가가 고가보다 1원 큰 식의 아티팩트가 흔하다.
           이런 행을 버리면 '종가=고가'인 강세일(돌파 후보)을 잃으므로 보존한다.
         - 초과 → 삭제(진짜 KRX 원본 오류). low<0·volume<0도 삭제.
    """
    out = df.copy()
    present = [c for c in _OHLCV_COLS if c in out.columns]

    # 1. 거래정지 보정
    halt = (
        (out["volume"] == 0)
        & (out["open"] == 0)
        & (out["high"] == 0)
        & (out["low"] == 0)
        & (out["close"] > 0)
    )
    halt_fixed = int(halt.sum())
    if halt_fixed:
        for c in ("open", "high", "low"):
            out.loc[halt, c] = out.loc[halt, "close"]

    # 2. NaN 삭제
    nan_mask = out[present].isna().any(axis=1)
    dropped_nan = int(nan_mask.sum())
    out = out[~nan_mask]

    # 3. close<=0 삭제
    nonpos = out["close"] <= 0
    dropped_nonpositive = int(nonpos.sum())
    out = out[~nonpos]

    # 4. 정합: O/C를 감싸는 최대·최소와 비교해 클램프(작은 위반) 또는 삭제(큰 위반)
    enclose_hi = out[["open", "high", "low", "close"]].max(axis=1)
    enclose_lo = out[["open", "high", "low", "close"]].min(axis=1)
    close = out["close"].abs().clip(lower=1e-9)
    viol = ((enclose_hi - out["high"]).clip(lower=0)
            + (out["low"] - enclose_lo).clip(lower=0)) / close
    hard_bad = (viol > _CLAMP_REL_TOL) | (out["low"] < 0) | (out["volume"] < 0)
    dropped_integrity = int(hard_bad.sum())
    out = out[~hard_bad]

    enclose_hi = out[["open", "high", "low", "close"]].max(axis=1)
    enclose_lo = out[["open", "high", "low", "close"]].min(axis=1)
    clamp_mask = (out["high"] < enclose_hi) | (out["low"] > enclose_lo)
    clamped = int(clamp_mask.sum())
    if clamped:
        out.loc[clamp_mask, "high"] = enclose_hi[clamp_mask]
        out.loc[clamp_mask, "low"] = enclose_lo[clamp_mask]

    stats = CleanStats(
        halt_fixed=halt_fixed,
        clamped=clamped,
        dropped_nonpositive=dropped_nonpositive,
        dropped_integrity=dropped_integrity,
        dropped_nan=dropped_nan,
    )
    return out.reset_index(drop=True), stats


def normalize_index(raw: pd.DataFrame) -> pd.DataFrame:
    """지수 원본 → date,close 프레임 (정렬·중복제거, close<=0/NaN 삭제)."""
    df = raw.rename(columns=RENAME_MAP).copy()
    df["date"] = pd.to_datetime(df.index).normalize().strftime("%Y-%m-%d")
    df = df[["date", "close"]].reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.drop_duplicates(subset="date", keep="last")
    df = df[df["close"].notna() & (df["close"] > 0)]
    return df.sort_values("date").reset_index(drop=True)
