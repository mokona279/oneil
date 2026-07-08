"""청산 규칙 — 추세 이탈·시장 방어·8주 룰 (규칙서 §6②③·보조, 계획서 §3.4, Phase 4B).

손절(§6①, StopRule) 외의 매도 사유를 담는다. 판정은 모두 **종가 기준**(§9)이며,
체결은 엔진이 다음날(D+1) 시가에 처리한다(§6.1). 각 규칙은 `ExitSignal`을 돌려주고,
엔진이 그 수량·사유로 `Order.exit`를 만들어 `fill_exit`로 체결한다.

TrendExitRule(§6②) — 60MA(추세선) 이탈:
    · 미이탈 상태에서 종가 < 60MA → **절반 매도**(HALF). 엔진이 그 날을
      `Position.trend_break_date`로 기록한다.
    · 이탈 상태에서 3거래일 안에 종가 ≥ 60MA로 **회복하면** 잔량 매도를 취소한다
      (ExitSignal.reason=None, CLEAR). 회복 못한 채 3거래일이 지나면 **잔량 전량**(REST).
    · config `volbreak_full`가 켜져 있고 이탈 당일 거래량이 급증(20일평균×배수)이면
      절반이 아니라 처음부터 **전량**(VOLBREAK).

MarketDefenseRule(§6③) — 지수 120MA 방어:
    · 해당 시장 상태가 DEFENSE면 그 시장 종목을 **절반**으로 축소한다.
    · 단 8주 룰 보호 종목(EightWeekGuard)은 ③에서 **제외**(정지)한다.
    · 반복 축소를 막기 위해 엔진은 방어 진입 전이일(MarketFilter.defense_triggered_on)
      에만 이 규칙을 호출한다 — 규칙 자체는 상태만 보고 순수 판정한다.

EightWeekGuard(보조) — 대시세 종목 조기매도 금지:
    돌파(진입) 후 `fast_window_days`(3주) 안에 진입가 대비 +`fast_gain_pct`(20%) 이상
    급등한 종목은 최소 `min_hold_days`(8주) 보유를 원칙으로, 그 기간 ③(시장방어)을
    정지한다. ①(손절)·②(60MA)는 그대로 적용된다. 판정은 ≤d 바만 사용(룩어헤드 없음).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from ..domain.bar import PriceFrame
from ..domain.config import Config
from ..domain.enums import ExitReason, MarketState
from ..domain.trade import Position
from ..indicators.base import asof_value
from ..indicators.indicator_set import IndicatorSet


@dataclass(frozen=True)
class ExitSignal:
    """청산 판정 결과. 엔진이 `decided_on`+1(D+1) 시가에 `qty`주를 청산한다.

    `reason`이 None이면 매도가 아니라 '회복 → 대기 청산 취소'(상태만 클리어)를 뜻한다.
    """

    decided_on: date
    reason: ExitReason | None
    qty: int

    @property
    def is_sell(self) -> bool:
        return self.reason is not None and self.qty > 0


class TrendExitRule:
    """60MA 이탈: 절반 → 3거래일 회복 실패 시 잔량 (규칙서 §6②)."""

    def __init__(self, prices: PriceFrame, ind: IndicatorSet, cfg: Config) -> None:
        self.prices = prices
        self.ind = ind
        self.ecfg = cfg.exit
        self._index: pd.DatetimeIndex = prices.df.index  # type: ignore[assignment]

    def evaluate(self, pos: Position, d: date) -> ExitSignal | None:
        row = self.prices.row(d)
        if row is None or pos.qty <= 0:
            return None  # 실거래 바 없음/무포지션 → 판정 보류
        close_d = float(row["close"])
        ma = asof_value(getattr(self.ind, f"ma{self.ecfg.ma_trend}"), d)
        if ma is None:
            return None  # 60MA 미확정(워밍업) → 판정 불가
        below = close_d < ma

        if pos.trend_break_date is None:
            if not below:
                return None
            # 거래량 급증 이탈이면 처음부터 전량(옵션).
            if self.ecfg.volbreak_full:
                vol_ma20 = asof_value(self.ind.vol_ma20, d)
                if vol_ma20 is not None and float(row["volume"]) >= (
                    vol_ma20 * self.ecfg.volbreak_mult
                ):
                    return ExitSignal(d, ExitReason.TREND_60MA_VOLBREAK, pos.qty)
            half = int(pos.qty * self.ecfg.trend_break_partial)
            return ExitSignal(d, ExitReason.TREND_60MA_HALF, half)

        # 이탈 활성 — 회복 여부 → 잔량 매도 카운트다운.
        if not below:
            return ExitSignal(d, None, 0)  # 회복 → 잔량 청산 취소(상태 클리어)
        elapsed = self._sessions_between(pos.trend_break_date, d)
        if elapsed >= self.ecfg.trend_recover_days:
            return ExitSignal(d, ExitReason.TREND_60MA_REST, pos.qty)
        return None

    def _sessions_between(self, start: date, end: date) -> int:
        """[start, end] 사이의 거래일 간격(end 위치 − start 위치). 결측일 무관."""
        s = self._index.searchsorted(pd.Timestamp(start).normalize(), side="left")
        e = self._index.searchsorted(pd.Timestamp(end).normalize(), side="left")
        return int(e) - int(s)


class MarketDefenseRule:
    """지수 120MA 이탈 → 해당 시장 종목 절반 (규칙서 §6③, 8주 룰 예외)."""

    def __init__(self, guard: "EightWeekGuard", cfg: Config) -> None:
        self.guard = guard
        self.ecfg = cfg.exit

    def evaluate(
        self, pos: Position, d: date, mstate: MarketState
    ) -> ExitSignal | None:
        if mstate is not MarketState.DEFENSE or pos.qty <= 0:
            return None
        if self.guard.protected(pos, d):
            return None  # 8주 룰 보호 → ③ 정지(①② 유지)
        reduce = int(pos.qty * self.ecfg.market_defense_reduce)
        if reduce <= 0:
            return None
        return ExitSignal(d, ExitReason.MARKET_DEFENSE_120MA, reduce)


class EightWeekGuard:
    """대시세 종목 조기매도 금지 — 돌파 후 3주 내 +20%면 8주 보유 (규칙서 §6 보조)."""

    def __init__(self, prices: PriceFrame, cfg: Config) -> None:
        self.prices = prices
        self.ewcfg = cfg.exit.eight_week
        self._highs = prices.df["high"]

    def protected(self, pos: Position, d: date) -> bool:
        """d 시점에 8주 룰 보호 상태인가(fast-gain 달성 & 최소보유 기간 이내)."""
        days_held = (d - pos.entry_date).days
        if days_held < 0 or days_held >= self.ewcfg.min_hold_days:
            return False  # 진입 전 / 8주 경과 → 보호 해제
        if pos.entry_price <= 0:
            return False
        window_end = min(
            d, pos.entry_date + timedelta(days=self.ewcfg.fast_window_days)
        )
        lo = pd.Timestamp(pos.entry_date).normalize()
        hi = pd.Timestamp(window_end).normalize()
        seg = self._highs.loc[lo:hi]
        if seg.empty:
            return False
        target = pos.entry_price * (1.0 + self.ewcfg.fast_gain_pct / 100.0)
        return bool(seg.max() >= target)
