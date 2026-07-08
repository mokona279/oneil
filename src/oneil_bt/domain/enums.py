"""도메인 열거형 (계획서 §3.1).

StrEnum을 사용해 값이 곧 문자열이 되도록 한다 (config·CSV 직렬화 친화적).
"""

from __future__ import annotations

from enum import StrEnum


class Market(StrEnum):
    """상장 시장. RS 벤치마크·시장필터 매칭에 사용 (계획서 §4.2)."""

    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"


class EntryReason(StrEnum):
    """진입 체결 사유 (트랜치별)."""

    BREAKOUT_T1 = "BREAKOUT_T1"   # 1차 돌파 진입
    PYRAMID_T2 = "PYRAMID_T2"     # 2차 피라미딩
    PYRAMID_T3 = "PYRAMID_T3"     # 3차 피라미딩


class ExitReason(StrEnum):
    """청산 체결 사유."""

    STOP = "STOP"                             # 손절 (§6①)
    TREND_60MA_HALF = "TREND_60MA_HALF"       # 60MA 이탈 → 절반 (§6②)
    TREND_60MA_REST = "TREND_60MA_REST"       # 60MA 회복 실패 → 잔량 (§6②)
    TREND_60MA_VOLBREAK = "TREND_60MA_VOLBREAK"   # 거래량 급증 이탈 → 전량 (§6② 보조)
    MARKET_DEFENSE_120MA = "MARKET_DEFENSE_120MA"  # 지수 120MA 방어 절반 (§6③)


class OrderKind(StrEnum):
    """주문 유형 (계획서 §3.1, §6.2)."""

    STOP_BUY = "STOP_BUY"        # 자동감시(스탑) 매수 — 돌파 1차
    LIMIT_BUY = "LIMIT_BUY"      # 지정가 매수 — 피라미딩 2·3차
    MARKET_SELL = "MARKET_SELL"  # 시장가 매도 — 청산 (Phase 4B)


class MarketState(StrEnum):
    """시장 필터 상태 (규칙서 §2, 계획서 §3.4).

    정상: 지수 > 60일선 → 신규 매수 가능
    경계: 지수 < 60일선 → 신규 매수 중단 (60일선 위 3거래일 유지 전까지)
    방어: 지수 < 120일선 → 신규 금지 + 주식 비중 50% 이하
    """

    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DEFENSE = "DEFENSE"


class FillModelType(StrEnum):
    """손절 체결 시점 모델 (계획서 §12 Q1 — 결과에 큰 영향)."""

    CLOSE_CONFIRMED_NEXT_OPEN = "close_confirmed_next_open"  # 종가 확정 → 다음날 시가 (기본)
    INTRADAY_TOUCH = "intraday_touch"                        # 장중 터치 자동 스탑 (대안)


class StopMethod(StrEnum):
    """손절 산정 방식 (계획서 §12 Q14)."""

    ATR2X = "atr2x"          # avg - 2*ATR, -10% 캡 (기본)
    FIXED_PCT = "fixed_pct"  # 고정 -7~8% (대안)
