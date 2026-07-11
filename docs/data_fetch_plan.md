# 실데이터 수집 스크립트 구현 계획 — `fetch_data`

> **이 문서는 독립 세션에서 구현을 시작할 수 있도록 자체 완결적으로 작성했다.**
> 구현 세션 시작 컨텍스트: 이 문서 전체 + `data_example/README.md` +
> `src/oneil_bt/data/loader.py`(출력이 통과해야 하는 검증기) + `src/oneil_bt/data/metadata.py`.
> 백테스트 엔진 내부는 몰라도 된다 — 계약은 전부 이 문서 §1에 옮겨 적었다.

---

## 0. 목적과 배경

백테스트 엔진(`oneil_bt`)은 CSV 파일(`CsvDataSource`)에서 데이터를 읽는다. 지금까지는
`data_example/`의 합성 데이터로만 돌렸고, **실제 한국 주식(코스피/코스닥) 시세로 백테스트를
돌리기 위한 수집 스크립트**가 필요하다. 이 스크립트는:

- **pykrx**(KRX 정보데이터시스템 래퍼)로 종목 일봉(수정주가)·지수 일봉·상장주식수를,
  **FinanceDataReader**로 상장일을 수집해,
- `data_example/`과 동일한 레이아웃(`prices/{symbol}.csv`, `kospi.csv`, `kosdaq.csv`, `meta.csv`)으로
  저장하고,
- 증분 갱신(이미 받은 종목은 마지막 날짜 이후만)과 KRX rate limit 대응을 내장한다.

엔진 쪽 코드는 **한 줄도 수정하지 않는다**. 산출물이 기존 로더 검증을 통과하면 끝이다.

### 왜 pykrx + FinanceDataReader인가 (선정 근거)

| 후보 | 판정 | 이유 |
|---|---|---|
| **pykrx** | **채택 (시세·주식수)** | OHLCV+거래대금을 한 호출에 제공(거래대금 100억 게이트에 실측값 사용 가능), 수정주가 지원, 지수·과거시점 티커목록 제공. 무료. |
| **FinanceDataReader** | **채택 (상장일 보충)** | `StockListing('KRX-DESC')`가 상장일 제공 — pykrx에 없는 유일한 필수 항목. |
| 증권사 API (KIS/키움) | 보류 | 인증·토큰 셋업이 무겁다. 실매매 연동 단계에서 도입(README 후속과제 "API 데이터 소스 교체"). |
| KRX 수동 CSV | 기각 | 유니버스 규모(수백~수천 종목)에서 비현실적. |
| 유료(DataGuide 등) | 기각 | 현 단계 과잉. |

RS가 전 종목 퍼센타일 랭킹이 아니라 **지수 대비 불리언**(계획서 §12 Q16 확정)이므로,
전 시장 시세가 강제 요건이 아니다. 유니버스에 포함할 종목 + 지수 2개만 있으면 된다.

---

## 1. 산출물 계약 (이 스크립트의 출력 = 엔진의 입력)

출력 디렉토리(기본 `data/`, **gitignore 대상**) 레이아웃:

```
data/
├─ prices/{symbol}.csv     # 종목 일봉 (symbol = 6자리 티커, 예: 005930.csv)
├─ kospi.csv               # KOSPI 지수 일봉
├─ kosdaq.csv              # KOSDAQ 지수 일봉
├─ meta.csv                # 종목 메타데이터
└─ _state/                 # 스크립트 내부 상태 (엔진은 읽지 않음)
   └─ fetch_state.json     # 체크포인트·실패목록·마지막 수집일
```

### 1.1 종목 일봉 `prices/{symbol}.csv`

헤더: `date,open,high,low,close,volume,value` — value(거래대금, 원)는 로더상 선택이지만
**우리는 항상 쓴다**(없으면 엔진이 `close*volume` 근사를 쓰게 되는데, 실측값이 있으므로).

로더(`CsvBarLoader.load`)가 강제하는 검증 — **하나라도 어기면 엔진이 ValidationError로 죽는다**:

- `date` 파싱 가능(ISO `YYYY-MM-DD` 권장), **중복 날짜 금지** (정렬은 로더가 해주지만 정렬해 쓴다)
- `open,high,low,close,volume`(+포함 시 `value`)에 **NaN/빈값 금지**
- `high >= low >= 0`, `open`과 `close`가 `[low, high]` 안, `volume >= 0`
- 인코딩 UTF-8 (로더는 utf-8-sig → cp949 순 자동감지; BOM 없는 UTF-8로 쓴다)

