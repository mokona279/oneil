# oneil-bt — 주도주 추세추종 매매규칙서 백테스트

오닐/미너비니 계열 **주도주 추세추종 규칙서(`oneil_strategy` v3-3)** 를 기계적으로 실행하는
한국 주식(코스피/코스닥) 백테스트 엔진. 규칙을 기계적으로 돌렸을 때의 수익률·리스크 특성을
검증하고, 이후 파라미터 민감도 분석이 가능한 결정론적 구조를 확보하는 것이 목표다.

- **대상**: 위성 슬리브(한국 주도주)만 모델링. 코어(미국·글로벌 지수 ETF) 배분은 범위 밖.
- **언어/도구**: Python 3.11+, pandas / numpy, PyYAML(설정), pytest.
- **설계 원칙**: 1클래스=1책임=1파일, 의존성 주입 + Protocol 계약 선행, 모든 규칙 수치 외부화(config), 룩어헤드 없음·결정론.

---

## 현재 상태

**Phase 0~8 완료 + 민감도 스윕 + 진입 진단** — 골격부터 통합·회귀·문서까지 전 Phase를
구축하고, 계획서 §11 후속과제로 파라미터 민감도 스윕과 진입 진단(퍼널·게이트 분해·현
베이스 단계)을 얹었다. 유닛+통합 테스트 261개 green. 계획서(§8)의 v1 로드맵 전 구간이 끝났다.

각 Phase의 상세 목표·산출 파일·테스트·"세션 시작 컨텍스트"는 계획서
[`docs/backtest_plan.md`](docs/backtest_plan.md) §8에 있다. 진행 현황과 미결정 사항(§12 Q)
확정 이력은 [`docs/PROGRESS.md`](docs/PROGRESS.md)에서 관리한다.

**후속 과제**(구조는 v1에서 확보, 계획서 §11): ~~파라미터 민감도 스윕 하니스~~·~~진입
진단(퍼널·게이트·현 단계)~~(구현 완료, ↓ 사용 방법), 워크포워드/롤링 검증, API 데이터
소스 교체, 펀더멘털·수급 소스 통합, 생존편향 보정.

---

## 문서 인덱스

| 문서 | 내용 |
|---|---|
| [`docs/oneil_strategy.md`](docs/oneil_strategy.md) | **규칙 단일 진실 원천** — 매매규칙서 원문 v3-3 |
| [`docs/backtest_plan.md`](docs/backtest_plan.md) | 구현 계획서 — 아키텍처, 인터페이스 계약, Phase별 계획, 미결정 질문(§12) |
| [`docs/backtest_plan_prompt.md`](docs/backtest_plan_prompt.md) | 계획서를 생성한 원본 프롬프트(요구사항 정의) |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | 진행 현황 체크리스트 + 결정사항 확정 로그 |
| [`docs/data_fetch_plan.md`](docs/data_fetch_plan.md) | 실데이터 수집 스크립트(`oneil_fetch`) 구현 계획 — pykrx/FDR (구현 완료, ↓ 실행 예시) |

---

## 프로젝트 구조

