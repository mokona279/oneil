"""베이스 단계 카운트 — 크로스-베이스 상태머신 (규칙서 §5 단계, 계획서 §3.4, Phase 3A).

한 심볼의 연속된 유효 베이스에 걸쳐 단계(stage)를 센다.

    1. 유효 돌파 후 종가 기준 +20% 이상 상승한 뒤 형성되는 베이스 = 단계 +1.
    2. +20% 미달 상태의 재베이스(베이스 온 베이스) = 단계 유지.
    3. 직전 베이스 최저 저가 하회 시 카운트 1로 리셋.
    4. (R3b, 개선계획 Q5b) 마지막 유효 돌파 후 `reset_no_breakout_months`개월 이상
       무돌파 상태에서 형성된 깊이 `reset_min_depth_pct`% 이상의 새 베이스 = 카운트 1
       ("새 사이클" 판정). config가 None이면 꺼짐(현행 동치).
    5. 1~3단계 진입 허용, 4단계 이상 신규 진입 금지/감액(진입 게이트는 엔진 몫; 여기선
       순수 카운트만 하며 4 이상도 그대로 센다).

계약(§3.4의 `stage_for`/`reset_check` pseudocode 대비): 단계는 직전 유효돌파가·직전
베이스 저점·돌파 후 최고 종가에 의존하는 경로 의존 상태다. Phase 2의 시장필터와
같은 사유로, 심볼 세션을 한 번 전방 스캔하며 상태를 갱신하는 형태로 구현한다.
`BaseDetector`가 스캔 중 세 훅을 호출한다:

    on_bar(close, low)             — 매 세션. 마지막 돌파 이후 최고 종가·저점 하회를 추적.
    stage_for_new_base(d, depth)   — 베이스 후보가 갱신될 때 그 베이스의 단계를 산출.
                                     d·depth는 R3b 리셋 판정용(그 세션까지 확정된 값).
    on_breakout(d, close, ...)     — 유효 돌파 발생 시 단계·기준값·돌파일을 확정.
"""

from __future__ import annotations

import math
from datetime import date

from ..domain.config import Config

# 개월→달력일 환산 상수(평균 그레고리력 월). R3b의 "N개월 무돌파"는 스윕 축이 6개월
# 단위라 환산 오차(±1일)가 판정을 가르지 않는다 — 재현성 위해 round로 고정.
_DAYS_PER_MONTH = 365.25 / 12.0


class StageTracker:
    def __init__(self, cfg: Config, *, disable_reset: bool = False) -> None:
        """disable_reset=True면 R3b 리셋을 배제한 순수 카운트 — 감지기가 §3.3 저표본
        추적(리셋 경유 진입 판별)용 반사실 단계를 병렬 산출할 때 쓰는 섀도 모드다.
        베이스 구조는 가격만으로 정해지므로 두 트래커의 베이스·돌파 시퀀스는 동일하고
        단계 라벨만 갈린다."""
        scfg = cfg.base.stage
        self._step_up_pct = scfg.step_up_close_gain_pct
        reset_months = None if disable_reset else scfg.reset_no_breakout_months
        self._reset_days: int | None = (
            None if reset_months is None else round(reset_months * _DAYS_PER_MONTH)
        )
        self._reset_min_depth = scfg.reset_min_depth_pct
        self.stage = 0                              # 마지막 유효 돌파의 단계 (0=아직 없음)
        self._last_breakout_close: float | None = None
        self._last_breakout_date: date | None = None
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

    def stage_for_new_base(self, d: date, depth_pct: float) -> int:
        """현 베이스 후보의 단계. d는 판정 세션, depth_pct는 그 세션까지의 베이스 깊이.

        - 첫 베이스(직전 유효 돌파 없음) → 1.
        - 직전 베이스 저점 하회 이력 → 1로 리셋.
        - (R3b) 마지막 돌파 후 N개월+ 무돌파 & 깊이 ≥ 임계 → 1로 리셋(새 사이클).
          매 세션 재판정되는 순수 함수라, 이후 베이스가 재시작돼(얕아져) 조건을 벗어나면
          기존 카운트로 되돌아간다 — 리셋은 그 깊은 베이스가 실제 돌파될 때 확정된다.
        - 마지막 돌파 이후 최고 종가가 +step_up% 이상 → 단계 +1.
        - 그 외(미달 재베이스) → 단계 유지.
        """
        if self._last_breakout_close is None:
            return 1
        if self._undercut:
            return 1
        if (
            self._reset_days is not None
            and self._last_breakout_date is not None
            and (d - self._last_breakout_date).days >= self._reset_days
            and depth_pct >= self._reset_min_depth  # type: ignore[operator]
        ):
            return 1
        gain_pct = (self._peak_close / self._last_breakout_close - 1.0) * 100.0
        if gain_pct >= self._step_up_pct:
            return self.stage + 1
        return self.stage

    def on_breakout(self, d: date, close: float, base_low: float, stage: int) -> None:
        """유효 돌파 확정. 다음 베이스 판정을 위한 기준을 이 돌파로 갱신한다."""
        self.stage = stage
        self._last_breakout_close = close
        self._last_breakout_date = d
        self._prev_base_low = base_low
        self._peak_close = close
        self._undercut = False
