# U5 — 강건성 검증 (KR P5~P7 잣대 재적용)

**목표**: U4 베이스라인에 KR 트랙이 통과한 것과 동일한 강건성 잣대를 적용한다.
특히 **KR 튜닝 축들의 on/off 델타 부호**가 핵심 산출물이다(마스터 플랜 §5 2차
기준 — 전이 판정의 본체).

**선행**: U4 완료. 배치 총량이 크므로 야간 분리 실행 전제.

## 분석 목록 (KR 대응 관계 명시)

| # | 분석 | KR 대응 | 방법 |
|---|---|---|---|
| 1 | **축 on/off 암 런** | P5 9암 | rules_us에서 축별 off 설정 생성(R3b→노리셋, Q11→클램프 off, R4b→재진입 off, confirm5→3, W1→3, slots12→8, reserve 복원) — KR AXES 구성 그대로. 델타 부호를 KR과 대조 |
| 2 | 연도 슬라이스 워크포워드 | P5 | 연 단위 창별 성적 — 특정 연도 의존 여부 |
| 3 | 절단 재계산 | P7 §3·P8-3 | 마지막 6~12개월 절단 시 축 델타·캡처 유지 여부 |
| 4 | 시작점 앙상블 | P7 §8·P8-2 | 최소 5점(연 단위), 여유 시 월 단위 — gap 분포 |
| 5 | 비용 민감도 | P7 §7 | 슬리피지 15/30/50bp — 엣지 생존선 |
| 6 | 시장필터 지수 스트레스 | P6 | `scripts/market_filter_stress.py` — `--index SP500=path --index NASDAQ=path`로 **무수정 재사용 가능**(인자 일반형 확인됨). 2000~현재(닷컴·2008 포함 — 지수 종가 단독이라 생존편향 무관) |
| 7 | 데이터 말단 이상 점검 | P7 §5 | 최근 6개월 수집분 재수집 대조(수정주가 소급 변동·세션 결측) |

**우선순위**: 1 > 6 > 5 > 2 > 3 > 4 (1이 전이 판정의 핵심 증거, 6은 지수만
필요해 값싸고 생존편향 무관 — 먼저 돌려도 됨).

**분석 스크립트**: scripts/walkforward_report.py는 KR 전용(zfill(6)·KR 암 경로)
— 수정하지 말고 `out/us/p5/` 아래 US용 스크립트를 새로 작성(구조는 미러).

## 실행 계획 (리소스)

- 암 런 7~9개 × 풀런 시간(U4에서 실측된 값으로 추정 갱신) — 순차 배치 .cmd
  생성(ASCII·per-런 로그·FAILED 계속·DONE 마커), detached 실행, Monitor 감시.
  KR P8-2 배치(out/p7adv/make_mstart.py)가 검증된 템플릿.
- 시작점 앙상블은 암 런과 별개 배치 — 같은 날 겹치지 않게.

## 산출물·DoD

- out/us/p5/ (암 산출·집계 CSV·스트레스 리포트)
- `plan/us/u5_robustness.md`: 분석 1~7 각각의 방법·결과·판정 + **축별 델타
  부호 대조표(KR vs US)** — U6의 직접 입력
- DoD: 최소 분석 1·2·5·6 완료(3·4는 1 결과가 애매할 때 필수로 승격).
  결과가 나빠도 DoD 무관(측정 단계).

---

## 실행 프롬프트 (새 세션에 붙여넣기)

```
U트랙(미국장 전이 백테스트) U5 단계를 진행한다. 먼저 읽어라:
docs/us/us_plan.md(마스터 — §5 성공 기준), docs/us/u5_robustness.md(본 단계),
plan/us/u4_baseline.md(베이스라인·풀런 시간 실측), 참고로 plan/p5_walkforward.md·
plan/p7_adversarial.md(KR에서 같은 분석을 수행한 형식과 잣대).

작업: u5_robustness.md 분석 목록을 우선순위 순서(1→6→5→2→3→4→7)로 수행하고
plan/us/u5_robustness.md에 기록하라. 핵심 산출물은 축별 on/off 델타의
KR vs US 부호 대조표다.

제약:
- 배치는 전부 분리 프로세스(ASCII .cmd + per-런 로그 + FAILED 계속 + DONE 마커
  — out/p7adv/make_mstart.py가 템플릿). RAM 7.4GB — 배치 간 동시 실행 금지,
  KR 데일리 런 시간대 회피. 완료 대기는 Monitor로.
- scripts/walkforward_report.py 등 KR 분석 스크립트 수정 금지 — US용은
  out/us/p5/ 아래 신규 작성.
- rules_us 값 수정 금지. 암 생성은 축 off 파생 설정만.
- 결과 해석에 생존편향 캐비앗 병기. 절대 수치가 아니라 축 델타 부호·상대
  비교가 본체임을 기록 서두에 명시.

파이썬: C:\Users\mh.han\repos\daytrading\.venv\Scripts\python.exe, PYTHONPATH=src,
-X utf8. 완료 기준: DoD(최소 1·2·5·6) + 기록 + PROGRESS 갱신 + 커밋.
```
