"""캡처 회귀 세트 — 창 내 대시세 종목 추출 (개선계획 §3.3, Q8 확정).

"전략이 잡았어야 할 시세"의 회귀 감시 장치. 정의(Q8):
    백테스트 창 안에서 **252세션 내 종가 기준 +100% 이상** 상승을 달성한 적이 있고,
    달성 구간(배수 조건을 충족한 세션)에서 **20일 평균 거래대금 ≥ 100억**인 종목.

- 종목당 O(n): 종가 / 직전 252세션 롤링 최소 종가 비율의 벡터 계산.
- 달성일(first_achieved)이 창 밖(2017 이전)인 이력은 제외 — 판정은 창 내 세션만 본다.
  단 롤링 최소값 자체는 창 이전(웜업 구간) 데이터를 자연스럽게 포함한다.
- 세트는 "정답지"가 아니라 회귀 감시 장치다(개선계획 §3.3). 임계 민감도가 크면 Q8 재상정.

공개 진입점:
- `CaptureCriteria` — 임계값 묶음(기본: 252세션·2.0×·20일 평균 100억).
- `capture_record(pf, start, end)` — 종목 1개 판정. 달성 이력이 없으면 None.
- `build_capture_set(...)` — 유니버스 전체를 돌아 DataFrame으로 수집.
  `turnover_ok == True` 행이 캡처 세트 본체다(달성했으나 유동성 미달인 행도
  감사 목적으로 남긴다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from ..domain.bar import PriceFrame


@dataclass(frozen=True)
class CaptureCriteria:
    """캡처 세트 임계값 (Q8 확정 기본값)."""

    lookback_sessions: int = 252  # 상승 달성 허용 구간(세션)
    multiple: float = 2.0  # 종가 배수(+100% = 2.0×)
    turnover_window: int = 20  # 거래대금 평균 창(세션)
    min_turnover: float = 1.0e10  # 20일 평균 거래대금 하한(원) = 100억


@dataclass(frozen=True)
class CaptureRecord:
    """종목 1개의 달성 이력 요약 — capture_set.csv의 한 행."""

    symbol: str
    first_achieved: date  # 창 내 최초 배수 달성일
    max_multiple: float  # 창 내 최대 배수 (252세션 롤링 최소 대비)
    turnover_ok: bool  # 달성 세션 중 20일 평균 거래대금 ≥ 하한 존재 여부
    sessions: int  # 창 내 세션 수


def capture_record(
    pf: PriceFrame,
    start: date,
    end: date,
    criteria: CaptureCriteria = CaptureCriteria(),
) -> CaptureRecord | None:
    """창 [start, end] 안에서 배수 달성 이력이 있으면 요약을, 없으면 None을 반환."""
    df = pf.df
    close = df["close"]
    rolling_min = close.rolling(criteria.lookback_sessions, min_periods=1).min()
    ratio = close / rolling_min

    win = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    achieved = (ratio >= criteria.multiple) & win
    if not bool(achieved.any()):
        return None

    if "value" in df.columns:
        turn = df["value"].rolling(criteria.turnover_window, min_periods=1).mean()
        turnover_ok = bool((achieved & (turn >= criteria.min_turnover)).any())
    else:
        turnover_ok = False  # 거래대금 없으면 유동성 판정 불가 → 보수적으로 미달

    first = df.index[achieved.to_numpy().argmax()].date()
    return CaptureRecord(
        symbol=pf.symbol,
        first_achieved=first,
        max_multiple=float(ratio[win].max()),
        turnover_ok=turnover_ok,
        sessions=int(win.sum()),
    )


def build_capture_set(
    frames: dict[str, PriceFrame] | list[PriceFrame],
    start: date,
    end: date,
    criteria: CaptureCriteria = CaptureCriteria(),
) -> pd.DataFrame:
    """유니버스 전체의 달성 이력을 모아 심볼 정렬된 DataFrame으로 반환.

    컬럼: symbol, first_achieved, max_multiple, turnover_ok, sessions.
    캡처 세트 본체는 `turnover_ok == True` 행 — 나머지 행은 임계 민감도 감사용.
    """
    pfs = frames.values() if isinstance(frames, dict) else frames
    records = []
    for pf in pfs:
        rec = capture_record(pf, start, end, criteria)
        if rec is not None:
            records.append(rec)
    records.sort(key=lambda r: r.symbol)
    return pd.DataFrame(
        [
            dict(
                symbol=r.symbol,
                first_achieved=r.first_achieved.isoformat(),
                max_multiple=round(r.max_multiple, 4),
                turnover_ok=r.turnover_ok,
                sessions=r.sessions,
            )
            for r in records
        ],
        columns=["symbol", "first_achieved", "max_multiple", "turnover_ok", "sessions"],
    )
