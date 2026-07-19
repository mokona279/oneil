# U3 — 코드 적응 (최소 diff + KR 골든 비트 불변 게이트)

**목표**: 엔진이 미국 시장 라벨·비용·지수를 받도록 **최소한으로** 일반화한다.
통과 게이트는 단 하나 — **KR 기존 경로 결과가 비트 동일**할 것.

**선행**: U2 완료(설정 파일이 파싱 대상 스키마를 확정). U1 완료 전이라도 착수
가능(유닛·골든은 KR 데이터로 검증).

## 수정 지점 인벤토리 (사전 조사 완료분)

| 파일 | 현행 | 수정 방향 |
|---|---|---|
| `src/oneil_bt/domain/enums.py` | `Market = {KOSPI, KOSDAQ}` | U0 Q4 확정 라벨 추가(예: NYSE, NASDAQ). StrEnum 값 추가는 기존 직렬화에 무영향 |
| `src/oneil_bt/execution/cost_model.py` | `_tax_bp()`가 kospi_bp/kosdaq_bp 2분기 하드코딩 | 시장→bp 일반 맵으로. 기존 costs.yaml 키는 하위호환 파싱(KR 골든 보호) |
| `src/oneil_bt/domain/config.py` | sell_tax_schedule 파싱이 kospi_bp/kosdaq_bp 고정, `turnover_20d_min_krw` 키 이름 | 스키마 일반화 + 구키 하위호환. turnover 키는 별칭 허용(krw/usd) 또는 단위중립 신키+구키 유지 — U2 합의안 |
| `src/oneil_bt/cli/run_portfolio.py` (run_single·run_sweep 동일) | `--kospi`/`--kosdaq` 인자 → index_paths | 일반형 `--index MARKET=path` 반복 인자 추가, 기존 인자 유지(내부에서 동일 경로로 합류) |
| `src/oneil_bt/execution/fill_model.py:151` | `order.market` None 시 `Market.KOSPI` 기본값 | 기본값 의존을 제거하거나 명시 전달로 — KR 결과 불변 확인 필수 |
| `src/oneil_bt/data/metadata.py` | meta.csv market 컬럼 → Market 변환 | 신규 라벨 수용 확인 |

**건드리지 않는 것**: engine/·rules/·indicators/·portfolio/ 로직 전부(시장
비의존), calendar(지수 CSV 기준 그대로), analysis/(캡처 빌더는 시장 중립).

**KR 전용 스크립트 격리 확인**: scripts/walkforward_report.py 등의
`zfill(6)`·6자리 가정은 KR 분석 전용 — US 경로에서 호출되지 않음을 확인만
하고 수정하지 않는다(U5에서 US용 분석 스크립트를 별도 작성).

## 검증 게이트 (순서 고정)

1. **유닛테스트 전체 통과** (기존 42+건 포함 전부) + 신규 유닛:
   일반화된 비용 스키마(구/신 키), 신규 Market 라벨, `--index` 인자 파싱.
2. **KR 골든 비트 동일**: 수정 후 코드로 KR 풀런 1회
   (out/p5/rules_full.yaml·config/costs.yaml·2017-01-02~2026-07-10) 재실행 →
   기준 산출(out/p7adv/full_taxfix)과 trades.csv·equity.csv **비트 비교**.
   1비트라도 다르면 게이트 실패 — 원인 규명 전 진행 금지.
3. **소수 가격 스모크**: 합성 소수 가격 CSV(예: 12.34달러대)로 run_single 실행,
   정수 가정(반올림·형변환)으로 인한 예외·왜곡 없음 확인.
4. **US 배선 스모크**: U1 데이터가 있으면 소형 심볼 셋으로 run_portfolio 실행
   (rules_us·costs_us·지수 2개) — 예외 없이 완주만 확인(성능 판정은 U4).

## 산출물·DoD

- 코드 diff(위 인벤토리 범위 내) + 신규 유닛테스트
- `plan/us/u3_code.md`: 수정 목록, 게이트 1~4 결과(골든 비트 동일 증명 — 비교
  커맨드·해시 병기)
- DoD: 게이트 1~3 통과(4는 U1 완료 시). 커밋.

---

## 실행 프롬프트 (새 세션에 붙여넣기)

```
U트랙(미국장 전이 백테스트) U3 단계를 진행한다. 먼저 읽어라:
docs/us/us_plan.md(마스터), docs/us/u3_code.md(본 단계 — 수정 지점 인벤토리와
게이트), plan/us/u0_decisions.md(Q4 라벨·Q10 전략), plan/us/u2_rules.md(스키마
합의), config/rules_us.yaml, config/costs_us.yaml.

작업: u3_code.md 인벤토리의 6개 지점을 최소 diff로 일반화하고, 검증 게이트
1~4를 순서대로 수행해 plan/us/u3_code.md에 기록하라.

절대 규칙:
- KR 골든 비트 불변이 유일한 통과 게이트다. 수정 후 KR 풀런(out/p5/rules_full.yaml,
  config/costs.yaml, 2017-01-02~2026-07-10, cash 1e8)을 재실행해
  out/p7adv/full_taxfix의 trades.csv·equity.csv와 비트 비교하라. 다르면 중단·규명.
- 인벤토리 밖 파일(engine/rules/indicators/portfolio/analysis)은 수정 금지.
  필요해 보이면 수정하지 말고 사유와 함께 사용자에게 상정.
- 기존 costs.yaml·rules_v3-3.yaml이 구키 그대로 파싱되어야 한다(하위호환).
- KR 풀런은 ~3.5분·RAM 부담 — 다른 엔진 런과 동시 실행 금지.

파이썬: C:\Users\mh.han\repos\daytrading\.venv\Scripts\python.exe, PYTHONPATH=src,
-X utf8. 완료 기준: 게이트 통과 기록 + docs/PROGRESS.md 갱신 + 커밋.
```
