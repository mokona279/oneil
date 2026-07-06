# 진행 현황 (PROGRESS)

Phase별 완료 여부와 §12 미결정 사항 확정 이력을 관리한다.
세밀한 변경 추적은 git 커밋(Phase 단위)에, "다음에 뭘 해야 하나 + 무슨 결정을 내렸나"는 이 문서에.

착수 순서: 0 → 1 → 2 → 3A → 3B → 4A → 4B → 5 → 6 → 7 → 8

## Phase 체크리스트

- [x] **Phase 0 — 골격·설정·캘린더·로더**
  - `pyproject.toml`, `config/*.yaml`, `domain/{enums,bar,config}`, `data/{datasource,csv_source,loader,calendar,metadata}`, `tests/fixtures/synthetic.py`
  - 유닛테스트 52개 green (calendar, config, datasource, loader, metadata, priceframe)
- [ ] **Phase 1 — 지표** (`indicators/`) ← **다음**
- [ ] **Phase 2 — 셋업 필터 + 시장필터** (`rules/{trend_template,overheating,rs_filter,market_filter}`)
- [ ] **Phase 3A — 베이스 감지기** (`rules/{base_detector,stage_tracker}`)
- [ ] **Phase 3B — 베이스 품질** (`rules/base_quality`)
- [ ] **Phase 4A — 체결 프리미티브** (`execution/{orders,cost_model,fill_model}`)
- [ ] **Phase 4B — 손절·청산 규칙** (`rules/{stop_rule,exit_rules}`)
- [ ] **Phase 5 — 사이저·포트폴리오·리스크거버너** (`portfolio/*`)
- [ ] **Phase 6 — 엔진(일별 루프)** (`engine/*`, `cli/*`)
- [ ] **Phase 7 — 리포팅** (`reporting/*`)
- [ ] **Phase 8 — 통합·회귀·문서** (`tests/integration/*`, `data_example/`)

## §12 결정사항 확정 로그

계획서 §12의 질문을 확정할 때마다 여기에 날짜·결정·근거를 기록한다. (미확정은 계획서의 제안 기본값 사용)

| Q | 항목 | 확정값 | 확정일 | 비고 |
|---|---|---|---|---|
| Q2 | 버전 태그 | `rulebook_version: v3-3` | (계획서 채택) | 문서=v3-3, 베이스=v3.2 |
| — | (미확정) | 계획서 제안 기본값 사용 | — | Q1 손절 체결시점 등 결과 영향 큰 항목 확정 요망 |

## 메모

- Python: `C:\Users\mh.han\repos\daytrading\.venv` 공유.
- 각 Phase 착수 시 계획서 §8의 "세션 시작 컨텍스트"를 붙여넣어 독립 착수.
