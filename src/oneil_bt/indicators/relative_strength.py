"""상대강도(RS) — 6개월 종목수익 vs 지수수익 (계획서 §3.3, §12 Q5).

정의(Q5 제안 기본값): `method="return_diff"`, `lookback_days=126`.
    rs_6m[D] = (종목 126일 수익률) - (지수 126일 수익률)
RsFilter는 rs_6m > 0 을 통과 조건으로 쓴다(미너비니 RS70의 불리언 대체).

지수 수익률은 지수 자체 시계열에서 계산한 뒤 종목 날짜에 정렬한다. 종목이
지수 캘린더의 부분집합이라는 전제(거래일=지수 CSV)에서 정확히 대응된다.
"""

from __future__ import annotations

import pandas as pd

from ..domain.bar import PriceFrame

METHOD_RETURN_DIFF = "return_diff"


class RelativeStrength:
    def __init__(self, lookback_days: int = 126, method: str = METHOD_RETURN_DIFF) -> None:
        if lookback_days <= 0:
            raise ValueError("lookback_days must be positive")
        if method != METHOD_RETURN_DIFF:
            raise ValueError(f"unsupported RS method: {method!r}")
        self.lookback_days = lookback_days
        self.method = method

    def compute(self, prices: PriceFrame, index_prices: PriceFrame) -> pd.Series:
        stock_ret = prices.df["close"].pct_change(self.lookback_days)
        index_ret = index_prices.df["close"].pct_change(self.lookback_days)
        index_ret = index_ret.reindex(prices.df.index)
        return (stock_ret - index_ret).rename(f"rs_{self.lookback_days}")
