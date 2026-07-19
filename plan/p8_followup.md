# P8 — P7 적대적 리뷰 후속 3건: 포워드 원장·시작점 전수·캡처 절단

**상태**: 진행 중 (P8-1·P8-3 완료, P8-2 실행 중) · 지시 2026-07-19 사용자
"코드 커밋. 1,2,3은 큰 수정 없이 진행 가능한가? … 진행"
**전제**: 규칙(rulebook v4.0)·엔진·골든 불변. 코드 추가는 scripts/forward_ledger.py와
daily.ps1 훅뿐 — 백테스트 경로는 건드리지 않는다.

## §1 범위

P7(plan/p7_adversarial.md) 완료 시 문서화한 후속 후보 중 사용자가 승인한 3건:

| # | 항목 | 성격 | 상태 |
|---|---|---|---|
| P8-1 | 포워드 섀도 원장 | 신규 코드(스크리너 불변, 후처리 훅) | **완료** |
| P8-2 | 월 단위 시작점 전수 스윕 | 재계산(102런, 분리 프로세스) | 실행 중 |
| P8-3 | 캡처율(≥4× 티어) 2025 절단 재계산 | 재계산(기존 산출물 후처리) | **완료** |

## §2 P8-1 포워드 섀도 원장 — 유일한 진짜 OOS 수단 가동

**설계** (승인 조건: "데일리 런 돌릴 때마다 포워드 검증 진행 문서가 생기는 것"):
- `scripts/forward_ledger.py` — 데일리 스크린 산출물(buy_candidates.csv)을 후처리로
  읽어, 세션당 시장필터 상태·후보 목록을 **결과를 알기 전에** 기록. 스크리너는 무수정.
- `daily.ps1`에 [2b/3] 단계 삽입 — 스크리닝 직후 자동 호출. daily.bat·daily_run.cmd
  모두 daily.ps1 경유라 커버리지 완전.
- 원장은 `forward/` (gitignore 밖 → 커밋 가능): `sessions.csv`(세션당 1행: 필터 상태,
  후보 수, 규칙 파일 sha8) + `signals.csv`(후보당 1행: actionable/watch) +
  `status.md`(진행 현황, 매 런 재생성). **append-only·선기록 우선** — 같은 세션 재실행은
  추가 없음. 신선도 가드: 후보 파일이 지수 데이터보다 오래되면 기록 거부.
- 개인 상태(state/ 현금·보유)는 원장에 넣지 않는다 — 전략 신호만.

**첫 기록** (2026-07-19 봉인): 세션 2026-07-16 — KOSPI CAUTION·KOSDAQ DEFENSE
(양 시장 신규진입 불허), actionable 0 · watch 0. 후보 469행 전수에서 all_gate=0 확인
— 필터 폐쇄와 정합. 기록 무결성은 forward/ 커밋 이력이 봉인한다.

**검증 방법(데이터 축적 후)**: signals.csv의 actionable 신호별 이후 실제 주가 경과
(피벗 대비 수익·손절 도달)를 대조 — 백테스트를 거치지 않은 전략 성과 문서가 된다.

## §3 P8-3 캡처율(≥4× 티어) 2025-12-31 절단 재계산 — 채택 지표 소멸 확인

**질문**: slots12(Q13)·W1(P4-ext) 채택의 1차 지표였던 ≥4×·turnover_ok 캡처율이
2026H1(데이터 이상 플래그 창)을 제외해도 성립하는가.

**방법** (`out/p7adv/capture2025.py`):
1. 전 종목(2,580) 1패스로 캡처 세트를 두 창(전체 ~2026-07-10 / 절단 ~2025-12-31)
   동시 재구축. **빌더 검증**: 전체창 재계산 vs out/p0/capture_set.csv — 심볼·달성일·
   유동성·세션 완전 일치(2,292행), max_multiple 미세 드리프트 3행(≤0.001, p0 이후
   재수집분의 소급 수정주가, 티어 배정 불변).
2. out/p5 9암 trades를 exit≤2025-12-31로 절단(인과성은 P7 §3.1에서 비트 실증).
   컷오프 미청산 포지션 제외는 전 암 동일 조건. build_capture_report(정본 모듈)로 집계.

