"""시장 필터 — 정상/경계/방어 상태머신 (규칙서 §2, 계획서 §3.4, §6.1).

종목이 속한 시장의 지수 종가로 판단한다(코스피/코스닥 각각). 상태:

    정상(NORMAL): 지수 > 60일선 → 신규 매수 가능
    경계(CAUTION): 지수 < 60일선 → 신규 매수 중단 (보유분은 개별 매도 규칙대로)
    방어(DEFENSE): 지수 < 120일선 → 신규 금지 + 주식 비중 50% 이하 축소

복귀 규칙: 지수가 60일선 위로 올라와 **3거래일 유지**해야 정상으로 복귀한다. 이
히스테리시스 때문에 상태는 경로 의존적이라, 생성 시 지수 세션 시리즈 전체에 대해
상태를 한 번에 계산해두고 as-of 조회한다.

계산 규약(결정론):
- 지수 종가 < 120일선 → DEFENSE (60일선 위 연속 카운트 리셋)
- 지수 종가 < 60일선 (그러나 ≥ 120일선) → CAUTION (카운트 리셋)
- 지수 종가 ≥ 60일선 AND ≥ 120일선 → 연속 카운트 +1;
      카운트 ≥ recover_days 면 NORMAL, 아니면 CAUTION(복귀 대기)
- 워밍업(60/120일선 미확정)은 판정 불가 → 보수적으로 DEFENSE (카운트 리셋).
  (진입은 워밍업 이후에만 발생하므로 실거래 구간에 영향 없음)

타이밍(§6.1):
- state_asof(d): d 종가 기준 상태.
- new_entry_allowed(d): 진입은 D 장중에 결정하므로 D 종가·지수가 아직 없다 →
  직전 거래일(D-1) 상태가 NORMAL일 때만 허용.
- defense_triggered_on(d): d에 새로 120일선을 이탈(직전 세션은 DEFENSE 아님)한
  발생일이면 True. MarketDefenseRule(Phase 4B)이 D+1 체결에 사용한다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..domain.bar import PriceFrame
from ..domain.config import Config
from ..domain.enums import MarketState
from ..indicators.indicator_set import IndicatorSet


def build_market_states(
    close: pd.Series,
    entry_ma: pd.Series,
    defense_ma: pd.Series,
    recover_days: int,
) -> pd.Series:
    """지수 세션별 시장 상태를 계산한다 (복귀 3거래일 히스테리시스 포함).

    입력 세 시리즈는 같은 날짜 인덱스로 정렬되어 있어야 한다. 결과는 close.index에
    맞춘 MarketState 시리즈다. 계산 규약은 모듈 docstring 참조.
    """
    states: list[MarketState] = []
    streak = 0
    for c, m_entry, m_def in zip(close, entry_ma, defense_ma):
        if pd.isna(m_entry) or pd.isna(m_def):
            streak = 0
            states.append(MarketState.DEFENSE)
        elif c < m_def:
            streak = 0
            states.append(MarketState.DEFENSE)
        elif c < m_entry:
            streak = 0
            states.append(MarketState.CAUTION)
        else:
            streak += 1
            states.append(
                MarketState.NORMAL if streak >= recover_days else MarketState.CAUTION
            )
    return pd.Series(states, index=close.index, dtype=object)


class MarketFilter:
    def __init__(
        self, index_prices: PriceFrame, ind: IndicatorSet, cfg: Config
    ) -> None:
        self.mcfg = cfg.market_filter
        self._index = index_prices.df.index

        ma_by_period = {
            50: ind.ma50,
            60: ind.ma60,
            120: ind.ma120,
            150: ind.ma150,
            200: ind.ma200,
        }
        entry_ma = self._select_ma(ma_by_period, self.mcfg.entry_ma)
        defense_ma = self._select_ma(ma_by_period, self.mcfg.defense_ma)
        close = index_prices.df["close"]
        self._state = build_market_states(
            close, entry_ma, defense_ma, self.mcfg.recover_days
        )

    @staticmethod
    def _select_ma(ma_by_period: dict[int, pd.Series], period: int) -> pd.Series:
        if period not in ma_by_period:
            raise KeyError(
                f"market_filter needs index MA{period} but it is not precomputed"
            )
        return ma_by_period[period]

    def _state_at_pos(self, pos: int) -> MarketState:
        if pos < 0:
            return MarketState.DEFENSE
        return self._state.iloc[pos]

    def state_asof(self, d: date) -> MarketState:
        """d 종가 기준(≤d 최근 세션) 시장 상태."""
        ts = pd.Timestamp(d).normalize()
        pos = self._index.searchsorted(ts, side="right") - 1
        return self._state_at_pos(pos)

    def _prev_pos(self, d: date) -> int:
        """d 직전(strictly <d) 세션의 위치. 없으면 -1."""
        ts = pd.Timestamp(d).normalize()
        return self._index.searchsorted(ts, side="left") - 1

    def new_entry_allowed(self, d: date) -> bool:
        """D 진입 판정용 — 직전 거래일(D-1) 상태가 NORMAL일 때만 허용."""
        return self._state_at_pos(self._prev_pos(d)) == MarketState.NORMAL

    def defense_triggered_on(self, d: date) -> bool:
        """d가 새로 120일선을 이탈한 방어 발생일이면 True."""
        if self.state_asof(d) != MarketState.DEFENSE:
            return False
        return self._state_at_pos(self._prev_pos(d)) != MarketState.DEFENSE