**빠진 날짜(휴장·거래정지)는 행을 안 쓰면 된다** — 엔진이 지수 캘린더로 reindex한다.
행을 억지로 채우지(전일 종가 복제 등) **않는다**.

### 1.2 지수 일봉 `kospi.csv` / `kosdaq.csv`

헤더: `date,close` (로더 `load_index`는 OHLC 없으면 close로 채움). 검증은 §1.1과 동일 계열.

**중요**: 엔진의 거래일 캘린더 = 지수 CSV의 날짜 집합이고 종목은 여기에 reindex된다
(`src/oneil_bt/data/calendar.py`). 따라서 **지수의 날짜 범위가 모든 종목 데이터를 완전히
덮어야 한다** — 지수를 종목과 같은 `--start/--end`로 받으면 자동 충족.

### 1.3 메타 `meta.csv`

헤더: `symbol,name,market,listing_date,shares_out`
(`MetaRepository.from_csv` 검증: symbol 중복 금지, market은 `KOSPI`/`KOSDAQ`만 허용 —
**KONEX 불가**, `listing_date`는 ISO 날짜 또는 빈칸, `shares_out`은 정수 또는 빈칸)

- `prices/`에 있는 **모든 심볼이 meta.csv에 존재해야 한다** (없으면 엔진이 죽음). 역은 무방.
- `listing_date`: IPO(상장 52주 미만) 유니버스 배제에 쓰인다. 못 구하면 빈칸(배제 미적용)으로
  두되 수집 리포트에 경고를 남긴다.
- `shares_out`: 선택. 최신 시점 스냅샷이면 충분(과거 시점별 아님).

### 1.4 워밍업 요건 (수집 범위 결정)

엔진은 백테스트 시작일 이전 **약 300거래일**(200MA + 52주 + 6M RS 여유; 계획서 §4.4)의
워밍업을 요구하고, 부족하면 종목별 판정 시작일을 자동으로 미룬다. 즉 데이터가 짧아도
죽지는 않지만 백테스트 앞부분이 통째로 버려진다.

→ 스크립트는 `--start`를 그대로 쓰되, **백테스트 예정 시작일보다 최소 15개월(달력)
앞선 날짜를 넘기라고 CLI 도움말과 README에 명시**한다. (예: 백테스트 2020-01-02 시작이면
수집은 2018-10-01 이전부터.)

---

## 2. 데이터 소스 API 매핑

의존성: `pykrx`, `finance-datareader` (설치는 §3.3). 모두 KRX/KIND/네이버를 스크레이핑하는
라이브러리라 **호출 간 sleep이 필수**다(§5.1).

### 2.1 pykrx (`from pykrx import stock`)

| 용도 | 호출 | 반환 (한글 컬럼) | 비고 |
|---|---|---|---|
| 종목 일봉 | `stock.get_market_ohlcv(fromdate, todate, ticker, adjusted=True)` | index=날짜, `시가,고가,저가,종가,거래량,거래대금,등락률` | 날짜 포맷 `"YYYYMMDD"`. **adjusted=True가 기본이지만 명시**한다. 거래대금은 수정과 무관한 실측 원화. |
| 지수 일봉 | `stock.get_index_ohlcv(fromdate, todate, code)` | 동일 계열 | `"1001"`=KOSPI, `"2001"`=KOSDAQ. `종가`만 취해 `close`로. |
| 티커 목록 | `stock.get_market_ticker_list(date, market="KOSPI"\|"KOSDAQ")` | `list[str]` | date 기준 시점의 상장 종목. |
| 종목명 | `stock.get_market_ticker_name(ticker)` | `str` | meta의 `name`. |
| 상장주식수 | `stock.get_market_cap(date, market=...)` | index=티커, `종가,시가총액,거래량,거래대금,상장주식수` | **시장 단위 1호출**로 전 종목 스냅샷 → 종목별 호출 불필요. |

컬럼 rename 맵(transform 모듈에 상수로): `시가→open, 고가→high, 저가→low, 종가→close,
거래량→volume, 거래대금→value`, index(날짜)→`date`.

### 2.2 FinanceDataReader (`import FinanceDataReader as fdr`)

| 용도 | 호출 | 사용 컬럼 |
|---|---|---|
| 상장일 | `fdr.StockListing('KRX-DESC')` | `Code`(6자리), `ListingDate` |

`Code`를 zero-pad 6자리 문자열로 정규화해 조인. 조인 실패 종목은 `listing_date` 빈칸 + 경고.

### 2.3 API 표류 대비