```
oneil/
├─ config/
│  ├─ rules_v3-3.yaml            # 모든 규칙 수치 + rulebook_version 태그
│  └─ costs.yaml                 # 수수료·거래세(기간별)·슬리피지
├─ docs/                         # 규칙서·계획서·진행문서 (위 인덱스)
├─ src/oneil_bt/
│  ├─ domain/     enums / bar(PriceFrame) / config(Config DTO)   # 의존 없음
│  ├─ data/       datasource(Protocol) / csv_source / loader / calendar / metadata
│  ├─ indicators/ (Phase 1)  MA / ATR / 52주고저 / RS / IndicatorSet
│  ├─ rules/      (Phase 2~4B)  시장필터 / 트렌드템플릿 / 과열 / RS / 베이스감지·품질 / 손절·청산
│  ├─ execution/  (Phase 4A)   fill_model / cost_model / orders
│  ├─ portfolio/  (Phase 5)    position_sizer / portfolio / risk_governor
│  ├─ engine/     (Phase 6)    context / pipeline / engine (일별 이벤트 루프)
│  ├─ reporting/  (Phase 7)    trade_log / equity_curve / metrics / event_list / report
│  ├─ analysis/   (후속 §11)   override(점경로 config 치환) / sweep(그리드 실행·CSV)
│  └─ cli/        (Phase 6~)   run_single / run_portfolio / run_sweep
├─ src/oneil_fetch/            # 실데이터 수집 (pykrx/FDR → 엔진 CSV). oneil_bt 무의존
│  ├─ krx_client / transform / universe / incremental / meta_builder / state / writer
│  └─ env_loader / cli / __main__
└─ tests/
   ├─ fixtures/   synthetic.py  (합성 OHLCV 빌더)
   ├─ unit/       (모듈별 미러링)
   └─ integration/ (Phase 8)   test_smoke / test_golden
```

의존 방향은 항상 고수준→저수준(cli → engine → rules/portfolio/execution → indicators → data → domain).
`domain`은 아무것도 의존하지 않는다. 상세 레이어 다이어그램은 계획서 §2.3.

### 아키텍처 스타일
지표는 심볼별 1회 벡터 계산·캐시(과거포함 롤링이라 룩어헤드 없음), 트레이드 라이프사이클만
일별 이벤트 루프로 처리하는 **하이브리드**. 경로 의존 상태(분할매수 평단·손절 재계산, 8종목/현금,
동일일 우선순위, 베이스 스테이지)는 이벤트 루프로, 순수 함수인 지표는 벡터화로.

---

## 개발 환경 · 실행

Python 바이너리는 이웃 `daytrading` 레포의 venv를 공유한다:

```
C:\Users\mh.han\repos\daytrading\.venv\Scripts\python.exe
```

테스트 실행:

```bash
"C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe" -m pytest -q
```

`pyproject.toml`의 `pythonpath = ["src", "."]` 설정으로 (pytest는) 별도 설치 없이 `import` 가능하다.

### 백테스트 CLI

