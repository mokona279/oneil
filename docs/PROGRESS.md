# 진행 현황 (PROGRESS)

Phase별 완료 여부와 §12 미결정 사항 확정 이력을 관리한다.
세밀한 변경 추적은 git 커밋(Phase 단위)에, "다음에 뭘 해야 하나 + 무슨 결정을 내렸나"는 이 문서에.

착수 순서: 0 → 1 → 2 → 3A → 3B → 4A → 4B → 5 → 6 → 7 → 8

## Phase 체크리스트

- [x] **Phase 0 — 골격·설정·캘린더·로더**
  - `pyproject.toml`, `config/*.yaml`, `domain/{enums,bar,config}`, `data/{datasource,csv_source,loader,calendar,metadata}`, `tests/fixtures/synthetic.py`
  - 유닛테스트 52개 green (calendar, config, datasource, loader, metadata, priceframe)
- [x] **Phase 1 — 지표** (`indicators/`)
  - MA(50/60/120/150/200), ATR(14), 52주 고저, 20일 거래대금·거래량, 20일 수익률, RS(6M), `ma200_rising`
- [x] **Phase 2 — 셋업 필터 + 시장필터** (`rules/{trend_template,overheating,rs_filter,market_filter}`)
  - 트렌드 템플릿 7조건 AND, 과열(+50% 수직상승·베이스 훅), RS 게이트, 시장 상태머신(복귀 3거래일 히스테리시스)
  - `MarketState`는 `domain/enums.py`에 추가. 게이트는 심볼별 `IndicatorSet` 주입(`passes(d)`/`excluded(d)`) — 계획서 pseudocode의 `passes(symbol,d)`는 심볼당 IndicatorSet이 이미 캐시 단위라 `symbol` 인자를 뺀 형태로 구현
  - 유닛테스트 91개 green (기존 72 + rules 19)
- [x] **Phase 3A — 베이스 감지기** (`rules/{base_detector,stage_tracker}`)
  - 전방 스캔 상태머신: 시작점(신고가)·피벗(장중 최고가)·깊이(장중 고저)·기간(달력일 7×N). 깊이 티어 15%/33% → 5주/7주, D>33% 패턴 무효·재시작
  - `base_asof(d)`는 구조값(피벗·저점·깊이)을 ≤d-1로 확정, 기간만 돌파일 d 기준(§5) — 룩어헤드 없음. `is_breakout(d,base)`=d 장중고가≥피벗
  - `StageTracker`(3훅: `on_bar`/`stage_for_new_base`/`on_breakout`) 위임 — +20% 종가랠리→단계+1, 미달 재베이스→유지, 직전베이스 저점 하회→1 리셋. 감지기는 4단계도 그대로 카운트(진입 게이트는 엔진 몫)
  - 유닛테스트 104개 green (기존 91 + base 13)
- [x] **Phase 3B — 베이스 품질** (`rules/base_quality`)
  - 진입 4요건: ①과열 미해당(OverheatingFilter 재사용) ②2×ATR≤피벗10% ③수축(직전10일 고저레인지≤피벗10%) ④드라이업(직전10일 평균거래량<베이스 전체 일평균)
  - `passes(d, base)→QualityResult`(4요건 개별 + `passed` 종합). 구조 품질은 **≤d-1** 세션만으로 확정(돌파일 d 가격·거래량 미사용 → 룩어헤드 없음). '직전 10일'·'베이스 전체'는 [start, d-1] 실거래 세션
  - 과열 요건은 `has_base=True`로 조회 — 유효 베이스가 손에 있으니 조항(a) '베이스 없이 수직상승'엔 해당 불가. v1은 (a)만 구현(§12 Q3)이라 사실상 통과, (b)(c) 데이터 확보 시 자동 반영
  - 유닛테스트 113개 green (기존 104 + quality 9)