pykrx/FDR은 KRX 웹 변경에 따라 컬럼명·동작이 깨질 수 있다. → 외부 라이브러리 호출은
**전부 `krx_client.py` 한 파일에 격리**하고(§3.1), 반환 직후 기대 컬럼 존재를 assert해서
표류 시 즉시(조용한 오염이 아니라) 실패하게 한다.

---

## 3. 아키텍처 · 파일 구조

### 3.1 모듈 분해 (레포 원칙: 1클래스=1책임=1파일, DI)

새 패키지 `src/oneil_fetch/` — **`oneil_bt`와 분리**한다. 이유: 네트워크 의존성(pykrx/FDR)이
백테스트 코어에 스며들면 안 되고, 의존 방향은 `oneil_fetch → oneil_bt.data`(검증기 재사용)
단방향만 허용한다. `oneil_bt`는 `oneil_fetch`를 절대 import하지 않는다.

```
src/oneil_fetch/
├─ __init__.py
├─ __main__.py          # python -m oneil_fetch 진입점 (cli.main 호출)
├─ cli.py               # argparse, 오케스트레이션 (지수→유니버스→종목→메타→검증 순)
├─ krx_client.py        # pykrx/FDR 호출 격리 + sleep/재시도. Protocol로 계약 정의
│                       #   (class KrxClient(Protocol): ohlcv/index_ohlcv/tickers/names/…)
│                       #   실구현 PykrxClient + 테스트용 FakeClient는 tests에
├─ transform.py         # 한글컬럼 rename, dtype 정규화, 거래정지 행 정제(§5.3) — 순수 함수
├─ universe.py          # 티커 목록 → 보통주 필터(§5.4) → 대상 심볼 리스트 — 순수 함수
├─ meta_builder.py      # name/market/listing_date/shares_out 조인 → meta.csv 행 생성
├─ incremental.py       # 종목별 증분 판단 + 수정주가 오버랩 검증(§5.2) — 순수 함수
├─ state.py             # _state/fetch_state.json 읽기/쓰기 (체크포인트·실패목록)
└─ writer.py            # CSV 쓰기(UTF-8, float 포맷) + CsvBarLoader로 자기검증(§5.5)

tests/unit/fetch/       # FakeClient 기반 유닛테스트 (§7)
```

순수 함수 모듈(transform/universe/incremental)에 로직을 몰고, `krx_client`는 얇게 유지 —
네트워크 없이 테스트 가능한 면적을 최대화한다.

### 3.2 실행 형태

```bash
PYTHONPATH=src "C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe" \
    -m oneil_fetch --start 2017-07-01 --end 2026-07-10 --out data
```

(README 컨벤션과 동일하게 `PYTHONPATH=src` + 이웃 daytrading venv의 python 사용.)

### 3.3 pyproject / 환경 변경

- `[project.optional-dependencies]`에 `fetch = ["pykrx>=1.0", "finance-datareader>=0.9"]` 추가.
- 실제 설치는 공유 venv에: `".../daytrading/.venv/Scripts/python.exe" -m pip install pykrx finance-datareader`
- `[tool.setuptools.packages.find]`는 `where=["src"]`라 자동 포함 — 변경 불필요.
- `.gitignore`에 `data/` 추가 (없으면 `.gitignore` 생성).

---

## 4. CLI 사양

```
python -m oneil_fetch
  --start YYYY-MM-DD          # 필수. 수집 시작일 (워밍업 15개월 포함해 잡을 것 — §1.4)
  --end YYYY-MM-DD            # 기본: 오늘
  --out DIR                   # 기본: data
  --symbols 005930,000660     # 지정 시 유니버스 산출 생략, 이 종목만 (쉼표 또는 @파일)
  --market kospi|kosdaq|all   # 기본 all. 유니버스 산출 범위
  --include-non-common        # 우선주 등 비보통주 포함 (기본: 보통주만, §5.4)
  --sleep 0.5                 # 요청 간 sleep 초
  --full-refresh              # 증분 무시, 전 종목 전체 재수집
  --skip-meta / --skip-index  # 부분 실행 (재시도·디버그용)
  --dry-run                   # 유니버스·계획만 출력, 네트워크 호출 없음(티커목록 제외)
```

동작 순서: (1) 지수 2개 수집 → (2) 유니버스 확정 → (3) 종목 루프(증분 판단 → 수집 → 정제
→ 자기검증 → 쓰기 → 체크포인트) → (4) meta.csv 생성 → (5) 최종 리포트(§5.5).

종목 하나의 실패는 **전체를 중단시키지 않는다** — 실패 목록에 기록하고 계속, 마지막에
요약 출력 + exit code 1 (전부 성공 시 0).

