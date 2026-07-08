"""베이스 품질 4요건 (Phase 3B DoD).

각 요건(과열 미해당·2×ATR≤피벗10%·수축·드라이업)의 개별·복합 경계와 룩어헤드
회귀를 검증한다. 품질은 ≤d-1 정보만으로 확정된다(§6.1).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from oneil_bt.domain.bar import PriceFrame
from oneil_bt.domain.config import Config
from oneil_bt.indicators.indicator_set import IndicatorSet
from oneil_bt.rules.base_detector import Base
from oneil_bt.rules.base_quality import BaseQualityCheck
from oneil_bt.rules.overheating import OverheatingFilter
from tests.fixtures.synthetic import business_dates, ohlcv_frame

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"

START = "2020-01-06"
N = 40
BREAKOUT_POS = 30  # d = dates[30]; 품질은 ≤ dates[29] 로 판정
PIVOT = 100.0


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


def _build(
    *,
    base_vol: float = 2_000.0,
    recent_vol: float = 800.0,
    base_hi: float = 97.0,
    base_lo: float = 93.0,
    recent_hi: float = 96.0,
    recent_lo: float = 92.0,
    dip: tuple[int, float] | None = (8, 88.0),
) -> tuple[PriceFrame, list[date], Base]:
    """40세션 프레임 + 손으로 만든 유효 Base.

    구간: [0,20) 초기 베이스 · [20,30) 직전 10거래일(수축·드라이업 창) · [30,40) 돌파+꼬리.
    돌파일 d=dates[30]. 품질은 dates[29] 이하만 본다.
    """
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[float] = []
    for i in range(N):
        if i < 20:
            hi, lo, v = base_hi, base_lo, base_vol
        elif i < 30:
            hi, lo, v = recent_hi, recent_lo, recent_vol
        else:
            hi, lo, v = 103.0, 99.0, 3_000.0
        highs.append(hi)
        lows.append(lo)
        closes.append((hi + lo) / 2)
        vols.append(v)
    if dip is not None:
        di, dl = dip
        lows[di] = dl
        closes[di] = (highs[di] + dl) / 2

    dates = business_dates(START, N)
    idx = pd.DatetimeIndex(pd.to_datetime(dates).normalize(), name="date")
    df = pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": vols},
        index=idx,
    )
    frame = PriceFrame("TEST", df)
    base = Base(
        start=dates[0],
        pivot=PIVOT,
        base_low=88.0,
        depth_pct=12.0,
        weeks_elapsed=6.0,
        min_weeks=5,
        tier="<=15%",
        stage=1,
    )
    return frame, dates, base


def _check(cfg: Config, frame: PriceFrame, dates: list[date]) -> BaseQualityCheck:
    index = PriceFrame("KOSPI", ohlcv_frame(dates, 100.0))
    ind = IndicatorSet(frame, index, cfg)
    overheating = OverheatingFilter(ind, cfg)
    return BaseQualityCheck(frame, ind, overheating, cfg)


# --------------------------------------------------------------------------- #
# 4요건 모두 충족
# --------------------------------------------------------------------------- #
def test_all_four_requirements_pass(cfg: Config) -> None:
    frame, dates, base = _build()
    q = _check(cfg, frame, dates)
    res = q.passes(dates[BREAKOUT_POS], base)
    assert res.not_overheated is True
    assert res.atr_ok is True
    assert res.contraction_ok is True
    assert res.dryup_ok is True
    assert res.passed is True


# --------------------------------------------------------------------------- #
# 요건 2 — 2×ATR ≤ 피벗 10%
# --------------------------------------------------------------------------- #
def test_atr_gate_blocks_high_volatility(cfg: Config) -> None:
    # 직전 10거래일 일중 범위를 크게(87~99=12) → 2×ATR > 피벗의 10%(=10).
    frame, dates, base = _build(recent_hi=99.0, recent_lo=87.0)
    q = _check(cfg, frame, dates)
    res = q.passes(dates[BREAKOUT_POS], base)
    assert res.atr_ok is False
    assert res.passed is False


# --------------------------------------------------------------------------- #
# 요건 3 — 수축(직전 10거래일 레인지 ≤ 피벗 10%)
# --------------------------------------------------------------------------- #
def test_contraction_boundary_pass(cfg: Config) -> None:
    # 레인지 = 96 - 86 = 10 = 피벗의 10% → 경계 통과(<=).
    frame, dates, base = _build(recent_hi=96.0, recent_lo=86.0)
    q = _check(cfg, frame, dates)
    res = q.passes(dates[BREAKOUT_POS], base)
    assert res.contraction_ok is True


def test_contraction_gate_blocks_wide_range(cfg: Config) -> None:
    # 레인지 = 96 - 85 = 11 > 10 → 수축 실패.
    frame, dates, base = _build(recent_hi=96.0, recent_lo=85.0)
    q = _check(cfg, frame, dates)
    res = q.passes(dates[BREAKOUT_POS], base)
    assert res.contraction_ok is False
    assert res.passed is False


# --------------------------------------------------------------------------- #
# 요건 4 — 드라이업(직전 10거래일 평균거래량 < 베이스 전체 일평균)
# --------------------------------------------------------------------------- #
def test_dryup_gate_blocks_rising_volume(cfg: Config) -> None:
    # 직전 10거래일 거래량이 베이스 평균 이상 → 드라이업 실패.
    frame, dates, base = _build(base_vol=1_000.0, recent_vol=1_500.0)
    q = _check(cfg, frame, dates)
    res = q.passes(dates[BREAKOUT_POS], base)
    assert res.dryup_ok is False
    assert res.passed is False


def test_dryup_pass_on_quiet_recent_volume(cfg: Config) -> None:
    frame, dates, base = _build(base_vol=2_000.0, recent_vol=500.0)
    q = _check(cfg, frame, dates)
    res = q.passes(dates[BREAKOUT_POS], base)
    assert res.dryup_ok is True


# --------------------------------------------------------------------------- #
# 요건 1 — 과열: 유효 베이스가 있으면(has_base=True) 조항(a)에 해당하지 않는다(v1)
# --------------------------------------------------------------------------- #
def test_not_overheated_true_with_valid_base(cfg: Config) -> None:
    frame, dates, base = _build()
    q = _check(cfg, frame, dates)
    res = q.passes(dates[BREAKOUT_POS], base)
    assert res.not_overheated is True


# --------------------------------------------------------------------------- #
# 룩어헤드 회귀 — 돌파일 d 이후 바를 조작해도 품질 판정 불변
# --------------------------------------------------------------------------- #
def test_quality_ignores_breakout_and_future_bars(cfg: Config) -> None:
    frame_a, dates, base = _build()
    q_a = _check(cfg, frame_a, dates)
    res_a = q_a.passes(dates[BREAKOUT_POS], base)

    # d(=30) 이후를 급변시켜도 dates[30] 품질은 그대로.
    df_b = frame_a.df.copy()
    df_b.iloc[BREAKOUT_POS:, df_b.columns.get_loc("high")] = 5.0
    df_b.iloc[BREAKOUT_POS:, df_b.columns.get_loc("low")] = 1.0
    df_b.iloc[BREAKOUT_POS:, df_b.columns.get_loc("volume")] = 99_999.0
    frame_b = PriceFrame("TEST", df_b)
    q_b = _check(cfg, frame_b, dates)
    res_b = q_b.passes(dates[BREAKOUT_POS], base)

    assert res_a == res_b


# --------------------------------------------------------------------------- #
# 세션 부족 — 판정 근거 부족 시 불통과
# --------------------------------------------------------------------------- #
def test_insufficient_history_fails(cfg: Config) -> None:
    frame, dates, base = _build()
    q = _check(cfg, frame, dates)
    # 첫 세션(dates[0]) 돌파 → 직전 세션 없음 → 전 요건 불통과.
    res = q.passes(dates[0], base)
    assert res.passed is False
