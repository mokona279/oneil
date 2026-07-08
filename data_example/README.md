# data_example — 통합 스모크·골든 회귀용 소형 데이터셋

`tests/integration/`이 CsvDataSource로 로드해 엔진을 end-to-end로 돌리는 **결정론적
소형 데이터**다. 실서비스 시세가 아니라, 규칙서의 주요 경로(돌파 진입·피라미딩·
추세이탈/손절 청산)를 자극하도록 손으로 설계한 합성 시계열이다.

## 구성

| 파일 | 내용 |
|---|---|
| `prices/{symbol}.csv` | 종목 일봉 `date,open,high,low,close,volume,value` |
| `kospi.csv` / `kosdaq.csv` | 지수 일봉 `date,close` — **거래일 캘린더 기준** |
| `meta.csv` | `symbol,name,market,listing_date,shares_out` |

종목 3개 × 320세션(2019-01-02 ~ 2020-03-24):

| symbol | market | 시나리오 |
|---|---|---|
| `005930` | KOSPI | 상승추세 → 얕은 베이스 → 돌파 후 지속 상승(보유) |
| `000660` | KOSPI | 돌파 진입 후 급락 → 손절/60MA 청산 |
| `035720` | KOSDAQ | 돌파 후 지속 상승(보유) |

## 재현

데이터는 `generate.py`가 **난수 없이** 순수 함수로 만든다 — 항상 동일 산출:

```bash
python data_example/generate.py
```

시나리오·세션 수·거래대금 게이트(100억) 통과 조건은 `generate.py` 상단 주석 참조.
데이터를 의도적으로 바꾸면 `tests/integration/test_golden.py`의 `GOLDEN_DIGEST`도
갱신해야 한다(그 변경이 정당한지는 리뷰가 판단).

## 실행 예시 (CLI)

```bash
python -m oneil_bt.cli.run_portfolio \
    --price-dir data_example/prices \
    --kospi data_example/kospi.csv --kosdaq data_example/kosdaq.csv \
    --meta data_example/meta.csv \
    --rules config/rules_v3-3.yaml --costs config/costs.yaml \
    --start 2019-01-02 --end 2020-03-24 --cash 1e8 \
    --out out/example
```