---

## 5. 핵심 동작 상세

### 5.1 Rate limit · 재시도

- 모든 pykrx 호출 사이 `--sleep`(기본 0.5초).
- 실패(예외/빈 DataFrame) 시 지수 백오프 재시도 3회(2s → 8s → 32s). 그래도 실패면 해당
  종목을 실패 목록에 넣고 진행.
- 전 종목(~2,700개) 최초 수집은 수 시간 걸린다. **체크포인트**(`fetch_state.json`에 완료
  심볼 기록)로 중단 후 재실행 시 이어서 받는다. 완료 판단은 상태 파일이 아니라
  "CSV 존재 + 마지막 날짜 ≥ 요청 end의 마지막 거래일"로도 이중 확인.

### 5.2 증분 갱신과 수정주가 무효화 (가장 중요한 설계 지점)

단순 append는 **수정주가에서 틀린다**: 액면분할·감자 등 이벤트가 발생하면 KRX가 주는
수정주가는 **과거 전체가 소급 변경**된다. 기존 CSV 뒤에 새 구간만 붙이면 이벤트 전후가
서로 다른 기준의 가격이 되어 지표(MA·52주고저)가 조용히 오염된다.

→ **오버랩 검증 후 증분**:

1. 기존 CSV의 마지막 K=10 거래일 구간을 겹치게 재요청한다
   (요청 fromdate = 기존 마지막 날짜의 K거래일 전).
2. 겹친 구간의 `close`를 비교 — 상대오차 1e-6 초과 불일치가 하나라도 있으면
   **수정 이벤트 발생으로 간주, 그 종목은 전체 기간 재수집**.
3. 일치하면 새 구간만 append.

`--full-refresh`는 이 로직을 건너뛰고 무조건 전체 재수집.

### 5.3 거래정지·이상 행 정제 (pykrx 알려진 동작)

KRX 원본에는 로더 검증을 깨는 행이 실제로 존재한다. `transform.py`에서 규칙 기반 정제:

| 증상 | 처리 |
|---|---|
| 거래정지일: `volume==0`이고 `open==high==low==0`, `close`는 직전가 유지 | `open=high=low=close`로 보정 (로더의 "close가 [low,high] 밖" 검증 통과용) |
| `close<=0`인 행 | 행 삭제 + 경고 로그 (정상 시세일 수 없음) |
| `high<low` 등 정합 붕괴 | 행 삭제 + 경고 로그 (KRX 원본 오류) |
| NaN 포함 행 | 행 삭제 + 경고 로그 |

정제 결과 행이 0개면 그 종목은 실패 처리. 정제로 삭제/보정된 행 수는 리포트에 집계.

### 5.4 유니버스 (기본값 — §9 Q1과 연동)

- `--end` 시점의 `get_market_ticker_list` (KOSPI + KOSDAQ, KONEX 제외 — meta의 market
  enum이 KOSPI/KOSDAQ만 허용).
- **보통주 필터**: 티커 끝자리 `'0'`만 (한국 티커 관례상 우선주는 5/7/9/K 등). 추가로
  종목명에 `스팩` 포함 종목 제외. ETF/ETN/리츠는 `get_market_ticker_list`에 원래
  안 나오므로 별도 처리 불요 (구현 시 실데이터로 1회 확인할 것).
- **생존편향**: `--end` 시점 상장 종목만 수집하므로 기간 중 상폐 종목이 빠진다.
  v1은 이를 **감수하고 문서화**한다 (README 후속과제 "생존편향 보정"과 일치, §9 Q2).

### 5.5 자기검증 · 리포트

쓰기 전에 산출물을 엔진의 검증기로 직접 통과시킨다:

- 각 종목 CSV: `oneil_bt.data.loader.CsvBarLoader().load(path)` — 예외 없으면 통과.
- 지수 CSV: `load_index(path)`.
- meta.csv: `oneil_bt.data.metadata.MetaRepository.from_csv(path)` + `prices/` 심볼
  전수 포함 확인.

최종 리포트(stdout + `_state/fetch_report.json`): 성공/실패/스킵 종목 수, 종목별 행 수·
날짜 범위, 정제 통계, listing_date 결측 목록, 소요 시간.

---

## 6. 알려진 한계 (구현하지 않고 문서화만)

