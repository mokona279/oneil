"""베이스 감지기 + 단계 트래커 (Phase 3A DoD).

경계 픽스처가 기대한 Base/None을 산출하는지 검증한다:
깊이 15%/33% 경계, 기간 5주/7주 경계(달력일 7×N), 기간 미충족 조기 상회 → 리셋,
D>33% 무효·재시작, 단계 1→2→3→4 카운트/유지/리셋, 룩어헤드 회귀.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oneil_bt.domain.bar import PriceFrame
from oneil_bt.domain.config import Config
from oneil_bt.indicators.indicator_set import IndicatorSet
from oneil_bt.rules.base_detector import BaseDetector
from oneil_bt.rules.stage_tracker import StageTracker
from tests.fixtures.synthetic import business_dates, ohlcv_frame

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"

# 2020-01-06 = 월요일 → 영업일 인덱스가 주 단위로 정렬돼 달력일 계산이 명료하다.
# 영업일 인덱스 j 의 달력일 경과 = (j//5)*7 + (j%5). 예: j=25 → 35일(5주), j=35 → 49일(7주).
START = "2020-01-06"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


def _frame(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    symbol: str = "TEST",
    volume: float = 1_000.0,
) -> tuple[PriceFrame, list[date]]:
    n = len(highs)
    dates = business_dates(START, n)
    idx = pd.DatetimeIndex(pd.to_datetime(dates).normalize(), name="date")
    df = pd.DataFrame(
        {
            "open": list(closes),
            "high": list(highs),
            "low": list(lows),
            "close": list(closes),
            "volume": [volume] * n,
        },
        index=idx,
    )
    return PriceFrame(symbol, df), dates


def _detector(cfg: Config, frame: PriceFrame, dates: list[date]) -> BaseDetector:
    index = PriceFrame("KOSPI", ohlcv_frame(dates, 100.0))
    ind = IndicatorSet(frame, index, cfg)
    return BaseDetector(frame, ind, cfg)


def _consolidation(
    pivot: float,
    depth_pct: float,
    n_consol: int,
    *,
    n_tail: int = 4,
    breakout: bool = True,
) -> tuple[list[float], list[float], list[float]]:
    """정점(day0) + n_consol 다짐 바 + 돌파 바(옵션) + 꼬리 바들.

    다짐 저가는 pivot*(1-depth) 로 깊이를 정확히 만든다. 다짐 고가는 pivot 미만.
    """
    low_floor = pivot * (1 - depth_pct / 100.0)
    highs = [pivot]
    lows = [pivot * 0.99]
    closes = [pivot * 0.99]
    for _ in range(n_consol):
        highs.append(pivot * 0.97)
        lows.append(low_floor)
        closes.append(pivot * 0.96)
    if breakout:
        highs.append(pivot * 1.02)  # high >= pivot
        lows.append(pivot * 0.99)
        closes.append(pivot * 1.0)
    for _ in range(n_tail):
        highs.append(pivot * 0.97)
        lows.append(low_floor)
        closes.append(pivot * 0.96)
    return highs, lows, closes


# --------------------------------------------------------------------------- #
# 유효 베이스 + 돌파 (플랫, 5주)
# --------------------------------------------------------------------------- #
def test_flat_base_breakout_and_period_boundary(cfg: Config) -> None:
    # 정점 day0(pivot=100), 25일 다짐, day25가 돌파(영업일 25 → 35달력일 = 5주 정확).
    highs, lows, closes = _consolidation(100.0, depth_pct=5.0, n_consol=24)
    frame, dates = _frame(highs, lows, closes)
    det = _detector(cfg, frame, dates)

    # 기간 경계: day24(32일)엔 아직 미성숙 → None, day25(35일)엔 유효.
    assert det.base_asof(dates[24]) is None
    base = det.base_asof(dates[25])
    assert base is not None
    assert base.start == dates[0]
    assert base.pivot == pytest.approx(100.0)
    assert base.depth_pct == pytest.approx(5.0)
    assert base.min_weeks == 5
    assert base.stage == 1
    assert det.is_breakout(dates[25], base) is True

    # 스캔이 유효 돌파를 1건 기록.
    assert [b.stage for b in det.breakouts] == [1]
    assert det.breakouts[0].date == dates[25]


def test_early_pivot_breach_is_invalidated(cfg: Config) -> None:
    # 다짐 20일 뒤 day21에 피벗 상회 시도 → 아직 5주 미충족 → 무효(돌파 기록 없음).
    highs, lows, closes = _consolidation(100.0, depth_pct=5.0, n_consol=20)
    # day21 을 조기 돌파 시도로: _consolidation 은 day0..20 다짐, day21 돌파바.
    frame, dates = _frame(highs, lows, closes)
    det = _detector(cfg, frame, dates)
    # day21 영업일 → (21//5)*7+1 = 29달력일 < 35 → 미성숙.
    assert det.base_asof(dates[21]) is None
    assert det.breakouts == []


# --------------------------------------------------------------------------- #
# 깊이 티어 경계 (15% / 16%, 33% / 34%)
# --------------------------------------------------------------------------- #
def test_depth_tier_15_uses_5_weeks(cfg: Config) -> None:
    highs, lows, closes = _consolidation(100.0, depth_pct=15.0, n_consol=24)
    frame, dates = _frame(highs, lows, closes)
    det = _detector(cfg, frame, dates)
    base = det.base_asof(dates[25])  # 35일 = 5주
    assert base is not None
    assert base.min_weeks == 5
    assert base.depth_pct == pytest.approx(15.0)


def test_depth_16_needs_7_weeks(cfg: Config) -> None:
    # 깊이 16% → 컵 티어(7주). 5주(35일) 시점엔 미성숙, 7주(49일)엔 성숙.
    highs, lows, closes = _consolidation(100.0, depth_pct=16.0, n_consol=40)
    frame, dates = _frame(highs, lows, closes)
    det = _detector(cfg, frame, dates)
    assert det.base_asof(dates[25]) is None            # 35일
    base = det.base_asof(dates[35])                    # 49일
    assert base is not None
    assert base.min_weeks == 7
    assert base.depth_pct == pytest.approx(16.0)


def test_depth_33_is_valid_cup(cfg: Config) -> None:
    highs, lows, closes = _consolidation(100.0, depth_pct=33.0, n_consol=40)
    frame, dates = _frame(highs, lows, closes)
    det = _detector(cfg, frame, dates)
    base = det.base_asof(dates[35])  # 49일 = 7주
    assert base is not None
    assert base.min_weeks == 7
    assert base.depth_pct == pytest.approx(33.0)


def test_depth_over_33_invalidates_pattern(cfg: Config) -> None:
    # 깊이 34% → 패턴 무효. 이후 오래 지나도 이 피벗의 유효 베이스/돌파는 없다.
    highs, lows, closes = _consolidation(100.0, depth_pct=34.0, n_consol=40)
    frame, dates = _frame(highs, lows, closes)
    det = _detector(cfg, frame, dates)
    base = det.base_asof(dates[35])
    assert base is None or base.pivot != pytest.approx(100.0)
    assert det.breakouts == []


# --------------------------------------------------------------------------- #
# 단계 카운트 (감지기 통합)
# --------------------------------------------------------------------------- #
def _staged(n_bases: int, gain: float) -> tuple[list[float], list[float], list[float]]:
    """n_bases 개의 연속 유효 베이스. 각 베이스 사이에 +gain 종가 랠리를 넣는다."""
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    pivot = 100.0
    for _ in range(n_bases):
        # 정점 + 25 다짐(5% 깊이) + 돌파바 → 26영업일(≥35달력일) 뒤 유효 돌파.
        highs.append(pivot); lows.append(pivot * 0.99); closes.append(pivot * 0.99)
        for _c in range(25):
            highs.append(pivot * 0.97); lows.append(pivot * 0.95); closes.append(pivot * 0.96)
        bo_close = pivot
        highs.append(pivot * 1.02); lows.append(pivot * 0.99); closes.append(bo_close)
        # 돌파 종가에서 +gain 까지 신고가를 만들며 랠리(각 바가 신고가 → 시작점 상승).
        top = bo_close * (1 + gain)
        rally_high = np.linspace(pivot * 1.03, top * 1.02, 6)
        for rh in rally_high:
            highs.append(rh); lows.append(rh * 0.98); closes.append(rh * 0.995)
        pivot = top * 1.02  # 다음 베이스 정점 = 랠리 최고가
    return highs, lows, closes


def test_stage_counts_up_with_20pct_runup(cfg: Config) -> None:
    highs, lows, closes = _staged(4, gain=0.25)
    frame, dates = _frame(highs, lows, closes)
    det = _detector(cfg, frame, dates)
    # 매 베이스 전 +25% 랠리 → 단계 1→2→3→4 (감지기는 4단계도 그대로 센다).
    assert [b.stage for b in det.breakouts] == [1, 2, 3, 4]


def test_stage_maintained_without_runup(cfg: Config) -> None:
    highs, lows, closes = _staged(3, gain=0.05)
    frame, dates = _frame(highs, lows, closes)
    det = _detector(cfg, frame, dates)
    # +5% 재베이스(베이스 온 베이스) → 단계 유지.
    assert [b.stage for b in det.breakouts] == [1, 1, 1]


# --------------------------------------------------------------------------- #
# 룩어헤드 회귀 — 미래 바 조작이 과거 베이스 확정을 바꾸지 않는다
# --------------------------------------------------------------------------- #
def test_base_asof_ignores_future_bars(cfg: Config) -> None:
    highs, lows, closes = _consolidation(100.0, depth_pct=5.0, n_consol=24)
    frame_a, dates = _frame(highs, lows, closes)
    det_a = _detector(cfg, frame_a, dates)

    # frame_b: day25 이후를 완전히 다르게 바꿔도(돌파 대신 급락) base_asof(day25)는 불변.
    highs_b = list(highs)
    lows_b = list(lows)
    closes_b = list(closes)
    highs_b[25], lows_b[25], closes_b[25] = 90.0, 80.0, 85.0  # 돌파 아님
    frame_b, _ = _frame(highs_b, lows_b, closes_b)
    det_b = _detector(cfg, frame_b, dates)

    base_a = det_a.base_asof(dates[25])
    base_b = det_b.base_asof(dates[25])
    assert base_a is not None and base_b is not None
    assert base_a.start == base_b.start
    assert base_a.pivot == pytest.approx(base_b.pivot)
    assert base_a.depth_pct == pytest.approx(base_b.depth_pct)
    # 그러나 돌파 판정은 당일 고가에 따라 갈린다.
    assert det_a.is_breakout(dates[25], base_a) is True
    assert det_b.is_breakout(dates[25], base_b) is False


# --------------------------------------------------------------------------- #
# StageTracker 단위 — 상승/유지/리셋
# --------------------------------------------------------------------------- #
D0 = date(2020, 1, 6)   # 판정일(리셋 꺼짐 케이스에선 값 무관)


def test_stage_tracker_first_base_is_one(cfg: Config) -> None:
    st = StageTracker(cfg)
    assert st.stage_for_new_base(D0, 5.0) == 1


def test_stage_tracker_steps_up_on_20pct(cfg: Config) -> None:
    st = StageTracker(cfg)
    st.on_breakout(D0, close=100.0, base_low=90.0, stage=2)
    st.on_bar(close=125.0, low=120.0)   # +25% 종가
    assert st.stage_for_new_base(D0, 5.0) == 3


def test_stage_tracker_maintained_below_20pct(cfg: Config) -> None:
    st = StageTracker(cfg)
    st.on_breakout(D0, close=100.0, base_low=90.0, stage=2)
    st.on_bar(close=115.0, low=110.0)   # +15% 종가
    assert st.stage_for_new_base(D0, 5.0) == 2


def test_stage_tracker_resets_on_undercut(cfg: Config) -> None:
    st = StageTracker(cfg)
    st.on_breakout(D0, close=100.0, base_low=90.0, stage=2)
    st.on_bar(close=110.0, low=85.0)    # 직전 베이스 저점(90) 하회
    assert st.stage_for_new_base(D0, 5.0) == 1


# --------------------------------------------------------------------------- #
# StageTracker R3b(Q5b) — 새 사이클 리셋 (N개월 무돌파 + 깊은 새 베이스)
# --------------------------------------------------------------------------- #
def _reset_cfg(cfg: Config, months: int = 12, min_depth: float = 20.0) -> Config:
    from oneil_bt.analysis.override import apply_overrides
    return apply_overrides(cfg, {
        "base.stage.reset_no_breakout_months": months,
        "base.stage.reset_min_depth_pct": min_depth,
    })


def test_stage_tracker_new_cycle_reset(cfg: Config) -> None:
    # 12개월+ 무돌파 & 깊이 20%+ 새 베이스 → 단계 1 (그 사이 +20% 랠리가 있었어도).
    st = StageTracker(_reset_cfg(cfg))
    st.on_breakout(D0, close=100.0, base_low=90.0, stage=3)
    st.on_bar(close=130.0, low=120.0)   # +30% 랠리 — 리셋 없으면 단계 4
    later = date(2021, 1, 6)            # 366일 ≥ 365일(12개월)
    assert st.stage_for_new_base(later, 25.0) == 1


def test_stage_tracker_no_reset_when_shallow(cfg: Config) -> None:
    # 기간은 충족해도 깊이 미달(20% 미만) → 리셋 없음(기존 카운트 경로).
    st = StageTracker(_reset_cfg(cfg))
    st.on_breakout(D0, close=100.0, base_low=90.0, stage=3)
    st.on_bar(close=130.0, low=120.0)
    later = date(2021, 1, 6)
    assert st.stage_for_new_base(later, 15.0) == 4   # +30% 랠리 → 단계 +1


def test_stage_tracker_no_reset_before_period(cfg: Config) -> None:
    # 깊이는 충족해도 무돌파 기간 미달 → 리셋 없음.
    st = StageTracker(_reset_cfg(cfg))
    st.on_breakout(D0, close=100.0, base_low=90.0, stage=3)
    st.on_bar(close=115.0, low=110.0)   # +15% — 미달 재베이스
    soon = date(2020, 6, 1)             # 147일 < 365일
    assert st.stage_for_new_base(soon, 25.0) == 3


def test_stage_tracker_reset_disabled_is_bitwise_current(cfg: Config) -> None:
    # months=null(v3-4 이전 동치)에선 d·depth를 무시하고 현행과 동일하게 동작.
    from oneil_bt.analysis.override import apply_overrides
    off = apply_overrides(cfg, {"base.stage.reset_no_breakout_months": None})
    st = StageTracker(off)
    st.on_breakout(D0, close=100.0, base_low=90.0, stage=3)
    st.on_bar(close=130.0, low=120.0)
    far = date(2030, 1, 1)
    assert st.stage_for_new_base(far, 99.0) == 4


def test_stage_tracker_shadow_ignores_reset(cfg: Config) -> None:
    # 섀도 모드(disable_reset=True): 리셋 config가 켜져 있어도 R3b를 배제한
    # 순수 카운트 — §3.3 반사실 단계(stage_no_reset) 산출용.
    st = StageTracker(_reset_cfg(cfg))
    shadow = StageTracker(_reset_cfg(cfg), disable_reset=True)
    for t in (st, shadow):
        t.on_breakout(D0, close=100.0, base_low=90.0, stage=3)
        t.on_bar(close=130.0, low=120.0)
    later = date(2021, 1, 6)                     # 366일 ≥ 365일, 깊이 25% ≥ 20%
    assert st.stage_for_new_base(later, 25.0) == 1       # 리셋 발동
    assert shadow.stage_for_new_base(later, 25.0) == 4   # 반사실: +30% 랠리 → +1


# --------------------------------------------------------------------------- #
# R4a(Q10) — 컵위드핸들 손잡이 피벗 (P3)
# --------------------------------------------------------------------------- #
def _handle_cfg(cfg: Config, min_sessions: int = 5, max_depth: float = 12.0) -> Config:
    from oneil_bt.analysis.override import apply_overrides
    return apply_overrides(cfg, {
        "base.handle.min_sessions": min_sessions,
        "base.handle.max_depth_pct": max_depth,
    })


def _cup_with_handle(
    pullback_low: float = 91.5,
) -> tuple[list[float], list[float], list[float]]:
    """정점 100(day0) → 저점 80(day10, 깊이 20%) → 회복 랠리 95(day29 정점)
    → 5일 눌림(day30-34, 저가 pullback_low) → day35 고가 96(핸들 95 돌파,
    절대 고점 100 미달) → 꼬리. 7주 티어라 day35(49달력일)에 성숙."""
    highs = [100.0]; lows = [99.0]; closes = [99.0]
    for lo in (97, 95, 93, 91, 89, 87, 85, 83, 81, 80):          # day1-10 하락
        highs.append(lo + 2.0); lows.append(float(lo)); closes.append(lo + 1.0)
    for hi in np.linspace(83.0, 95.0, 19):                        # day11-29 랠리
        highs.append(float(hi)); lows.append(float(hi) - 2.0); closes.append(float(hi) - 1.0)
    for _ in range(5):                                            # day30-34 눌림
        highs.append(94.0); lows.append(pullback_low); closes.append(92.0)
    highs.append(96.0); lows.append(93.0); closes.append(95.5)    # day35 핸들 돌파
    for _ in range(4):                                            # 꼬리
        highs.append(94.0); lows.append(92.0); closes.append(93.0)
    return highs, lows, closes


def test_handle_pivot_detected(cfg: Config) -> None:
    highs, lows, closes = _cup_with_handle()
    frame, dates = _frame(highs, lows, closes)
    det = _detector(_handle_cfg(cfg), frame, dates)
    base = det.base_asof(dates[35])  # 49달력일 = 7주 성숙, 눌림 5일(day30-34)
    assert base is not None
    assert base.handle is True
    assert base.pivot == pytest.approx(95.0)          # 진입 피벗 = 손잡이 고점
    assert base.struct_pivot == pytest.approx(100.0)  # 절대 고점은 참고 보존
    assert base.depth_pct == pytest.approx(20.0)      # 구조 깊이는 절대 고점 기준
    assert base.min_weeks == 7
    # 핸들 돌파: 당일 고가 96 ≥ 95(핸들) — 절대 고점 100엔 못 미쳐도 진입 기회.
    assert det.is_breakout(dates[35], base) is True
    # 스캔 수준 유효 돌파(절대 고점)는 발생하지 않음 — 구조는 절대 고점 기준 유지.
    assert det.breakouts == []


def test_handle_off_is_bitwise_current(cfg: Config) -> None:
    # 기본 null(끔)이면 같은 프레임에서 절대 고점 피벗 — 현행 비트 동치.
    highs, lows, closes = _cup_with_handle()
    frame, dates = _frame(highs, lows, closes)
    det = _detector(cfg, frame, dates)
    base = det.base_asof(dates[35])
    assert base is not None
    assert base.handle is False
    assert base.struct_pivot is None
    assert base.pivot == pytest.approx(100.0)
    assert det.is_breakout(dates[35], base) is False  # 96 < 100


def test_handle_min_sessions_boundary(cfg: Config) -> None:
    # 눌림 5일 < min_sessions 6 → 핸들 불인정.
    highs, lows, closes = _cup_with_handle()
    frame, dates = _frame(highs, lows, closes)
    det = _detector(_handle_cfg(cfg, min_sessions=6), frame, dates)
    base = det.base_asof(dates[35])
    assert base is not None and base.handle is False
    assert base.pivot == pytest.approx(100.0)


def test_handle_depth_boundary(cfg: Config) -> None:
    # 눌림 깊이 (95-91.5)/95 = 3.7% > 상한 3% → 핸들 불인정.
    highs, lows, closes = _cup_with_handle()
    frame, dates = _frame(highs, lows, closes)
    det = _detector(_handle_cfg(cfg, max_depth=3.0), frame, dates)
    base = det.base_asof(dates[35])
    assert base is not None and base.handle is False


def test_handle_requires_upper_half(cfg: Config) -> None:
    # 눌림 저가 89 < 중점 90(=80+0.5×20) → 상반부 요건 미달, 핸들 불인정.
    highs, lows, closes = _cup_with_handle(pullback_low=89.0)
    frame, dates = _frame(highs, lows, closes)
    det = _detector(_handle_cfg(cfg), frame, dates)
    base = det.base_asof(dates[35])
    assert base is not None and base.handle is False


def test_handle_vanishes_after_peak_retouch(cfg: Config) -> None:
    # day35 고가 96이 손잡이 고점(95)을 넘으면 눌림 시계 리셋 → 다음 세션엔
    # 핸들 소멸(진입 기회는 돌파일 하루), 절대 고점 피벗으로 복귀.
    highs, lows, closes = _cup_with_handle()
    frame, dates = _frame(highs, lows, closes)
    det = _detector(_handle_cfg(cfg), frame, dates)
    base = det.base_asof(dates[36])
    assert base is not None and base.handle is False
    assert base.pivot == pytest.approx(100.0)


def test_handle_lookahead_free(cfg: Config) -> None:
    # 돌파일(day35) 이후 바를 조작해도 base_asof(day35)의 핸들 확정은 불변.
    highs, lows, closes = _cup_with_handle()
    frame_a, dates = _frame(highs, lows, closes)
    highs_b, lows_b, closes_b = list(highs), list(lows), list(closes)
    highs_b[35], lows_b[35], closes_b[35] = 75.0, 70.0, 72.0   # 돌파 대신 급락
    frame_b, _ = _frame(highs_b, lows_b, closes_b)
    hcfg = _handle_cfg(cfg)
    base_a = _detector(hcfg, frame_a, dates).base_asof(dates[35])
    base_b = _detector(hcfg, frame_b, dates).base_asof(dates[35])
    assert base_a is not None and base_b is not None
    assert base_a.pivot == pytest.approx(base_b.pivot) == pytest.approx(95.0)
    assert base_a.handle is base_b.handle is True