세 진입점(`run_single`·`run_portfolio`·`run_sweep`)은 같은 데이터·설정 인자를 공유하며,
모듈로 실행하되 `PYTHONPATH=src`를 지정한다. 아래 예시는 실데이터(`data/…`, ↓ '실데이터
수집' 후) 기준이다. 데이터 준비 전이라면 `data_example/` 소형 데이터로 동일하게 돌릴 수
있다(`--price-dir data_example/prices … --start 2019-01-02 --end 2020-03-24`, 재현:
`python data_example/generate.py`, 상세 [`data_example/README.md`](data_example/README.md)).

**공통 인자** (세 CLI 공통)

| 인자 | 필수 | 설명 |
|---|---|---|
| `--price-dir` | ✓ | 종목 일봉 CSV 디렉토리(`{symbol}.csv`) |
| `--meta` | ✓ | `meta.csv` (symbol·name·market·listing_date·shares_out) |
| `--kospi` | ✓ | 코스피 지수 CSV — 거래일 캘린더의 기준 |
| `--kosdaq` | | 코스닥 지수 CSV(코스닥 종목이 있으면 필요) |
| `--rules` | ✓ | 규칙 수치 YAML(`config/rules_v3-3.yaml`) |
| `--costs` | ✓ | 비용 YAML(`config/costs.yaml`) |
| `--start`·`--end` | ✓ | 백테스트 구간 `YYYY-MM-DD`. 지표 워밍업(200MA+52주) 위해 **데이터**는 15개월 더 앞부터 있어야 |
| `--cash` | | 초기자본(기본 `1e8`) |
| `--out` | | 리포트 출력 디렉토리(생략 시 콘솔 요약만) |

#### 단일종목 — `oneil_bt.cli.run_single`

한 종목만 엔진에 태워 판정·트레이드를 본다. 공통 인자 + **`--symbol`**(하나).

```bash
PYTHONPATH=src "C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe" \
    -m oneil_bt.cli.run_single --symbol 000660 \
    --price-dir data/prices --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
    --meta data/meta.csv --rules config/rules_v3-3.yaml --costs config/costs.yaml \
    --start 2024-01-01 --end 2026-07-12 --cash 1e8 --out out/hynix
```

#### 포트폴리오 — `oneil_bt.cli.run_portfolio`

유니버스를 8종목/현금 규칙(§1)으로 굴린다. **`--symbols`**(쉼표구분)로 대상을 좁히고,
생략하면 `--price-dir`의 전 종목이 유니버스가 된다.

```bash
PYTHONPATH=src "C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe" \
    -m oneil_bt.cli.run_portfolio \
    --symbols 005930,000660,271560,319660 \
    --price-dir data/prices --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
    --meta data/meta.csv --rules config/rules_v3-3.yaml --costs config/costs.yaml \
    --start 2024-01-01 --end 2026-07-12 --cash 1e8 --out out/portfolio
```

#### 파라미터 민감도 스윕 — `oneil_bt.cli.run_sweep`

규칙 수치가 전부 config로 외부화돼 있어, 축(config 점 경로)별 값 목록의 데카르트 곱으로
백테스트를 반복 실행하고 조합별 성과지표를 랭킹 표·CSV로 낸다.

```bash
PYTHONPATH=src "C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe" \
    -m oneil_bt.cli.run_sweep --symbols 000660 \
    --price-dir data/prices --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
    --meta data/meta.csv --rules config/rules_v3-3.yaml --costs config/costs.yaml \
    --start 2024-01-01 --end 2026-07-12 --cash 1e8 \
    --param base.stage.max_stage=3,4,5 --param quality.contraction_le_pivot_pct=10,15,20 \
    --sort n_trades --out out/sweep.csv
```

- **축 지정**: `--param <점경로>=<v1,v2,...>`(반복) 또는 `--grid <grid.yaml>`(`{점경로: [값,...]}`).
  값은 숫자·`true/false`·Enum 문자열(예: `stop.method=atr2x,fixed_pct`). 점 경로는 YAML 키가
  아니라 **Config DTO** 기준이다(예: `trend.high_52w_within_pct`, `base.stage.max_stage`).
- **`--sort`**: 랭킹 지표(기본 `total_return_pct`). `n_trades`·`mdd_pct`·`expectancy_r` 등
  아래 지표 열 이름을 쓴다. `--asc`면 오름차순.
- 파이썬 API는 `oneil_bt.analysis`의 `run_sweep`/`ParameterGrid`/`apply_overrides`.

### 출력물 (`--out`)

`--out`을 준 백테스트(`run_single`/`run_portfolio`)는 그 디렉토리에 아래를 쓴다. 성과 요약
(`metrics`)·원자료(`trades`/`equity_curve`/`events`)에 더해, 규칙이 종목을 **왜 사고/안
샀는지** 사후 감사용 **진단 CSV 3종**(§11)이 함께 나온다 — 엔진이 실제 진입 판정 시점에
기록해 이벤트/체결과 100% 일치한다(끄려면 엔진 `record_diagnostics=False`).

| 파일 | 내용 |
|---|---|
| `trades.csv` | 트레이드 로그 — 진입/청산/수량/손익/R배수/보유일 |
| `equity_curve.csv` | 일별 자본곡선 — 현금·평가액·노출·시장상태 |
| `events.csv` | 육안검증 이벤트 — 돌파후보·진입·피라미딩·청산·추격스킵·거래량실패 |
| `metrics.txt` / `.json` | 성과지표 — 수익률·CAGR·MDD·승률·손익비·기댓값·거래비용 등 |
| `entry_funnel.csv` | **진단** 종목별 진입 퍼널 — 유효베이스→단계→돌파(기회)→게이트별 통과→체결까지 각 단계 잔존 세션 수. "왜 안 샀나"를 한 줄로 |
| `gate_breakdown.csv` | **진단** 돌파(기회)일마다 게이트 개별 판정(트렌드·RS·시장·과열·ATR·수축·드라이업). `n_failed=1`은 파라미터 하나만 풀면 잡히는 니어미스 |
| `base_stage.csv` | **진단** 종료 시점 종목별 **현 베이스 단계**·피벗·깊이 + 유효 돌파 이력(최고 단계·마지막 돌파) |

진단 산출 로직은 [`reporting/diagnostics.py`](src/oneil_bt/reporting/diagnostics.py). 스윕은
조합별 성과지표를 `--out` CSV 한 파일로만 낸다(위 진단 3종은 스윕 산출엔 없음).

### 실전 워크플로우 (실데이터)

수집 → 포트폴리오 백테스트 → 안 잡힌 종목 심화 → 병목 파라미터 스윕의 전형적 흐름:

```bash
PY="C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe"

# 1) 수집 — 백테스트 시작(2024-01)보다 15개월 앞부터(워밍업). ↓ '실데이터 수집' 참고
PYTHONPATH=src "$PY" -m oneil_fetch --symbols 005930,000660,271560,319660 \
    --start 2022-10-01 --end 2026-07-12 --out data --env-file C:/path/to/.env

# 2) 포트폴리오 백테스트 (2024-01 ~ 현재)
PYTHONPATH=src "$PY" -m oneil_bt.cli.run_portfolio --symbols 005930,000660,271560,319660 \
    --price-dir data/prices --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
    --meta data/meta.csv --rules config/rules_v3-3.yaml --costs config/costs.yaml \
    --start 2024-01-01 --end 2026-07-12 --cash 1e8 --out out/portfolio

# 3) 안 잡힌 종목 심화 — 단일 백테스트 후 out/hynix/entry_funnel.csv·gate_breakdown.csv로 병목 확인
PYTHONPATH=src "$PY" -m oneil_bt.cli.run_single --symbol 000660 \
    --price-dir data/prices --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
    --meta data/meta.csv --rules config/rules_v3-3.yaml --costs config/costs.yaml \
    --start 2024-01-01 --end 2026-07-12 --out out/hynix

# 4) 병목 파라미터 스윕 — gate_breakdown의 니어미스(n_failed=1) 축을 풀어 재실행
PYTHONPATH=src "$PY" -m oneil_bt.cli.run_sweep --symbols 000660 \
    --price-dir data/prices --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
    --meta data/meta.csv --rules config/rules_v3-3.yaml --costs config/costs.yaml \
    --start 2024-01-01 --end 2026-07-12 \
    --param base.stage.max_stage=3,4,5 --param quality.contraction_le_pivot_pct=10,15,20 \
    --sort n_trades --out out/sweep.csv
```

### 실데이터 수집 (`oneil_fetch`)

pykrx(시세·지수·상장주식수)와 FinanceDataReader(상장일)로 실제 코스피/코스닥 시세를
받아 위 백테스트가 읽는 CSV 레이아웃(`prices/{symbol}.csv`, `kospi.csv`, `kosdaq.csv`,
`meta.csv`)으로 저장한다. `oneil_bt`는 한 줄도 수정하지 않으며, 산출물이 엔진 로더 검증을
통과하는지 쓰기 시 자기검증한다. 설계는 [`docs/data_fetch_plan.md`](docs/data_fetch_plan.md).

**1) 의존성 설치** (공유 venv에):

