# U2 — 규칙·비용 미국장 번역

**목표**: `config/rules_us.yaml`·`config/costs_us.yaml`을 작성한다. 원칙은
마스터 플랜 §1-3: **v4.0 값 무변경 이식** — 단위·시장구조상 불가피한 변환만
허용하고, 전 키를 3분류(동일 유지 / 변환 / 해당 없음) 표로 고정한다.

**선행**: U0 승인 완료. U1과 병행 가능(코드·데이터 불필요, 설정 파일만).

## 작업 항목

1. **rules_us.yaml** — `config/rules_v3-3.yaml`(v4.0)을 복사해 다음만 변경:

| 키 | 처리 | 근거 |
|---|---|---|
| `trend_template.turnover_20d_min_krw` | **변환** → USD 값(U0 Q5 확정) | 통화 단위. 키 이름 처리(krw→usd)는 U3 파싱과 합의 |
| `overheating.limitup_lookback_days` | **유지(무효)** | KR에서도 미구현 필드 — 미국은 상한가 자체가 없음. 주석으로 N/A 명기 |
| 지수 관련(시장 매핑) | rules가 아닌 CLI/메타 소관 | rules_us엔 변경 없음 — U3에서 처리 |
| `rulebook_version` | `"v4.0-us"` 등 태그 | 재현성 태그 구분 |
| **그 외 전 키** | **동일 유지** | 전이 검증 원칙 — 값 변경은 U6 이후 별도 트랙 |

2. **costs_us.yaml** — 구조는 costs.yaml 재사용:
   - `commission_bp`·`slippage_bp`: U0 Q9 확정값(권장 0 / 5)
   - `sell_tax_schedule`: SEC Section 31 수수료 이력(U0 작업 2 산출)을 계단으로.
     시장 구분 키(kospi_bp/kosdaq_bp의 일반화)는 U3 스키마와 합의 — U2 시점엔
     주석으로 의도 명기, U3에서 파싱 확정.
   - FINRA TAF: U0 결정(무시 or bp 근사)을 주석으로 기록.

3. **파라미터 전수 매핑 표**: rules_v3-3.yaml의 모든 키(약 70개)를 한 줄씩
   동일/변환/N-A로 분류하고 근거 한 줄 병기 — `plan/us/u2_rules.md`의 본체.
   "동일"이 압도적 다수여야 정상(전이 검증이므로). 변환이 3개를 넘으면
   그 자체를 사용자에게 보고(전이 순도 훼손 경고).

4. **잔여 결정 질문**: 표 작성 중 발견되는 모호점(예: 8주 룰의 달력일 기준이
   미국 휴장 패턴에서 갖는 의미 변화 등)은 임의 처리 금지 — 질문 목록으로 상정.

## 산출물·DoD

- config/rules_us.yaml, config/costs_us.yaml (신규 파일 — KR 파일 무수정)
- plan/us/u2_rules.md: 전수 매핑 표 + 잔여 질문 답변 완료
- DoD: 사용자 승인(특히 변환 항목 목록) + 커밋

---

## 실행 프롬프트 (새 세션에 붙여넣기)

```
U트랙(미국장 전이 백테스트) U2 단계를 진행한다. 먼저 읽어라:
docs/us/us_plan.md(마스터 — 특히 §1-3 무튜닝 원칙), docs/us/u2_rules.md(본 단계),
plan/us/u0_decisions.md(확정 결정), config/rules_v3-3.yaml(원본 v4.0),
config/costs.yaml(원본 비용).

작업: u2_rules.md의 작업 항목 1~4. 산출물은 config/rules_us.yaml,
config/costs_us.yaml, plan/us/u2_rules.md(전수 매핑 표) 세 파일이다.
코드는 작성하지 않는다.

핵심 원칙: v4.0 파라미터 값을 바꾸지 않는다. 바꾸는 것은 단위·시장구조상
불가피한 항목뿐이며, 그 목록이 3개를 넘으면 이유와 함께 사용자에게 경고하라.
"미국장에 더 맞을 것 같은 값"으로의 조정은 금지 — 그건 U6 이후 별도 트랙이다.

제약: config/rules_v3-3.yaml·costs.yaml 등 KR 파일 무수정(복사 원본으로만 사용).
모호점은 임의 가정 없이 질문 목록으로 상정.

완료 기준: 세 파일 + 매핑 표 + 잔여 질문 전부 사용자 답변 반영 →
docs/PROGRESS.md 갱신, 커밋.
```
