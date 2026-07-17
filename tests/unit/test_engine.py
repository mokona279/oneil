"""백테스트 엔진 (Phase 6 DoD).

단일종목·포트폴리오 end-to-end 동작, 슬롯·현금·우선순위 상호작용, 결정론(2회 동일),
룩어헤드 가드(미래 바 조작이 과거 판정·자본곡선을 바꾸지 않음)를 검증한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from oneil_bt.analysis.override import apply_overrides
from oneil_bt.domain.bar import PriceFrame
from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import ExitReason, Market, MarketState
from oneil_bt.data.metadata import SymbolMeta
from oneil_bt.engine.engine import BacktestEngine
from tests.fixtures.synthetic import business_dates, ohlcv_frame

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


# --------------------------------------------------------------------------- #
# 인메모리 데이터 소스 (DataSource Protocol 충족)
# --------------------------------------------------------------------------- #
class MemSource:
    def __init__(
        self,
        frames: dict[str, PriceFrame],
        metas: dict[str, SymbolMeta],
        indices: dict[Market, PriceFrame],
    ) -> None:
        self._frames = frames
        self._metas = metas
        self._indices = indices

    def symbols(self) -> list[str]:
        return sorted(self._frames)

    def load_prices(self, symbol: str) -> PriceFrame:
        return self._frames[symbol]

    def load_index(self, market: Market) -> PriceFrame:
        return self._indices[market]

    def meta(self, symbol: str) -> SymbolMeta:
        return self._metas[symbol]


# --------------------------------------------------------------------------- #
# 시나리오 빌더: 긴 상승추세 → 얕은 베이스(다짐) → 돌파
# --------------------------------------------------------------------------- #
UPTREND = 260  # 52주(252세션) 워밍업 완료 후 베이스가 오도록 넉넉히
BASE = 30
TAIL = 20
N = UPTREND + BASE + TAIL  # 총 세션


def _breakout_closes(peak: float = 300.0, base_px: float = 295.0) -> list[float]:
    up = list(np.linspace(100.0, peak, UPTREND))
    # 베이스: 피벗(=peak 고가) 아래에서 얕게 다진다. 마지막 10일은 더 조용히.
    base = []
    for i in range(BASE):
        base.append(base_px + (2.0 if i % 2 == 0 else -2.0))
    # 돌파일(꼬리 첫날): 피벗 상회 후 계속 상승.
    tail = [305.0] + list(np.linspace(308.0, 360.0, TAIL - 1))
    return up + base + tail


def _breakout_volumes() -> list[float]:
    up = [5_000.0] * UPTREND
    base = [2_000.0] * (BASE - 10) + [800.0] * 10  # 드라이업: 직전 10일 조용
    tail = [6_000.0] * TAIL                         # 돌파일 거래량 급증
    return up + base + tail


def _mem_source(symbols: list[str] | None = None) -> tuple[MemSource, list[date]]:
    dates = business_dates("2019-01-01", N)
    symbols = symbols or ["AAA"]
    closes = _breakout_closes()
    vols = _breakout_volumes()
    values = [2.0e10] * N  # 거래대금 100억 이상(트렌드 템플릿 7조건)

    frames: dict[str, PriceFrame] = {}
    metas: dict[str, SymbolMeta] = {}
    for sym in symbols:
        frames[sym] = PriceFrame(sym, ohlcv_frame(dates, closes, vols, values=values))
        metas[sym] = SymbolMeta(sym, sym, Market.KOSPI, None, None)

    # 지수: 완만한 상승 → 60/120일선 위 NORMAL 유지, 종목 RS(>지수) 성립.
    index_close = list(np.linspace(100.0, 150.0, N))
    index = PriceFrame("KOSPI", ohlcv_frame(dates, index_close, 0.0))
    return MemSource(frames, metas, {Market.KOSPI: index}), dates


# --------------------------------------------------------------------------- #
# 단일종목 모드 — 진입·자본곡선·트레이드 산출
# --------------------------------------------------------------------------- #
def test_single_symbol_produces_entry_and_equity_curve(cfg: Config) -> None:
    source, dates = _mem_source()
    engine = BacktestEngine(source, cfg, initial_cash=1.0e8)
    result = engine.run(dates[0], dates[-1], symbols=["AAA"])

    # 자본곡선은 세션마다 1행.
    assert len(result.equity_curve) == N
    # 돌파 진입이 최소 1회 발생.
    entries = [e for e in result.events if e.event == "ENTRY"]
    assert entries, "돌파 진입이 발생해야 한다"
    ent = entries[0]
    assert ent.symbol == "AAA"
    # 진입가는 피벗 이상(장중 돌파 체결).
    assert ent.detail["price"] >= ent.detail["pivot"]
    # 매수 후 노출도 > 0.
    assert any(rec.exposure_pct > 0 for rec in result.equity_curve)


def test_pyramiding_after_confirmed_breakout(cfg: Config) -> None:
    source, dates = _mem_source()
    engine = BacktestEngine(source, cfg, initial_cash=1.0e8)
    result = engine.run(dates[0], dates[-1], symbols=["AAA"])
    # 돌파일 거래량 급증(6000 ≥ 1.5×평균)이므로 2·3차 피라미딩이 발생해야 한다.
    assert not [e for e in result.events if e.event == "VOL_FAIL"]
    assert [e for e in result.events if e.event == "PYRAMID"]


# --------------------------------------------------------------------------- #
# 결정론 — 2회 실행 동일
# --------------------------------------------------------------------------- #
def test_deterministic_two_runs_identical(cfg: Config) -> None:
    source, dates = _mem_source(["AAA", "BBB", "CCC"])
    e1 = BacktestEngine(source, cfg, initial_cash=1.0e8).run(dates[0], dates[-1])
    e2 = BacktestEngine(source, cfg, initial_cash=1.0e8).run(dates[0], dates[-1])
    curve1 = [(r.date, r.equity, r.n_positions) for r in e1.equity_curve]
    curve2 = [(r.date, r.equity, r.n_positions) for r in e2.equity_curve]
    assert curve1 == curve2
    assert len(e1.trades) == len(e2.trades)
    assert [ev.event for ev in e1.events] == [ev.event for ev in e2.events]


# --------------------------------------------------------------------------- #
# 슬롯 제약 — 동일 신호 다수 종목이 max_positions로 제한
# --------------------------------------------------------------------------- #
def test_slot_limit_caps_concurrent_positions(cfg: Config) -> None:
    syms = [f"S{i:02d}" for i in range(cfg.portfolio.max_positions + 3)]
    source, dates = _mem_source(syms)
    engine = BacktestEngine(source, cfg, initial_cash=1.0e9)
    result = engine.run(dates[0], dates[-1])
    max_conc = max(rec.n_positions for rec in result.equity_curve)
    assert max_conc <= cfg.portfolio.max_positions


# --------------------------------------------------------------------------- #
# 룩어헤드 가드 — 돌파 이후 미래 바 조작이 그 이전 자본곡선을 바꾸지 않음
# --------------------------------------------------------------------------- #
def test_no_lookahead_future_bars_do_not_change_past(cfg: Config) -> None:
    source, dates = _mem_source()
    base_result = BacktestEngine(source, cfg, initial_cash=1.0e8).run(
        dates[0], dates[-1], symbols=["AAA"]
    )
    # 진입일 인덱스 찾기.
    entry_ev = next(e for e in base_result.events if e.event == "ENTRY")
    entry_pos = dates.index(entry_ev.date)

    # 진입일 +5 이후 종가를 크게 조작한 새 소스.
    frame = source._frames["AAA"]
    df = frame.df.copy()
    cut = entry_pos + 5
    df.iloc[cut:, df.columns.get_loc("close")] *= 3.0
    df.iloc[cut:, df.columns.get_loc("high")] *= 3.0
    tampered = MemSource(
        {"AAA": PriceFrame("AAA", df)},
        source._metas,
        source._indices,
    )
    tampered_result = BacktestEngine(tampered, cfg, initial_cash=1.0e8).run(
        dates[0], dates[-1], symbols=["AAA"]
    )

    # 조작 지점 이전의 자본곡선은 완전히 동일해야 한다(과거는 미래를 못 본다).
    for a, b in zip(
        base_result.equity_curve[: cut], tampered_result.equity_curve[: cut]
    ):
        assert a.date == b.date
        assert a.equity == pytest.approx(b.equity)
        assert a.n_positions == b.n_positions


# --------------------------------------------------------------------------- #
# R3a(Q5a) — max_stage 초과 베이스의 감액 진입
# --------------------------------------------------------------------------- #
def test_overlimit_stage_blocked_without_factor(cfg: Config) -> None:
    # 픽스처 베이스는 1단계 — max_stage=0으로 낮추면 '초과 베이스'가 된다.
    # 계수 없음(현행) → 진입 금지 그대로.
    source, dates = _mem_source()
    over = apply_overrides(cfg, {"base.stage.max_stage": 0})
    result = BacktestEngine(source, over, initial_cash=1.0e8).run(
        dates[0], dates[-1], symbols=["AAA"]
    )
    assert not [e for e in result.events if e.event == "ENTRY"]


def test_overlimit_stage_enters_with_reduced_weight(cfg: Config) -> None:
    # 계수 0.5 → 초과 베이스도 진입하되 목표 비중(=1차 수량)이 절반으로 준다.
    source, dates = _mem_source()
    full = BacktestEngine(source, cfg, initial_cash=1.0e8).run(
        dates[0], dates[-1], symbols=["AAA"]
    )
    reduced_cfg = apply_overrides(cfg, {
        "base.stage.max_stage": 0,
        "base.stage.overlimit_weight_factor": 0.5,
    })
    reduced = BacktestEngine(source, reduced_cfg, initial_cash=1.0e8).run(
        dates[0], dates[-1], symbols=["AAA"]
    )
    q_full = next(e for e in full.events if e.event == "ENTRY").detail["qty"]
    ent = next(e for e in reduced.events if e.event == "ENTRY")
    q_half = ent.detail["qty"]
    # 같은 돌파일에 진입하되 수량은 절반(정수주 floor ±1).
    assert ent.date == next(e for e in full.events if e.event == "ENTRY").date
    assert abs(q_half * 2 - q_full) <= 2


# --------------------------------------------------------------------------- #
# Q11 — 피라미딩 재계산 손절가 하향 금지 클램프
# --------------------------------------------------------------------------- #
def _atr_spike_source() -> tuple[MemSource, list[date]]:
    """진입 후 ATR이 급증해 2차 체결 시 재계산 손절가가 기존보다 낮아지는 시나리오.

    돌파 진입(≈306, 손절 ≈282) → 5일 장중 급변동(저가 -20%, 종가 유지)으로 ATR 폭증
    → 2차 트리거(+2.5%) 체결 시 재계산 = 평단-10% 캡 바닥(≈278) < 기존 282.
    이후 종가 280: 클램프면 손절 발동(280 ≤ 282), 아니면 미발동(280 > 278).
    """
    n_wide = 5
    closes = (list(np.linspace(100.0, 300.0, UPTREND))
              + [295.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(BASE)]
              + [305.0]                    # 돌파일: high=311.1 ≥ 피벗 306
              + [305.0] * n_wide           # 장중 급변동 구간(종가 유지)
              + [315.0]                    # 2차 트리거(313.65) 도달·체결
              + [280.0] * (TAIL - n_wide - 2))
    vols = ([5_000.0] * UPTREND + [2_000.0] * (BASE - 10) + [800.0] * 10
            + [6_000.0] * TAIL)
    n = len(closes)
    dates = business_dates("2019-01-01", n)
    df = ohlcv_frame(dates, closes, vols, values=[2.0e10] * n)
    # 급변동 구간의 장중 저가만 -20%로 끌어내려 TR을 키운다(종가·고가는 그대로).
    lo = UPTREND + BASE + 1
    df.iloc[lo:lo + n_wide, df.columns.get_loc("low")] = 305.0 * 0.80
    index = PriceFrame("KOSPI", ohlcv_frame(dates, list(np.linspace(100.0, 150.0, n)), 0.0))
    source = MemSource(
        {"AAA": PriceFrame("AAA", df)},
        {"AAA": SymbolMeta("AAA", "AAA", Market.KOSPI, None, None)},
        {Market.KOSPI: index},
    )
    return source, dates


def test_stop_recalc_can_lower_without_clamp(cfg: Config) -> None:
    # v3-4 이전(no_lower_recalc=false): 재계산이 손절가를 내려 280 종가에 손절 미발동
    # — Q11이 봉쇄한 구멍의 재현(명시적 오버라이드로 과거 동작 고정).
    source, dates = _atr_spike_source()
    old_cfg = apply_overrides(cfg, {"stop.no_lower_recalc": False})
    result = BacktestEngine(source, old_cfg, initial_cash=1.0e8).run(
        dates[0], dates[-1], symbols=["AAA"]
    )
    assert [e for e in result.events if e.event == "PYRAMID"], "2차 체결 전제"
    stops = [t for t in result.trades if t.closed.exit_fill.reason is ExitReason.STOP]
    assert not stops, "하향된 손절가(≈278)라 280 종가에선 발동하지 않아야"


def test_stop_no_lower_recalc_clamps(cfg: Config) -> None:
    # Q11 클램프: 재계산해도 기존 손절가(≈282) 유지 → 280 종가에 손절 발동.
    source, dates = _atr_spike_source()
    clamped_cfg = apply_overrides(cfg, {"stop.no_lower_recalc": True})
    result = BacktestEngine(source, clamped_cfg, initial_cash=1.0e8).run(
        dates[0], dates[-1], symbols=["AAA"]
    )
    assert [e for e in result.events if e.event == "PYRAMID"], "2차 체결 전제"
    stops = [t for t in result.trades if t.closed.exit_fill.reason is ExitReason.STOP]
    assert stops, "클램프로 손절가가 유지돼 280 종가에 발동해야"
    # §3.3 분리 집계: 클램프 발동(하향 재계산 거부)이 진단 채널에 기록돼야 한다.
    clamps = [a for a in result.rule_activations if a.rule == "q11_stop_clamp"]
    assert clamps and clamps[0].symbol == "AAA"
    assert clamps[0].detail["recalc"] < clamps[0].detail["kept"]


# --------------------------------------------------------------------------- #
# 빈 유니버스/무신호 — 자본곡선만, 자본 보존
# --------------------------------------------------------------------------- #
def test_flat_market_no_trades_preserves_cash(cfg: Config) -> None:
    dates = business_dates("2019-01-01", 200)
    flat = PriceFrame("AAA", ohlcv_frame(dates, 100.0, 1_000.0, values=[2e10] * 200))
    index = PriceFrame("KOSPI", ohlcv_frame(dates, 100.0, 0.0))
    source = MemSource(
        {"AAA": flat},
        {"AAA": SymbolMeta("AAA", "AAA", Market.KOSPI, None, None)},
        {Market.KOSPI: index},
    )
    result = BacktestEngine(source, cfg, initial_cash=5.0e7).run(dates[0], dates[-1])
    assert not result.trades
    assert result.final_equity == pytest.approx(5.0e7)
    assert all(rec.market_states[Market.KOSPI] in MarketState for rec in result.equity_curve)


# --------------------------------------------------------------------------- #
# Q14(plan/q14_rs_rank.md) — 전시장 RS 백분위 랭크 게이트
# --------------------------------------------------------------------------- #
def _rank_gate_source() -> tuple[MemSource, list[date]]:
    """RS(6M)가 뚜렷이 다른 4종목 — peak(상승 목표가)이 클수록 상승폭이 커 RS가 높다.

    베이스 형태(우상향 → 얕은 다짐 → 돌파)는 `_breakout_closes`와 동일하게 피벗
    기준 절대 간격(피벗-5 다짐 / 피벗+5 돌파 / 피벗+8~+60 후속)을 유지한다 — peak=300
    이면 `_breakout_closes(300, 295)`와 완전히 같은 값이라 검증된 돌파 메커니즘을
    그대로 재사용한다. peak을 바꿔도 추격 상한(5%)·품질 게이트(전부 %상대)는 동일하게
    통과하고, 126일 수익률(rs_6m)만 peak에 비례해 갈린다.
    """
    peaks = {"S1": 220.0, "S2": 280.0, "S3": 340.0, "S4": 400.0}
    dates = business_dates("2019-01-01", N)

    def closes_for(peak: float) -> list[float]:
        base_px = peak - 5.0
        up = list(np.linspace(100.0, peak, UPTREND))
        base = [base_px + (2.0 if i % 2 == 0 else -2.0) for i in range(BASE)]
        tail = [peak + 5.0] + list(np.linspace(peak + 8.0, peak + 60.0, TAIL - 1))
        return up + base + tail

    vols = _breakout_volumes()
    values = [2.0e10] * N
    frames: dict[str, PriceFrame] = {}
    metas: dict[str, SymbolMeta] = {}
    for sym, peak in peaks.items():
        df = ohlcv_frame(dates, closes_for(peak), vols, values=values)
        frames[sym] = PriceFrame(sym, df)
        metas[sym] = SymbolMeta(sym, sym, Market.KOSPI, None, None)

    index_close = list(np.linspace(100.0, 150.0, N))
    index = PriceFrame("KOSPI", ohlcv_frame(dates, index_close, 0.0))
    return MemSource(frames, metas, {Market.KOSPI: index}), dates


def test_rs_rank_gate_off_matches_baseline(cfg: Config) -> None:
    # rank_top_pct=None(기본) — 랭크와 무관하게 전 종목이 게이트를 통과해야 한다.
    source, dates = _rank_gate_source()
    result = BacktestEngine(source, cfg, initial_cash=1.0e9).run(dates[0], dates[-1])
    for sym in ("S1", "S2", "S3", "S4"):
        f = result.entry_funnel[sym]
        assert f.gate_rs_rank_ok == f.breakout
    entered = {e.symbol for e in result.events if e.event == "ENTRY"}
    assert {"S1", "S2", "S3", "S4"} <= entered


def test_rs_rank_gate_blocks_low_percentile_symbol(cfg: Config) -> None:
    # 상위 50%만 진입 — 4종목 중 최하위(S1, RS 랭크 25%)는 막히고 최상위(S4, 100%)는 통과.
    source, dates = _rank_gate_source()
    over = apply_overrides(cfg, {"rs.rank_top_pct": 50})
    result = BacktestEngine(source, over, initial_cash=1.0e9).run(dates[0], dates[-1])

    low = result.entry_funnel["S1"]
    assert low.breakout > 0
    assert low.gate_rs_rank_ok < low.breakout
    assert not [e for e in result.events if e.event == "ENTRY" and e.symbol == "S1"]

    high = result.entry_funnel["S4"]
    assert high.breakout > 0
    assert high.gate_rs_rank_ok == high.breakout
    assert [e for e in result.events if e.event == "ENTRY" and e.symbol == "S4"]