```bash
"C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe" -m pip install pykrx finance-datareader
# 또는: pip install -e ".[fetch]"
```

**2) KRX 자격증명** — 이 venv의 pykrx는 KRX 데이터 접근에 로그인 세션을 요구한다
(`KRX_ID`/`KRX_PW` 미설정 시 지수·티커목록·시가총액·거래대금 엔드포인트가 빈 응답을 준다).
[`.env.example`](.env.example)를 복사해 `.env`(gitignore 대상)를 만들고 KRX 계정
(https://data.krx.co.kr)을 채운 뒤 `--env-file`로 넘긴다. `.env`는 코드가 파이썬으로만
로드하며 값은 로그에 남기지 않는다.

**3) 수집 실행**:

```bash
# 지정 종목만 (증분·자기검증·리포트 포함)
PYTHONPATH=src "C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe" \
    -m oneil_fetch --symbols 005930,000660,035720 \
    --start 2018-07-01 --end 2020-12-31 --out data \
    --env-file C:/path/to/.env

# 전 유니버스(코스피+코스닥 보통주, 스팩 제외) — 수 시간 소요, 중단 시 이어받기
PYTHONPATH=src "C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe" \
    -m oneil_fetch --start 2018-07-01 --end 2026-07-10 --out data --env-file C:/path/to/.env
```

