"""증분 갱신 + 수정주가 무효화 오버랩 검증 (계획서 §5.2). 순수 함수.

가장 중요한 설계 지점. 단순 append는 수정주가에서 틀린다: 액면분할·감자 등이 발생하면
KRX 수정주가는 과거 전체가 소급 변경되므로, 기존 CSV 뒤에 새 구간만 붙이면 이벤트 전후가
서로 다른 기준이 되어 지표가 조용히 오염된다.

→ 기존 마지막 K거래일을 겹치게 재요청해 close를 비교하고, 상대오차 1e-6 초과 불일치가
하나라도 있으면 수정 이벤트로 간주해 전체 재수집한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

# §9 Q5: 오버랩 창 K=10 거래일, 허용 상대오차 1e-6.
DEFAULT_OVERLAP_K = 10
DEFAULT_RTOL = 1e-6
# 요청 start가 기존 첫 거래일보다 이만큼(달력일) 넘게 이르면 앞구간 결손으로 보고 full.
# --start는 달력일이고 기존 첫 행은 그 이후 첫 '거래일'이라, 주말·연휴 경계에서 며칠 벌어지는
# 것은 정상이다(항상 full이 되면 증분이 무의미). 한국 최장 연휴+주말도 넉넉히 덮는 값.
_BACKFILL_TOLERANCE_DAYS = 10


def overlap_fromdate(last_date: date, k: int = DEFAULT_OVERLAP_K) -> date:
    """기존 마지막 날짜에서 최소 K거래일을 덮는 재요청 시작일(달력일).

    종목별 거래일 캘린더가 없으므로 넉넉히 잡는다: K거래일 ≈ K*2 달력일이면 주말·연휴를
    감안해도 K개 이상의 거래일이 겹친다. 겹침이 넘쳐도 비교만 하므로 무해하다.
    """
    return last_date - timedelta(days=max(k, 1) * 2 + 5)


@dataclass(frozen=True)
class FetchDecision:
    """무엇을 어디서부터 받을지. mode='full'이면 [start,end], 'incremental'이면 오버랩부터."""

    mode: str          # "full" | "incremental"
    fromdate: date     # 요청 시작일
    todate: date       # 요청 종료일


def decide_fetch(
    existing: pd.DataFrame | None,
    start: date,
    end: date,
    *,
    k: int = DEFAULT_OVERLAP_K,
) -> FetchDecision:
    """기존 CSV 유무·범위로 수집 범위를 결정한다.

    - 기존 없음/비어있음 → full [start,end]
    - 요청 start가 기존 첫 거래일보다 tolerance 넘게 이르면 → full (앞구간 결손 방지)
    - 그 외 → incremental (오버랩 시작일부터 end까지)
    """
    if existing is None or len(existing) == 0:
        return FetchDecision("full", start, end)

    first = _row_date(existing["date"].iloc[0])
    last = _row_date(existing["date"].iloc[-1])
    if start < first and (first - start).days > _BACKFILL_TOLERANCE_DAYS:
        return FetchDecision("full", start, end)
    return FetchDecision("incremental", overlap_fromdate(last, k), end)


@dataclass(frozen=True)
class MergeResult:
    """증분 병합 결과. action='append'면 df가 병합본, 'refetch_full'이면 전체 재수집 필요."""

    action: str          # "append" | "refetch_full"
    df: pd.DataFrame | None = None
    appended: int = 0    # 새로 추가된 행 수 (리포트용)


def merge_incremental(
    existing: pd.DataFrame,
    fetched: pd.DataFrame,
    *,
    rtol: float = DEFAULT_RTOL,
) -> MergeResult:
    """오버랩 close 비교 후 append 또는 full-refetch 판정 (§5.2 2~3).

    existing/fetched는 normalize_ohlcv 산출 스키마(date 문자열 + close 등)를 가정한다.
    """
    ex = existing.set_index("date")
    fe = fetched.set_index("date")
    overlap = ex.index.intersection(fe.index)

    if len(overlap) == 0:
        # 겹치는 구간이 없으면 수정 여부를 검증할 수 없다 → 안전하게 전체 재수집.
        return MergeResult("refetch_full")

    ex_close = ex.loc[overlap, "close"].astype(float)
    fe_close = fe.loc[overlap, "close"].astype(float)
    rel = (ex_close - fe_close).abs() / fe_close.abs().clip(lower=1e-12)
    if (rel > rtol).any():
        return MergeResult("refetch_full")

    # 일치 → 기존 + (기존 마지막 날짜 초과 신규 행)만 append.
    last = existing["date"].iloc[-1]
    new_rows = fetched[fetched["date"] > last]
    merged = (
        pd.concat([existing, new_rows], ignore_index=True)
        .drop_duplicates(subset="date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return MergeResult("append", merged, appended=len(new_rows))


def _row_date(value: object) -> date:
    return pd.Timestamp(value).date()
