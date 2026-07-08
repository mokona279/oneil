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

from oneil_bt.domain.bar import PriceFrame
from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import Market, MarketState
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
