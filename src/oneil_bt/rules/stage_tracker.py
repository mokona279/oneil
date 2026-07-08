"""베이스 단계 카운트 — 크로스-베이스 상태머신 (규칙서 §5 단계, 계획서 §3.4, Phase 3A).

한 심볼의 연속된 유효 베이스에 걸쳐 단계(stage)를 센다.

    1. 유효 돌파 후 종가 기준 +20% 이상 상승한 뒤 형성되는 베이스 = 단계 +1.
    2. +20% 미달 상태의 재베이스(베이스 온 베이스) = 단계 유지.
    3. 직전 베이스 최저 저가 하회 시 카운트 1로 리셋.
    4. 1~3단계 진입 허용, 4단계 이상 신규 진입 금지(진입 게이트는 엔진 몫; 여기선 순수
       카운트만 하며 4 이상도 그대로 센다).

계약(§3.4의 `stage_for`/`reset_check` pseudocode 대비): 단계는 직전 유효돌파가·직전
베이스 저점·돌파 후 최고 종가에 의존하는 경로 의존 상태다. Phase 2의 시장필터와
같은 사유로, 심볼 세션을 한 번 전방 스캔하며 상태를 갱신하는 형태로 구현한다.
`BaseDetector`가 스캔 중 세 훅을 호출한다:

    on_bar(close, low)        — 매 세션. 마지막 돌파 이후 최고 종가·저점 하회를 추적.
    stage_for_new_base()      — 새 베이스가 무장될 때 그 베이스의 단계를 산출.
    on_breakout(close, ...)   — 유효 돌파 발생 시 단계·기준값을 확정.
"""

from __future__ import annotations

import math

from ..domain.config import Config


class StageTracker:
    def __init__(self, cfg: Config) -> None:
        self._step_up_pct = cfg.base.stage.step_up_close_gain_pct
        self.stage = 0                              # 마지막 유효 돌파의 단계 (0=아직 없음)
        self._last_breakout_close: float | None = None
        self._prev_base_low: float | None = None    # 직전 유효 베이스 저점(하회=리셋 기준)
        self._peak_close: float = -math.inf         # 마지막 돌파 이후 최고 종가
        self._undercut = False                      # 마지막 돌파 이후 직전 베이스 저점 하회

    def on_bar(self, close: float, low: float) -> None:
        """매 세션 호출. 마지막 유효 돌파 이후의 최고 종가와 저점 하회를 갱신."""
        if self._last_breakout_close is None:
            return
        if close > self._peak_close:
            self._peak_close = close
        if self._prev_base_low is not None and low < self._prev_base_low:
            self._undercut = True

    def stage_for_new_base(self) -> int:
        """새로 무장된 베이스의 단계.

        - 첫 베이스(직전 유효 돌파 없음) → 1.
        - 직전 베이스 저점 하회 이력 → 1로 리셋.
        - 마지막 돌파 이후 최고 종가가 +step_up% 이상 → 단계 +1.
        - 그 외(미달 재베이스) → 단계 유지.
        """
        if self._last_breakout_close is None:
            return 1
        if self._undercut:
            return 1
        gain_pct = (self._peak_close / self._last_breakout_close - 1.0) * 100.0
        if gain_pct >= self._step_up_pct:
            return self.stage + 1
        return self.stage

    def on_breakout(self, close: float, base_low: float, stage: int) -> None:
        """유효 돌파 확정. 다음 베이스 판정을 위한 기준을 이 돌파로 갱신한다."""
        self.stage = stage
        self._last_breakout_close = close
        self._prev_base_low = base_low
        self._peak_close = close
        self._undercut = False