- **거래량 미수정**: pykrx `adjusted=True`는 가격만 수정하고 거래량은 원값이다. 분할
  전후로 거래량 절대치가 불연속 → 돌파 거래량 배수(50일 평균 대비) 판정이 분할 경계
  ±50거래일 구간에서 왜곡될 수 있다. 발생 빈도가 낮아 v1은 감수. (필요 시
  `volume ≈ value/close_adj` 재구성이 후속 옵션 — §9 Q3.)
- **생존편향**: §5.4. 상폐 종목 미포함 → 성과 과대평가 방향의 편향.
- **shares_out 스냅샷**: 최신 시점 값 하나만 저장. 과거 시점별 주식수 아님 (엔진도
  선택 항목으로만 취급).
- **KRX 데이터 자체의 오류**: §5.3 정제는 알려진 패턴만 다룬다. 리포트의 정제 통계가
  비정상적으로 크면 사람이 봐야 한다.

---

## 7. 테스트 계획

네트워크 호출은 테스트하지 않는다 — `krx_client`의 Protocol을 구현한 **FakeClient**
(고정 DataFrame 반환)를 주입해 전 로직을 오프라인·결정론으로 검증.

- `test_transform.py`: rename 정확성, 거래정지 행 보정(O=H=L=0 → close 복제), close<=0
  행 삭제, 정제 후 로더 통과(실제 `CsvBarLoader`로 round-trip).
- `test_incremental.py`: (a) 오버랩 일치 → append 경로, (b) close 불일치 → full-refetch
  판정, (c) 기존 CSV 없음 → 신규 경로, (d) K거래일 계산 경계.
- `test_universe.py`: 보통주 필터(끝자리 0), 스팩 이름 제외, market별 분리.
- `test_meta_builder.py`: FDR 조인 성공/실패(빈 listing_date), zero-pad 정규화,
  `MetaRepository.from_csv` round-trip.
- `test_state.py`: 체크포인트 저장/복원, 실패 목록 누적.
- `test_cli.py`: `--dry-run` 계획 출력, `--symbols` 우선, 실패 종목 있어도 계속 진행 +
  exit code 1.
- (선택, 기본 skip) `test_network_smoke.py`: `@pytest.mark.network` — 실 pykrx로 005930
  10거래일 수집 → 로더 통과. CI/기본 실행에서 제외(`-m "not network"` 불필요하게:
  마커 없으면 자동 skip 처리).

기존 220개 테스트는 건드리지 않는다 (`oneil_bt` 무수정이므로 영향 없음).

## 8. 구현 순서 (한 세션 내 커밋 단위)

1. **골격**: `src/oneil_fetch/` 패키지 + `krx_client.py` Protocol/PykrxClient +
   pyproject `fetch` 그룹 + venv 설치 + `.gitignore`.
2. **transform + writer**: rename·정제 순수 함수 → CSV 쓰기 → `CsvBarLoader` round-trip
   테스트. (여기까지로 단일 종목 end-to-end 수동 확인 가능)
3. **incremental + state**: 오버랩 검증·체크포인트.
4. **universe + meta_builder**: 유니버스 필터 + meta.csv.
5. **cli**: 오케스트레이션·리포트·에러 계속진행. `--dry-run`부터.
6. **실수집 검증**: 소수 종목(`--symbols 005930,000660,035720`) 실수집 →
   `run_portfolio` 실행이 실제로 도는지 확인 → README에 사용법 추가
   (개발환경·실행 섹션, 문서 인덱스에 본 문서 링크).

각 단계 끝에 pytest green 유지. 커밋 메시지 컨벤션은 기존 이력(`feat(...): 한글 요약`) 따름.

## 9. 미결정 질문 (기본값으로 진행 가능, 바꾸려면 확정 후)

| # | 질문 | 기본값 (근거) |
|---|---|---|
| Q1 | 유니버스에 우선주 포함? | **제외** (규칙서는 주도주=보통주 전제. `--include-non-common`으로 우회 가능) |
| Q2 | 상폐 종목 포함(생존편향 보정)을 v1 수집에서 시도? | **안 함** (pykrx로 가능은 하나 과거시점 티커 대량 조회·검증 비용이 큼. README 후속과제로 유지) |
| Q3 | 분할 경계 거래량 보정(`value/close_adj`) | **안 함** (§6. 왜곡 구간이 국소적) |
| Q4 | `value`를 근사(`close*volume`)로 채운 데이터 허용? | **불허** (pykrx가 실측 제공하므로 근사 불필요. 실측 없으면 실패 처리) |
| Q5 | 오버랩 검증 창 K, 허용오차 | **K=10 거래일, 상대오차 1e-6** (수정 이벤트는 수 % 이상 움직이므로 여유 큼) |
