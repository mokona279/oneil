"""캡처 회귀 세트 — 배수 달성 판정·창 경계·유동성 플래그 (개선계획 §3.3, Q8).

합성 종가 시리즈로 순수 판정 로직만 검증한다. 실데이터 규모 검수(하이닉스·삼성 포함
여부, 세트 크기)는 P0 실행 단계에서 별도로 수행한다.
"""

from __future__ import annotations

from datetime import date

from oneil_bt.analysis import CaptureCriteria, build_capture_set, capture_record
from oneil_bt.domain.bar import PriceFrame
from tests.fixtures.synthetic import business_dates, ohlcv_frame

# 짧은 합성 시리즈용 축소 임계: 10세션 내 2.0×, 3일 평균 거래대금 ≥ 1,000.
CRIT = CaptureCriteria(
    lookback_sessions=10, multiple=2.0, turnover_window=3, min_turnover=1_000.0
)


def _pf(closes: list[float], values: list[float] | None = None) -> PriceFrame:
    dates = business_dates("2020-01-01", len(closes))
    if values is None:
        values = [10_000.0] * len(closes)
    return PriceFrame("TEST", ohlcv_frame(dates, closes, values=values))


def _win(pf: PriceFrame) -> tuple[date, date]:
    return pf.dates[0].date(), pf.dates[-1].date()


# --------------------------------------------------------------------------- #
# 달성 판정
# --------------------------------------------------------------------------- #
def test_doubling_within_lookback_is_captured() -> None:
    closes = [100.0] * 5 + [150.0, 210.0] + [200.0] * 3  # 7번째 세션에 2.1×
    pf = _pf(closes)
    start, end = _win(pf)
    rec = capture_record(pf, start, end, CRIT)
    assert rec is not None
    assert rec.first_achieved == pf.dates[6].date()
    assert rec.max_multiple >= 2.1
    assert rec.turnover_ok is True
    assert rec.sessions == len(closes)


def test_no_doubling_returns_none() -> None:
    pf = _pf([100.0 + i for i in range(12)])  # 최대 1.11×
    start, end = _win(pf)
    assert capture_record(pf, start, end, CRIT) is None


def test_slow_rise_beyond_lookback_not_captured() -> None:
    # 20세션에 걸쳐 2배 — 롤링 최소(10세션)가 따라와 구간 배수는 2.0× 미달.
    closes = [100.0 * (1.036**i) for i in range(20)]  # 10세션 배수 ≈ 1.42×
    pf = _pf(closes)
    start, end = _win(pf)
    assert capture_record(pf, start, end, CRIT) is None


# --------------------------------------------------------------------------- #
# 창 경계 — 창 밖 달성 이력은 제외
# --------------------------------------------------------------------------- #
def test_achievement_before_window_excluded() -> None:
    # 앞 5세션에 2배 달성 후 12세션 횡보. 창 시작을 롤링 최소(10세션)에서 저점이
    # 빠져나간 뒤로 잡으면 창 내 비율이 2.0× 미만 → 과거 달성 이력은 제외된다.
    closes = [100.0, 120.0, 160.0, 210.0, 220.0] + [215.0] * 12
    pf = _pf(closes)
    start = pf.dates[10].date()  # 세션 10: 롤링 창 = 세션 1~10, 최소 120 → 1.79×
    rec = capture_record(pf, start, pf.dates[-1].date(), CRIT)
    assert rec is None


# --------------------------------------------------------------------------- #
# 유동성 플래그
# --------------------------------------------------------------------------- #
def test_low_turnover_sets_flag_false() -> None:
    closes = [100.0] * 5 + [150.0, 210.0] + [200.0] * 3
    values = [100.0] * len(closes)  # 3일 평균 100 < 1,000
    pf = _pf(closes, values)
    start, end = _win(pf)
    rec = capture_record(pf, start, end, CRIT)
    assert rec is not None
    assert rec.turnover_ok is False


# --------------------------------------------------------------------------- #
# 세트 수집
# --------------------------------------------------------------------------- #
def test_build_capture_set_collects_and_sorts() -> None:
    hit = PriceFrame(
        "B_HIT", ohlcv_frame(business_dates("2020-01-01", 10),
                             [100.0] * 5 + [210.0] * 5,
                             values=[10_000.0] * 10)
    )
    miss = PriceFrame(
        "A_MISS", ohlcv_frame(business_dates("2020-01-01", 10), [100.0] * 10,
                              values=[10_000.0] * 10)
    )
    thin = PriceFrame(
        "C_THIN", ohlcv_frame(business_dates("2020-01-01", 10),
                              [100.0] * 5 + [210.0] * 5,
                              values=[10.0] * 10)
    )
    df = build_capture_set([hit, miss, thin], date(2020, 1, 1), date(2020, 1, 20), CRIT)
    assert list(df.columns) == [
        "symbol", "first_achieved", "max_multiple", "turnover_ok", "sessions"
    ]
    assert df["symbol"].tolist() == ["B_HIT", "C_THIN"]  # 미달성 제외, 심볼 정렬
    assert df.set_index("symbol")["turnover_ok"].to_dict() == {
        "B_HIT": True, "C_THIN": False
    }
