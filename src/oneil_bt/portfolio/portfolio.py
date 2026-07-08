"""포트폴리오 — 현금·포지션·슬롯의 단일 소유자 (규칙서 §1/§3.10, 계획서 §3.5, Phase 5).

계좌의 모든 자금 회계를 한 곳에서 소유한다: 현금, 보유 포지션(심볼→Position),
그리고 피라미딩(2·3차) 예약 현금. 체결(Fill)을 받아 현금·포지션을 갱신하고,
평가금액·자본·슬롯 여유를 계산한다. 회계 항등식 **자본 = 현금 + 평가금액**을 유지한다.

슬롯·현금 제약(§6.3):
    - `max_positions`(8) 슬롯 상한. `has_slot`이 신규 진입 여부를 가른다.
    - `reserve_pyramid_cash`가 켜져 있으면 1차 진입 시 2·3차 몫을 예약해 두어,
      가용현금(`available_cash`)에서 빼 신규·타종목이 그 현금을 다시 못 쓰게 한다.
      2·3차 체결/청산 때 예약을 `release`한다.

체결 반영은 매수/매도로 나뉜다. `Fill`은 심볼·시장을 담지 않으므로(§3.1) 엔진이
심볼·시장을 함께 넘긴다. 손절가 재계산은 StopRule 소유라 갱신값을 인자로 받는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ..domain.config import Config
from ..domain.enums import Market
from ..domain.trade import Fill, Position


class Portfolio:
    def __init__(self, initial_cash: float, cfg: Config) -> None:
        if initial_cash < 0:
            raise ValueError("initial_cash must be non-negative")
        self.cash = float(initial_cash)
        self.positions: dict[str, Position] = {}
        self.pcfg = cfg.portfolio
        self.scfg = cfg.sizing
        self._reserved: dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # 조회 — 슬롯·현금·평가
    # ------------------------------------------------------------------ #
    @property
    def n_positions(self) -> int:
        return len(self.positions)

    @property
    def reserved_cash(self) -> float:
        """예약 피라미딩 현금 합계 (토글 off면 0)."""
        if not self.scfg.reserve_pyramid_cash:
            return 0.0
        return sum(self._reserved.values())

    @property
    def available_cash(self) -> float:
        """신규·타종목이 쓸 수 있는 현금 = 현금 − 예약."""
        return self.cash - self.reserved_cash

    def has_slot(self) -> bool:
        return self.n_positions < self.pcfg.max_positions

    def can_open(self, needed_cash: float) -> bool:
        """신규 진입 가능? — 슬롯 여유 AND 가용현금 충분 (§6.3)."""
        return self.has_slot() and self.available_cash >= needed_cash

    def holdings_value(self, marks: Mapping[str, float]) -> float:
        """보유 평가금액. 마크가 없는 심볼은 평단으로 평가(마지막 원가 근사)."""
        total = 0.0
        for sym, pos in self.positions.items():
            mark = marks.get(sym, pos.avg_price)
            total += float(mark) * pos.qty
        return total

    def equity(self, marks: Mapping[str, float]) -> float:
        """자본 = 현금 + 평가금액 (회계 항등식)."""
        return self.cash + self.holdings_value(marks)

    # ------------------------------------------------------------------ #
    # 예약 현금 (피라미딩 2·3차)
    # ------------------------------------------------------------------ #
    def reserve(self, symbol: str, amount: float) -> None:
        if amount <= 0:
            return
        self._reserved[symbol] = self._reserved.get(symbol, 0.0) + amount

    def release(self, symbol: str, amount: float | None = None) -> None:
        """예약 해제. amount 생략 시 해당 심볼 전액 해제."""
        if amount is None:
            self._reserved.pop(symbol, None)
            return
        remaining = self._reserved.get(symbol, 0.0) - amount
        if remaining <= 1e-9:
            self._reserved.pop(symbol, None)
        else:
            self._reserved[symbol] = remaining

    # ------------------------------------------------------------------ #
    # 체결 반영
    # ------------------------------------------------------------------ #
    def apply_buy(
        self, symbol: str, market: Market, fill: Fill, stop_price: float
    ) -> Position:
        """매수 체결 반영. 신규면 포지션 생성, 기존이면 평단·수량 갱신(피라미딩)."""
        if fill.qty <= 0:
            raise ValueError("buy fill qty must be positive")
        self.cash -= fill.cash_out
        existing = self.positions.get(symbol)
        if existing is None:
            pos = Position(
                symbol=symbol,
                market=market,
                entry_date=fill.date,
                entry_price=fill.price,
                avg_price=fill.price,
                qty=fill.qty,
                stop_price=stop_price,
            )
        else:
            new_qty = existing.qty + fill.qty
            new_avg = (
                existing.avg_price * existing.qty + fill.price * fill.qty
            ) / new_qty
            pos = replace(existing, avg_price=new_avg, qty=new_qty, stop_price=stop_price)
        self.positions[symbol] = pos
        return pos

    def apply_sell(self, symbol: str, fill: Fill) -> Position | None:
        """매도 체결 반영. 전량이면 포지션·예약 제거하고 None 반환."""
        pos = self.positions.get(symbol)
        if pos is None:
            raise KeyError(f"no position to sell: {symbol}")
        if fill.qty <= 0:
            raise ValueError("sell fill qty must be positive")
        if fill.qty > pos.qty:
            raise ValueError(f"sell qty {fill.qty} exceeds holding {pos.qty}")
        self.cash += fill.cash_in
        new_qty = pos.qty - fill.qty
        if new_qty <= 0:
            del self.positions[symbol]
            self.release(symbol)  # 남은 예약도 정리
            return None
        updated = replace(pos, qty=new_qty)
        self.positions[symbol] = updated
        return updated
