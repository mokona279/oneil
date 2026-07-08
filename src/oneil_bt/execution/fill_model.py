"""일봉 기반 체결 모델 (계획서 §3.5, §6.2 체결 가정표).

당일 바(OHLC)와 주문(트리거·상한)을 대조해 **결정론적** 체결가를 만든다. 룩어헤드는
구조적으로 배제된다: 체결가는 그 바의 O/H/L만 쓰고 미래 바를 보지 않는다.

기호: `P`=피벗/트리거, `O/H/L`=당일 시고저, `cap`=지정가 상한.

1차 돌파(`fill_entry`, STOP_BUY):
    - `H < P` → 장중 피벗 미도달, 미체결(None).
    - 기본: `fill = max(O, P)`. `O ≤ P`면 스탑이 피벗에서 발동 → `P` 체결.
    - 갭업이 추격 상한 이내(`O ≤ cap`) → `O` 체결.
    - 갭업이 상한 초과(`max(O,P) > cap`): 장중 저가가 상한까지 내려오면(`L ≤ cap`)
      `cap` 체결, 아니면 추격 한도 초과로 **미체결**(None) — EventList 기록 대상.

2·3차 피라미딩(`fill_pyramid`, LIMIT_BUY):
    - `H < P` → 트리거 미도달, 미체결(None).
    - `fill = max(O, P)`. 갭이 상한 초과(`fill > cap`, 즉 `O > cap`)면 그 회차 **스킵**
      (규칙서 §4: "다음날 갭이 상한을 넘어 있으면 그 회차는 건너뛴다"). 1차와 달리
      장중 복귀 체결을 허용하지 않는다.

거래량 게이트(`volume_confirmed`)는 돌파일 종가 확정 거래량이 20일 평균의 1.5배 이상
인지 본다 — 2·3차 감시주문 예약 여부 판단용(§6.2). 1차는 이미 체결됐으므로 게이트와
무관하다. 게이트 판정 자체는 엔진(파이프라인)이 소비한다.

청산(`fill_exit`, Phase 4B, MARKET_SELL):
    - 기본(종가확정): 판정 다음날(D+1) 바를 받아 **시가 전량** 체결. 갭 여부 무관(§6.2).
    - 손절 장중스탑(대안 `intraday_touch`): 판정일 D 바를 받아 `min(O, 손절가)` 체결
      — 갭하락(`O < 손절가`)이면 시가, 아니면 손절가. 손절(STOP) 사유에만 적용된다.
    엔진이 어느 날 바를 넘길지 결정하고, 이 모델은 그 바에서 체결가만 만든다.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Protocol

import pandas as pd

from ..domain.config import Config
from ..domain.enums import ExitReason, FillModelType, Market, OrderKind
from ..domain.trade import Fill
from .cost_model import CostModel
from .orders import Order


class FillModel(Protocol):
    """체결 계약. 진입/피라미딩(4A) + 청산(4B)."""

    def fill_entry(self, bar: pd.Series, order: Order) -> Fill | None: ...
    def fill_pyramid(self, bar: pd.Series, order: Order) -> Fill | None: ...
    def fill_exit(self, bar: pd.Series, order: Order) -> Fill: ...


def _bar_date(bar: pd.Series) -> date:
    """바 Series의 인덱스 이름(Timestamp)에서 날짜를 뽑는다."""
    name = bar.name
    if isinstance(name, pd.Timestamp):
        return name.date()
    return pd.Timestamp(name).date()


class DailyBarFillModel:
    def __init__(self, cost: CostModel, cfg: Config) -> None:
        self.cost = cost
        self.fcfg = cfg.fill

    # ------------------------------------------------------------------ #
    # 진입 체결
    # ------------------------------------------------------------------ #
    def fill_entry(self, bar: pd.Series, order: Order) -> Fill | None:
        pivot = order.trigger
        cap = order.limit_cap
        if pivot is None or cap is None:
            raise ValueError("entry order requires trigger(pivot) and limit_cap")
        o = float(bar["open"])
        h = float(bar["high"])
        low = float(bar["low"])

        if h < pivot:
            return None  # 장중 피벗 미도달 → 돌파 없음
        price = max(o, pivot)
        if price > cap:
            # 갭업이 추격 상한 초과. 장중 저가가 상한까지 내려오면 상한 체결.
            if low <= cap:
                price = cap
            else:
                return None  # 추격 한도 초과 → 미체결
        return self._buy_fill(bar, price, order)

    # ------------------------------------------------------------------ #
    # 피라미딩 체결
    # ------------------------------------------------------------------ #
    def fill_pyramid(self, bar: pd.Series, order: Order) -> Fill | None:
        trigger = order.trigger
        cap = order.limit_cap
        if trigger is None or cap is None:
            raise ValueError("pyramid order requires trigger and limit_cap")
        o = float(bar["open"])
        h = float(bar["high"])

        if h < trigger:
            return None  # 트리거 미도달
        price = max(o, trigger)
        if price > cap:
            return None  # 갭이 상한 초과 → 그 회차 스킵
        return self._buy_fill(bar, price, order)

    # ------------------------------------------------------------------ #
    # 청산 체결 (손절·60MA·시장방어)
    # ------------------------------------------------------------------ #
    def fill_exit(self, bar: pd.Series, order: Order) -> Fill:
        """청산 시장가 매도 체결. 세금은 시장(order.market)·매도일 기준."""
        if order.kind is not OrderKind.MARKET_SELL:
            raise ValueError("fill_exit requires a MARKET_SELL order")
        o = float(bar["open"])
        if (
            order.reason is ExitReason.STOP
            and self.fcfg.stop_fill_model is FillModelType.INTRADAY_TOUCH
        ):
            # 장중 자동스탑: 저가가 손절가에 닿으면 손절가 체결, 갭하락이면 시가.
            stop = order.trigger
            price = min(o, stop) if stop is not None else o
        else:
            # 종가확정(기본) 및 60MA·시장방어: 다음날(D+1) 시가 전량.
            price = o
        d = _bar_date(bar)
        market = order.market if order.market is not None else Market.KOSPI
        cost = self.cost.sell_cost(price, order.qty, d, market)
        return Fill(date=d, price=price, qty=order.qty, reason=order.reason, cost=cost)

    # ------------------------------------------------------------------ #
    # 거래량 게이트 (2·3차 예약 여부)
    # ------------------------------------------------------------------ #
    def volume_confirmed(self, breakout_volume: float, vol_ma20: float | None) -> bool:
        """돌파일 거래량 ≥ 20일 평균 × 배수(기본 1.5)인가."""
        if vol_ma20 is None or math.isnan(vol_ma20) or vol_ma20 <= 0:
            return False
        return breakout_volume >= vol_ma20 * self.fcfg.breakout_volume_mult

    # ------------------------------------------------------------------ #
    def _buy_fill(self, bar: pd.Series, price: float, order: Order) -> Fill:
        d = _bar_date(bar)
        cost = self.cost.buy_cost(price, order.qty, d)
        return Fill(date=d, price=price, qty=order.qty, reason=order.reason, cost=cost)
