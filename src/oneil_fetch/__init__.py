"""oneil_fetch — 실데이터 수집 스크립트 (계획서 docs/data_fetch_plan.md).

pykrx/FinanceDataReader로 한국 주식 일봉(수정주가)·지수·상장주식수·상장일을 받아
oneil_bt 엔진이 읽는 CSV 레이아웃(prices/{symbol}.csv, kospi/kosdaq.csv, meta.csv)으로
저장한다.

의존 방향은 oneil_fetch → oneil_bt.data(검증기 재사용) 단방향만 허용한다.
oneil_bt는 oneil_fetch를 절대 import하지 않는다 (§3.1).
"""

from __future__ import annotations

# 지수 코드 (pykrx get_index_ohlcv). §2.1.
KOSPI_INDEX_CODE = "1001"
KOSDAQ_INDEX_CODE = "2001"
