# 포워드 검증 진행 현황

매 데일리 런이 그날의 신호를 결과를 알기 전에 봉인한 원장의 요약이다.
이 파일은 매 실행 재생성된다 — 원본은 sessions.csv / signals.csv (append-only).

- 기록 세션: **1개** (2026-07-16 ~ 2026-07-16)
- 누적 신호: actionable 0 · watch 0
- 방어(DEFENSE) 세션: 1

| asof | KOSPI | KOSDAQ | 즉시매수 | 관심 | 기록 시각 |
|---|---|---|---|---|---|
| 2026-07-16 | CAUTION | DEFENSE | 0 | 0 | 2026-07-19T15:20:10 |

검증 방법(데이터가 쌓인 뒤): signals.csv의 actionable 신호별로 이후 실제
주가(피벗 대비 경과·손절 도달 여부)를 대조한다. 원장 봉인을 위해 매 런 후
`git add forward && git commit`을 권장한다.
