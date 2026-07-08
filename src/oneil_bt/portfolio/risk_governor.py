"""리스크 거버너 — 연속손절 쿨다운 (규칙서 §7 생존수칙, 계획서 §3.5·§12 Q12, Phase 5).

규칙서 §7: 손절이 **연속으로** `consecutive_stops`회(기본 3) 나오면 시장과 나 사이가
어긋난 신호로 보고 `halt_days`(기본 10거래일 = 2주) 동안 신규 진입을 멈춘다. config
`risk_governor.enabled`로 켜고 끈다(§12 Q12 — v1 기본 on).

"연속"은 포트폴리오 전체 청산 순서 기준이다: 손절 청산이면 카운터 +1, 손절이 아닌
청산(추세이탈·방어·이익실현 등)이 하나라도 끼면 카운터를 0으로 리셋한다. 카운터가
임계에 닿으면 청산일 기준 `halt_days` 거래일 뒤까지 차단하고 카운터를 리셋한다.

차단은 **신규 진입만** 막는다 — 보유 포지션의 피라미딩·청산에는 관여하지 않는다.
거래일 이동은 TradingCalendar로 계산한다(주입 없으면 달력일로 근사).
"""

from __future__ import annotations

from datetime import date, timedelta

from ..data.calendar import TradingCalendar
from ..domain.config import Config
from ..domain.trade import ClosedTrade


class RiskGovernor:
    def __init__(self, cfg: Config, calendar: TradingCalendar | None = None) -> None:
        self.rgcfg = cfg.risk_governor
        self.calendar = calendar
        self._consecutive = 0
        self._halt_until: date | None = None

    @property
    def consecutive_stops(self) -> int:
        return self._consecutive

    @property
    def halt_until(self) -> date | None:
        return self._halt_until

    def record_exit(self, trade: ClosedTrade) -> None:
        """청산 1건을 반영. 손절이면 연속 카운트, 아니면 리셋."""
        if not self.rgcfg.enabled:
            return
        if trade.is_stop:
            self._consecutive += 1
            if self._consecutive >= self.rgcfg.consecutive_stops:
                self._halt_until = self._halt_start(trade.exit_fill.date)
                self._consecutive = 0
        else:
            self._consecutive = 0

    def new_trades_blocked(self, d: date) -> bool:
        """d에 신규 진입이 막혀 있는가. 쿨다운 종료일에 도달하면 자동 해제."""
        if not self.rgcfg.enabled or self._halt_until is None:
            return False
        if d >= self._halt_until:
            self._halt_until = None
            return False
        return True

    def _halt_start(self, exit_date: date) -> date | None:
        """차단 종료일 = 청산일 + halt_days 거래일 (캘린더 없으면 달력일 근사)."""
        if self.calendar is not None:
            end = self.calendar.shift(exit_date, self.rgcfg.halt_days)
            if end is not None:
                return end
        return exit_date + timedelta(days=self.rgcfg.halt_days)