- [x] **Phase 4A — 체결 프리미티브** (`execution/{orders,cost_model,fill_model}` + `domain/trade.py`)
  - `Fill`(비용 반영 체결 값객체), `Order`(+`OrderKind`; `breakout`/`pyramid` 팩토리로 상한 계산 일원화), `CostModel`(편도 수수료+슬리피지, 매도 시 시장·기간별 거래세 계단), `DailyBarFillModel`
  - 체결 규칙(§6.2): 1차 돌파=`max(O,피벗)`, 갭업 추격상한(+5%) 초과 시 장중 저가가 상한 복귀하면 상한 체결·아니면 미체결. 2·3차=`max(O,트리거)`, 상한(+3%) 초과 갭이면 그 회차 스킵(1차와 달리 장중 복귀 불허). 거래량 게이트 `Vol≥20일평균×1.5`
  - 슬리피지는 체결가를 흔들지 않고 비용 항목(bp)으로 반영 → 결정론. 세금 계단은 `from_date<=d`인 마지막 시행일(포함), 최초 이전이면 가장 이른 계단 방어 적용
  - 유닛테스트 131개 green (기존 113 + execution 18)
- [x] **Phase 4B — 손절·청산 규칙** (`rules/{stop_rule,exit_rules}` + `domain/trade.Position` + `execution` 청산체결)
  - `StopRule`: 손절가 = max(평단−2×ATR, 평단×(1−10%)) — 2×ATR가 평단 10% 초과 시 -10% 캡 바닥으로 클램프, 평단 상승 시 재계산. `hit(pos,d)`는 체결모델별 종가(기본)/장중저가(대안) ≤ 손절가
  - `TrendExitRule`(§6②): 60MA 이탈 종가 → 절반(HALF). 이탈 후 거래일 카운트 3 미회복 → 잔량 전량(REST), 3거래일 내 종가 회복 → `reason=None`(CLEAR)로 대기청산 취소. `volbreak_full` 시 거래량 급증 이탈은 처음부터 전량(VOLBREAK)
  - `MarketDefenseRule`(§6③): mstate=DEFENSE면 해당시장 종목 절반. 8주 보호 종목은 정지(None). 반복축소는 엔진이 `defense_triggered_on` 전이일에만 호출해 방지
  - `EightWeekGuard`(보조): 돌파 후 fast_window(3주=21달력일) 내 진입가 대비 +20% 장중 도달 & 최소보유(56일) 이내면 보호 → ③만 정지, ①② 유지. 판정은 ≤d 바만(룩어헤드 없음)
  - `ExitSignal`(decided_on/reason/qty): 판정 D, 체결 D+1 시가. `domain/trade.Position`(평단·수량·손절가·60MA이탈일 스냅샷, frozen) 신설. `Order.exit`+`DailyBarFillModel.fill_exit`: 기본 D+1 시가 전량, 손절 장중스탑(대안)은 min(O,손절가) 갭하락 반영, 매도세금 시장·기간별
  - 유닛테스트 154개 green (기존 131 + stop 12 + exit 11)
- [x] **Phase 5 — 사이저·포트폴리오·리스크거버너** (`portfolio/{position_sizer,portfolio,risk_governor}` + `domain/trade.ClosedTrade`)
  - `PositionSizer`: 비중 = min(상한20%, risk_per_trade%/손절폭%) — 손절폭%는 StopRule(§6①)과 동일 산식(2×ATR 진입가% + -10% 캡). 트랜치 수량은 자본×비중×비율/체결가 정수주(floor)
  - `Portfolio`: 현금·포지션(dict)·예약현금의 단일 소유자. `apply_buy`(신규/피라미딩 평단·손절 갱신)·`apply_sell`(부분/전량, 전량 시 예약 정리). 회계 항등식 자본=현금+평가 유지. 슬롯 `max_positions`(8)·현금 `can_open`. `reserve/release`로 2·3차 예약현금이 `available_cash`에서 빠짐(토글 `reserve_pyramid_cash`)
  - `RiskGovernor`: 연속손절 `consecutive_stops`(3)회 → `halt_days`(10거래일) 신규 차단·자동해제. 손절 아닌 청산이 끼면 카운터 리셋. 거래일 이동은 TradingCalendar 주입(없으면 달력일 근사). `enabled` 토글(§12 Q12)
  - `ClosedTrade`(§3.1) 신설: 진입·청산 체결 쌍 회계(부분청산 안분 pnl·pnl_r·hold_days·is_stop)
  - 유닛테스트 174개 green (기존 154 + portfolio 20)
- [ ] **Phase 6 — 엔진(일별 루프)** (`engine/*`, `cli/*`) ← **다음**
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