- `--start`는 **백테스트 예정 시작일보다 최소 15개월(달력) 앞**으로 잡는다(200MA+52주 워밍업).
- `--dry-run`으로 유니버스·계획만 확인(시세 미수집). `--full-refresh`로 증분 무시 전체 재수집.
- 출력 `data/`는 **gitignore 대상**. 수집 후 위 `run_portfolio` 예시의 `--price-dir data/prices …`로
  실데이터 백테스트를 돌린다.

**계획 대비 구현 노트** (실데이터로 확인된 사항):
- **거래대금(value)**: 이 pykrx 버전은 `adjusted=True`에서 거래대금 대신 등락률을 준다.
  거래대금은 수정과 무관한 실측값이므로 `adjusted=False`를 추가 호출해 병합한다(종목당 2호출,
  근사 아님 — 계획서 §9 Q4 실측 원칙 충족).
- **수정주가 반올림 보정**: 수정 OHLC는 필드별 독립 반올림으로 종가가 고가보다 1원 큰 식의
  정합 위반이 흔하다. 이런 미세 위반(종가 대비 0.5% 이하)은 행을 버리지 않고 high/low를
  클램프해 보존한다(‘종가=고가’ 강세일 손실 방지). 큰 붕괴만 삭제.

---

## 핵심 계약 (요약)

- **데이터**: 종목당 일봉 CSV 1파일(수정주가), 코스피·코스닥 지수 CSV 2개(거래일 캘린더 기준),
  단일 `meta.csv`(symbol, name, market, listing_date). 상세 §4.
- **설정**: 모든 규칙 수치를 `config/rules_v3-3.yaml`에 외부화. 코드 내 하드코딩 금지.
  `rulebook_version` 태그로 재현성 확보. 상세 §5.
- **정합성**: 판정/체결 시점 분리(룩어헤드 금지), 일봉 기반 결정론적 체결 모델,
  비용(수수료·기간별 거래세·슬리피지) 반영, 정수주 floor. 상세 §6.

---

## 범위 제외 (v1)

데이터 부재/불확정으로 v1에서 제외. 구조는 확보해 두어 데이터 확보 시 추가 가능. (계획서 §11)

- 분기 영업이익/EPS +20%·흑자 등 **펀더멘털 필터**
- **대장주(테마 1~2등) 판별·피어그룹 RS 랭크** — v1은 지수 대비 상대수익(불리언)만
- 수급(기관·외국인)·밸류업 공시 가점
- 과열 제외의 상한가/±15% 스윙 반복(데이터·정의 필요)

---

## 미결정 사항

임의 가정 없이, 결과에 영향을 주는 해석·충돌·데이터 이슈는 계획서 §12에 질문으로 남겨두었다.
가장 영향이 큰 것은 **손절 체결 시점 충돌**(종가확정 다음날 매도 vs 장중 자동스탑, §12 Q1).
확정 이력은 `docs/PROGRESS.md`에 기록한다.
