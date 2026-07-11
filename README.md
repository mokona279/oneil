# oneil-bt — 주도주 추세추종 매매규칙서 백테스트

오닐/미너비니 계열 **주도주 추세추종 규칙서(`oneil_strategy` v3-3)** 를 기계적으로 실행하는
한국 주식(코스피/코스닥) 백테스트 엔진. 규칙을 기계적으로 돌렸을 때의 수익률·리스크 특성을
검증하고, 이후 파라미터 민감도 분석이 가능한 결정론적 구조를 확보하는 것이 목표다.

- **대상**: 위성 슬리브(한국 주도주)만 모델링. 코어(미국·글로벌 지수 ETF) 배분은 범위 밖.
- **언어/도구**: Python 3.11+, pandas / numpy, PyYAML(설정), pytest.
- **설계 원칙**: 1클래스=1책임=1파일, 의존성 주입 + Protocol 계약 선행, 모든 규칙 수치 외부화(config), 룩어헤드 없음·결정론.

---

## 현재 상태

**Phase 0~8 완료 + 파라미터 민감도 스윕 하니스** — 골격부터 통합·회귀·문서까지 전 Phase를
구축하고, 계획서 §11 후속과제의 첫 항목(민감도 스윕)을 얹었다. 유닛+통합 테스트 220개 green.
계획서(§8)의 v1 로드맵 전 구간이 끝났다.

각 Phase의 상세 목표·산출 파일·테스트·"세션 시작 컨텍스트"는 계획서
[`docs/backtest_plan.md`](docs/backtest_plan.md) §8에 있다. 진행 현황과 미결정 사항(§12 Q)
확정 이력은 [`docs/PROGRESS.md`](docs/PROGRESS.md)에서 관리한다.

**후속 과제**(구조는 v1에서 확보, 계획서 §11): ~~파라미터 민감도 스윕 하니스~~(구현 완료, ↓ 실행 예시),
워크포워드/롤링 검증, API 데이터 소스 교체, 펀더멘털·수급 소스 통합, 생존편향 보정.

---

## 문서 인덱스

| 문서 | 내용 |
|---|---|
| [`docs/oneil_strategy.md`](docs/oneil_strategy.md) | **규칙 단일 진실 원천** — 매매규칙서 원문 v3-3 |
| [`docs/backtest_plan.md`](docs/backtest_plan.md) | 구현 계획서 — 아키텍처, 인터페이스 계약, Phase별 계획, 미결정 질문(§12) |
| [`docs/backtest_plan_prompt.md`](docs/backtest_plan_prompt.md) | 계획서를 생성한 원본 프롬프트(요구사항 정의) |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | 진행 현황 체크리스트 + 결정사항 확정 로그 |

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

### 백테스트 실행 (소형 예제 데이터)

`data_example/`에 통합 테스트용 소형 데이터셋이 있다(재현: `python data_example/generate.py`).
CLI 직접 실행 시엔 `PYTHONPATH=src`를 지정한다:

```bash
PYTHONPATH=src "C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe" \
    -m oneil_bt.cli.run_portfolio \
    --price-dir data_example/prices \
    --kospi data_example/kospi.csv --kosdaq data_example/kosdaq.csv \
    --meta data_example/meta.csv \
    --rules config/rules_v3-3.yaml --costs config/costs.yaml \
    --start 2019-01-02 --end 2020-03-24 --cash 1e8 --out out/example
```

`--out`을 주면 트레이드 로그·자본곡선·이벤트 CSV와 성과지표(`metrics.txt`/`.json`)를
그 디렉토리에 쓴다(§9). 단일종목은 `run_single --symbol 005930 ...`. 데이터셋 상세는
[`data_example/README.md`](data_example/README.md).

### 파라미터 민감도 스윕 (계획서 §11 후속과제)

규칙 수치가 전부 config로 외부화돼 있어, 축(config 점 경로)별 값 목록의 데카르트 곱으로
백테스트를 반복 실행하고 조합별 성과지표를 랭킹 표·CSV로 낸다.

```bash
PYTHONPATH=src "C:/Users/mh.han/repos/daytrading/.venv/Scripts/python.exe" \
    -m oneil_bt.cli.run_sweep \
    --price-dir data_example/prices \
    --kospi data_example/kospi.csv --kosdaq data_example/kosdaq.csv \
    --meta data_example/meta.csv \
    --rules config/rules_v3-3.yaml --costs config/costs.yaml \
    --start 2019-01-02 --end 2020-03-24 --cash 1e8 \
    --param sizing.max_weight_pct=5,10,20 --param stop.atr_mult=1.5,2,2.5 \
    --sort total_return_pct --out out/sweep.csv
```

축은 `--param <점경로>=<v1,v2,...>`(반복) 또는 `--grid <grid.yaml>`(`{점경로: [값,...]}`)로
지정한다. 값은 숫자·`true/false`·Enum 문자열(예: `stop.method=atr2x,fixed_pct`)을 받는다.
파이썬 API는 `oneil_bt.analysis`의 `run_sweep`/`ParameterGrid`/`apply_overrides`.

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
