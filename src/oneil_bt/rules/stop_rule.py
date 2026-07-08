"""손절 규칙 — 2×ATR / -10% 캡, 평단 갱신 재계산 (규칙서 §6①, 계획서 §3.4, Phase 4B).

규칙서 §6①:
    손절가 = 평균 매수가 − 2×ATR(14), 단 손절폭은 **최대 -10%**까지만.
    종가로 손절가 도달 → 다음 날 전량 매도(기본). 예외 없음.

- `stop_price(avg, atr)`: 손절가 산출. 2×ATR가 평단의 10%를 넘으면(변동성 과대)
  캡이 걸려 -10% 바닥으로 클램프된다. 피라미딩으로 평단이 오르면 엔진이 이 값을
  다시 계산해 `Position.stop_price`를 갱신한다(규칙서 §4 "평단 −2×ATR 재계산").
  방식은 config `stop.method`로 atr2x(기본)·고정%(대안, Q14)를 선택한다.

- `hit(pos, d)`: 손절 발동 판정. 체결 시점 모델(§12 Q1)에 따라 입력이 다르다.
    · close_confirmed_next_open(기본): **D 종가** ≤ 손절가 (체결은 엔진이 D+1 시가).
    · intraday_touch(대안): **D 장중 저가** ≤ 손절가 (체결은 D 장중, min(O,stop)).
  판정은 실거래 바가 있는 날만 수행한다(§6.1 결측 처리) — 없으면 False.
"""

from __future__ import annotations

from datetime import date

from ..domain.bar import PriceFrame
from ..domain.config import Config
from ..domain.enums import FillModelType, StopMethod
from ..domain.trade import Position


class StopRule:
    def __init__(self, prices: PriceFrame, cfg: Config) -> None:
        self.prices = prices
        self.scfg = cfg.stop

    def stop_price(self, avg_price: float, atr: float) -> float:
        """손절가 = max(평단−2×ATR, 평단×(1−10%)). 손절폭 -10% 캡이 바닥을 만든다."""
        if self.scfg.method is StopMethod.ATR2X:
            raw = avg_price - self.scfg.atr_mult * atr
        else:  # FIXED_PCT — v1 대안(고정 -7~8%)
            raw = avg_price * (1.0 - self.scfg.fixed_pct / 100.0)
        floor = avg_price * (1.0 - self.scfg.max_stop_pct / 100.0)
        return max(raw, floor)

    def hit(self, pos: Position, d: date) -> bool:
        """d에 손절이 발동했는가. 체결 모델별로 종가/장중 저가를 본다."""
        row = self.prices.row(d)
        if row is None:
            return False  # 실거래 바 없음(거래정지 등) → 종가 기반 판정 보류
        if self.scfg.fill_model is FillModelType.INTRADAY_TOUCH:
            return float(row["low"]) <= pos.stop_price
        return float(row["close"]) <= pos.stop_price
