"""R4b 추세 복귀 재진입 (P4, plan/p4_reentry.md).

자격 부여(§6② 전량 청산만)·50MA 회복 카운터·유효 기간·체결(1차만·시가)·체인,
그리고 기본 꺼짐(null)이 현행과 동치임을 검증한다. 골든 불변은 integration의
골든 다이제스트가 보증한다(기본 null = 코드 경로 자체가 안 열림).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from oneil_bt.analysis.override import apply_overrides
from oneil_bt.domain.bar import PriceFrame
from oneil_bt.domain.config import Config, ConfigError, ReentryCfg
from oneil_bt.domain.enums import EntryReason, ExitReason, Market
from oneil_bt.data.metadata import SymbolMeta
from oneil_bt.engine.engine import BacktestEngine
from tests.fixtures.synthetic import business_dates, ohlcv_frame
from tests.unit.test_engine import MemSource

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


def _reentry_cfg(cfg: Config, confirm: int = 3, window: int = 6) -> Config:
    return apply_overrides(cfg, {
        "reentry.confirm_sessions": confirm,
        "reentry.window_months": window,
    })


# --------------------------------------------------------------------------- #
# config 파싱·검증
# --------------------------------------------------------------------------- #
def test_yaml_default_is_candidate(cfg: Config) -> None:
    # P4 승인(2026-07-16): c5w3 채택 — v3-6 기본값. v3-5 재현은 null/null.
    assert cfg.reentry.ma == 50
    assert cfg.reentry.confirm_sessions == 5
    assert cfg.reentry.window_months == 3
    assert cfg.reentry.enabled is True


def test_confirm_requires_window() -> None:
    with pytest.raises(ConfigError, match="window_months"):
        ReentryCfg.from_dict({"ma": 50, "confirm_sessions": 3})


def test_confirm_must_be_positive() -> None:
    with pytest.raises(ConfigError, match="confirm_sessions"):
        ReentryCfg.from_dict({"ma": 50, "confirm_sessions": 0, "window_months": 6})


def test_window_without_confirm_is_ignored_off() -> None:
    # 스윕 그리드의 off 행(confirm null × window 값) 호환 — P3 핸들 패턴과 동일.
    rc = ReentryCfg.from_dict({"ma": 50, "confirm_sessions": None, "window_months": 6})
    assert rc.enabled is False


def test_window_days_conversion() -> None:
    rc = ReentryCfg.from_dict({"ma": 50, "confirm_sessions": 3, "window_months": 12})
    assert rc.window_days == round(12 * 365.25 / 12.0)  # R3b와 동일 환산


# --------------------------------------------------------------------------- #
# 시나리오 빌더 — 돌파 진입 → 60MA 이탈 전량 청산 → 50MA 회복 → 재진입
# --------------------------------------------------------------------------- #
UPTREND = 260   # 52주 워밍업 후 베이스가 오도록
BASE = 30
PEAK = 300.0


def _closes_and_vols(
    *,
    drop_len: int = 6,
    drop_px: float = 292.0,
    recover: list[float] | None = None,
) -> tuple[list[float], list[float]]:
    """상승 → 베이스 → 돌파(305) → 랠리(→360) → 이탈 → 회복 시퀀스.

    drop_px 기본 292는 60MA(≈304) 아래·손절가(피라미딩 후 평단 ≈311의 −2×ATR
    ≈284) 위 — §6② 추세 이탈(HALF→REST)로 전량 청산되는 경로다. 손절(STOP)
    청산을 원하면 250처럼 손절가 아래로 떨어뜨린다.
    """
    closes = (list(np.linspace(100.0, PEAK, UPTREND))
              + [295.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(BASE)]
              + [305.0]                                  # 돌파일
              + list(np.linspace(308.0, 360.0, 14))      # 랠리
              + [drop_px] * drop_len                     # 60MA 이탈(HALF→REST)
              + (recover if recover is not None else [340.0] * 10))
    n = len(closes)
    vols = ([5_000.0] * UPTREND + [2_000.0] * (BASE - 10) + [800.0] * 10
            + [6_000.0] * (n - UPTREND - BASE))
    return closes, vols


def _source(
    closes: list[float],
    vols: list[float],
    *,
    index_closes: list[float] | None = None,
) -> tuple[MemSource, list[date]]:
    n = len(closes)
    dates = business_dates("2019-01-01", n)
    df = ohlcv_frame(dates, closes, vols, values=[2.0e10] * n)
    idx = index_closes if index_closes is not None else list(np.linspace(100.0, 150.0, n))
    index = PriceFrame("KOSPI", ohlcv_frame(dates, idx, 0.0))
    source = MemSource(
        {"AAA": PriceFrame("AAA", df)},
        {"AAA": SymbolMeta("AAA", "AAA", Market.KOSPI, None, None)},
        {Market.KOSPI: index},
    )
    return source, dates


def _events(result, name: str):
    return [e for e in result.events if e.event == name]


# --------------------------------------------------------------------------- #
# 본선 — §6② 전량 청산 후 3일 회복 유지 → 익일 시가 재진입
# --------------------------------------------------------------------------- #
def test_reentry_after_60ma_full_exit(cfg: Config) -> None:
    closes, vols = _closes_and_vols()
    source, dates = _source(closes, vols)
    rcfg = _reentry_cfg(cfg, confirm=3, window=6)
    result = BacktestEngine(source, rcfg, initial_cash=1.0e8).run(dates[0], dates[-1])

    # 전제: 60MA 이탈 절반 → 회복 실패 잔량(전량 청산 완료).
    reasons = [t.closed.exit_fill.reason for t in result.trades]
    assert ExitReason.TREND_60MA_HALF in reasons
    assert ExitReason.TREND_60MA_REST in reasons
    assert ExitReason.STOP not in reasons

    # 회복 3일(340 ≥ 50MA) 유지 → 트리거 확인 → 익일 시가(=340) 체결.
    triggers = _events(result, "REENTRY_TRIGGER")
    entries = _events(result, "REENTRY_ENTRY")
    assert triggers and entries
    trig, ent = triggers[0], entries[0]
    assert trig.detail["streak"] == 3
    assert dates.index(ent.date) == dates.index(trig.date) + 1
    assert ent.detail["price"] == pytest.approx(340.0)  # open=close 합성 픽스처

    # §3.3 발동 분리 집계.
    acts = [a for a in result.rule_activations if a.rule == "r4b_reentry_entry"]
    assert len(acts) == 1
    assert acts[0].detail["exit_reason"] == str(ExitReason.TREND_60MA_REST)

    # 재진입 후 보유 유지(340 > 손절 306·60MA 위) → 종료 시점 노출 > 0.
    assert result.equity_curve[-1].n_positions == 1


def test_reentry_off_override_no_events(cfg: Config) -> None:
    # null 오버라이드 = v3-5 재현 경로(코드가 아예 안 열림).
    closes, vols = _closes_and_vols()
    source, dates = _source(closes, vols)
    off = apply_overrides(cfg, {
        "reentry.confirm_sessions": None, "reentry.window_months": None,
    })
    result = BacktestEngine(source, off, initial_cash=1.0e8).run(dates[0], dates[-1])
    assert not _events(result, "REENTRY_TRIGGER")
    assert not _events(result, "REENTRY_ENTRY")
    assert not [a for a in result.rule_activations if a.rule == "r4b_reentry_entry"]


def test_reentry_one_tranche_no_pyramid(cfg: Config) -> None:
    # 재진입 후 +5% 이상 상승해도 피라미딩(2·3차)이 없어야 한다(1차만, Q6 집행).
    closes, vols = _closes_and_vols(recover=[340.0] * 3 + [358.0] * 7)
    source, dates = _source(closes, vols)
    rcfg = _reentry_cfg(cfg)
    result = BacktestEngine(source, rcfg, initial_cash=1.0e8).run(dates[0], dates[-1])
    ent = _events(result, "REENTRY_ENTRY")[0]
    pyramids_after = [e for e in _events(result, "PYRAMID") if e.date >= ent.date]
    assert not pyramids_after


# --------------------------------------------------------------------------- #
# 자격 — 손절 청산은 불가(§9 복수 매매 금지 유지)
# --------------------------------------------------------------------------- #
def test_no_reentry_after_stop_exit(cfg: Config) -> None:
    # 돌파 직후 급락 → 손절(STOP) 전량 청산. 이후 회복해도 재진입 자격이 없다.
    closes = (list(np.linspace(100.0, PEAK, UPTREND))
              + [295.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(BASE)]
              + [305.0]              # 돌파 진입(≈306)
              + [250.0] * 4          # 손절가(-10% 캡 ≈275) 하회 → STOP
              + [340.0] * 10)        # 회복 — 자격 없음
    n = len(closes)
    vols = ([5_000.0] * UPTREND + [2_000.0] * (BASE - 10) + [800.0] * 10
            + [6_000.0] * (n - UPTREND - BASE))
    source, dates = _source(closes, vols)
    rcfg = _reentry_cfg(cfg)
    result = BacktestEngine(source, rcfg, initial_cash=1.0e8).run(dates[0], dates[-1])
    reasons = [t.closed.exit_fill.reason for t in result.trades]
    assert ExitReason.STOP in reasons
    assert not _events(result, "REENTRY_TRIGGER")
    assert not _events(result, "REENTRY_ENTRY")


# --------------------------------------------------------------------------- #
# 카운터 — 하회 시 리셋(연속 M일)
# --------------------------------------------------------------------------- #
def test_streak_resets_on_dip_below_ma(cfg: Config) -> None:
    # 회복 2일 → 하루 하회(250) → 다시 3일 회복: 트리거는 두 번째 시퀀스에서만.
    closes, vols = _closes_and_vols(
        recover=[340.0, 340.0, 250.0, 340.0, 340.0, 340.0, 340.0, 340.0])
    source, dates = _source(closes, vols)
    rcfg = _reentry_cfg(cfg, confirm=3)
    result = BacktestEngine(source, rcfg, initial_cash=1.0e8).run(dates[0], dates[-1])
    triggers = _events(result, "REENTRY_TRIGGER")
    assert triggers
    first = triggers[0]
    # 하회(3번째 회복 세션) 이후 3연속 — 회복 시퀀스 6번째 세션에서 확인.
    drop_start = UPTREND + BASE + 1 + 14 + 6  # 회복 시퀀스 시작 인덱스
    assert dates.index(first.date) == drop_start + 5
    assert first.detail["streak"] == 3


# --------------------------------------------------------------------------- #
# 유효 기간 — 만료 후 자격 소멸 (제어쌍: 넉넉한 W에선 재진입 성사)
# --------------------------------------------------------------------------- #
def test_ticket_expires_after_window(cfg: Config) -> None:
    # 청산 후 25세션(≈35달력일) 바닥 → 회복. W=1개월(≈30일)이면 만료, W=12면 성사.
    closes, vols = _closes_and_vols(drop_len=25, recover=[340.0] * 8)
    source, dates = _source(closes, vols)

    expired = BacktestEngine(
        source, _reentry_cfg(cfg, confirm=3, window=1), initial_cash=1.0e8
    ).run(dates[0], dates[-1])
    assert not _events(expired, "REENTRY_ENTRY")

    alive = BacktestEngine(
        source, _reentry_cfg(cfg, confirm=3, window=12), initial_cash=1.0e8
    ).run(dates[0], dates[-1])
    assert _events(alive, "REENTRY_ENTRY"), "동일 시나리오 W=12에선 성사(만료가 원인)"


# --------------------------------------------------------------------------- #
# 게이트 — 시장필터 비정상이면 대기 차단
# --------------------------------------------------------------------------- #
def test_market_filter_blocks_reentry(cfg: Config) -> None:
    closes, vols = _closes_and_vols()
    n = len(closes)
    # 지수가 청산 직전부터 급락 → 회복 구간 내내 60MA 아래(CAUTION/DEFENSE).
    crash_at = UPTREND + BASE + 10
    idx = list(np.linspace(100.0, 150.0, n))
    for i in range(crash_at, n):
        idx[i] = 80.0
    source, dates = _source(closes, vols, index_closes=idx)
    rcfg = _reentry_cfg(cfg)
    result = BacktestEngine(source, rcfg, initial_cash=1.0e8).run(dates[0], dates[-1])
    assert not _events(result, "REENTRY_TRIGGER")
    assert not _events(result, "REENTRY_ENTRY")


# --------------------------------------------------------------------------- #
# 체인 — 재진입 트레이드의 §6② 전량 청산이 새 자격을 부여
# --------------------------------------------------------------------------- #
def test_chained_reentry_and_trade_log_reason(cfg: Config) -> None:
    # 1차 재진입(340, 손절 −10% 캡 = 306) → 60MA가 손절가 위로 올라올 때까지
    # 보유(345×40, 60MA ≈ 339) → 315로 이탈(>306 손절 미발동, <60MA) →
    # HALF→REST 전량 청산 → 재회복(350) → 2차 재진입.
    recover = ([340.0] * 4          # 1차 재진입 성사(3일 확인 + 체결일)
               + [345.0] * 40       # 보유 — 60MA를 재진입 손절가 위로 견인
               + [315.0] * 6        # 다시 HALF→REST 전량 청산
               + [350.0] * 5)       # 재회복 → 2차 재진입
    closes, vols = _closes_and_vols(recover=recover)
    source, dates = _source(closes, vols)
    rcfg = _reentry_cfg(cfg, confirm=3, window=6)
    result = BacktestEngine(source, rcfg, initial_cash=1.0e8).run(dates[0], dates[-1])

    acts = [a for a in result.rule_activations if a.rule == "r4b_reentry_entry"]
    assert len(acts) == 2, "체인: 두 번째 자격도 §6② 전량 청산이 부여"

    # 트레이드 로그: 1차 재진입 트레이드의 청산 행이 REENTRY_50MA 사유·base_stage=0.
    reentry_rows = [t for t in result.trades
                    if t.closed.entry_fill.reason is EntryReason.REENTRY_50MA]
    assert reentry_rows
    assert all(t.base_stage == 0 for t in reentry_rows)
    assert {t.closed.exit_fill.reason for t in reentry_rows} <= {
        ExitReason.TREND_60MA_HALF, ExitReason.TREND_60MA_REST,
    }


# --------------------------------------------------------------------------- #
# 룩어헤드 가드 — 재진입 이후 미래 바 조작이 그 이전 자본곡선 불변
# --------------------------------------------------------------------------- #
def test_reentry_no_lookahead(cfg: Config) -> None:
    closes, vols = _closes_and_vols()
    source, dates = _source(closes, vols)
    rcfg = _reentry_cfg(cfg)
    base_result = BacktestEngine(source, rcfg, initial_cash=1.0e8).run(
        dates[0], dates[-1]
    )
    ent = _events(base_result, "REENTRY_ENTRY")[0]
    cut = dates.index(ent.date) + 2

    frame = source._frames["AAA"]
    df = frame.df.copy()
    df.iloc[cut:, df.columns.get_loc("close")] *= 3.0
    df.iloc[cut:, df.columns.get_loc("high")] *= 3.0
    tampered = MemSource(
        {"AAA": PriceFrame("AAA", df)}, source._metas, source._indices
    )
    tampered_result = BacktestEngine(tampered, rcfg, initial_cash=1.0e8).run(
        dates[0], dates[-1]
    )
    for a, b in zip(base_result.equity_curve[:cut], tampered_result.equity_curve[:cut]):
        assert a.date == b.date
        assert a.equity == pytest.approx(b.equity)
        assert a.n_positions == b.n_positions