**결과** (out/p7adv/capture_tiers.csv·capture_axis_2025.csv). 절단 세트: 달성 2,231 /
turnover_ok 1,849 (전체창 2,292/1,923 — 74종목은 2026에만 달성). ≥4× 티어 모수
전체창 1,003 → 절단 882.

| 축 | ≥4× delta (전체창) | ≥4× delta (절단) | 해석 |
|---|---|---|---|
| slots12 (vs slots8) | **+0.20pp** (69 vs 67종목) | **0.00pp** (54 vs 54) | 채택 근거였던 +2종목이 전부 2026H1 포착분 — 절단 시 소멸 |
| W1 (vs w3) | 0.00pp | **-0.12pp** (-1종목) | 절단 창에선 오히려 열위. capture_sum_r도 열위(1,584 vs 1,588) |
| R4b | -0.50pp | -0.12pp | off가 원래 우위(재진입의 슬롯 경합) — 방향 유지 |

**판정**: Q13 문서 스스로 "12번째 슬롯은 s1 무접촉, s2에서만 캡처 +2건"이라 적었고,
본 실측은 그 +2건이 **s2 중에서도 2026H1**임을 확정했다. 캡처 지표 기준으로 slots12·
W1의 '정확한 값' 선택을 지지하는 증거는 절단 창에 존재하지 않는다 — P7 절단 분석
(수익 delta), PBO-lite(순위 상관)와 **3개 독립 방법이 같은 결론**. 단 delta의 절대
크기는 ±2종목(모수 882~1,003) 수준으로 어느 방향으로도 통계적 힘이 없다 — 채택
유지의 실질 근거는 P5-5(창 내 무해)와 논리이며, 캡처 지표는 이제 근거 목록에서
제외해야 한다(규칙 변경 없음, 증거 재분류).

## §4 P8-2 월 단위 시작점 전수 스윕 — 실행 중

**질문**: P7 §8(5점 앙상블)의 2021-01 시작 -227.7pp 괴리가 꼬리(특이점)인지
분포(시작점 운의 상시 크기)인지.

**설계**: 2017-01~2025-06 매월 1일 시작(엔진이 첫 세션으로 스냅) × end 2026-07-10,
rules=out/p5/rules_full.yaml, costs=config/costs.yaml(2026 세율 수정본) — 102런.
gap_pp = 신규 계좌 총수익률 - 기준 경로(out/p7adv/full_taxfix) 동일창 수익률.
- 생성: out/p7adv/make_mstart.py → mstart_runs.cmd (런당 ~3.5분, 순차 단일 프로세스)
- 실행: 분리 프로세스(cmd.exe Hidden), 실패 런은 FAILED.txt에 기록 후 계속
- 분석: out/p7adv/mstart_analyze.py → mstart_ensemble.csv + 분포 요약

**앵커 검증(부분 결과)**: m2017-01 = 기준 경로와 비트 동일(+754.8%, 367트레이드,
gap 0.0) — 배치·비용 정합 확인. 1월 무거래로 m2017-02도 동일.

**결과**: (실행 완료 후 기입)

## §5 재현

```
# P8-1 (데일리 런이 자동 수행; 수동 실행 시)
python -X utf8 scripts/forward_ledger.py --candidates out/daily/<date>/buy_candidates.csv \
  --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
  --rules config/rules_v3-3.yaml --costs config/costs.yaml --out forward

# P8-3
python -X utf8 out/p7adv/capture2025.py     # ~2분 (2,580종목 1패스 ×2창)

# P8-2
python -X utf8 out/p7adv/make_mstart.py     # 배치 생성
out\p7adv\mstart_runs.cmd                    # ~6시간 (분리 실행 권장)
python -X utf8 out/p7adv/mstart_analyze.py
```

파이썬은 `C:\Users\mh.han\repos\daytrading\.venv\Scripts\python.exe` (PYTHONPATH=src).
data/·out/p0/·out/p5/ 읽기 전용. 산출은 out/p7adv/·forward/ 만.
