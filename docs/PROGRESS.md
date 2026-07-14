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
- [x] **Phase 6 — 엔진(일별 루프)** (`engine/{context,engine}` + `cli/{run_single,run_portfolio}`)
  - `BacktestEngine.run(start,end,symbols=None)`: 하루를 §6.3 순서로 처리 — ①대기청산 체결(전일 종가결정→당일 시가) ②장중 자동스탑(대안모델) ③피라미딩 2·3차(장중, 시장필터 무관 Q11) ④신규 돌파진입(≤d-1 게이트+장중 돌파, RS 내림·심볼 사전순 정렬) ⑤청산판정(종가: 손절·60MA·방어→d+1 대기) ⑥자본곡선 기록
  - 조립은 `context.py`의 `build_symbol_context`/`build_market_context`(지표 사전계산 캐시). `TradePlan`이 포지션의 경로의존 진입상태(피벗·목표명목·트랜치진행·예약현금·1주당리스크)를 소유 — `Position`(값객체)이 담지 않는 "다음 트랜치를 어떻게 살까"
  - 사이징·손절 재계산 ATR·진입 게이트는 **직전 세션(d-1)** 값 사용. 돌파 판정만 d 장중 고가. 룩어헤드 회귀 테스트로 보증(미래 바 조작이 그 이전 자본곡선 불변)
  - 돌파일 거래량 게이트(1.5×) 통과 시에만 2·3차 예약(§6.1), 실패 시 VOL_FAIL·피라미딩 없음. 트랜치 체결마다 예약 release·평단/손절 갱신
  - 단일종목=`symbols=[sym]`, 포트폴리오=생략(전체). 동일 엔진, 유니버스 크기만 다름. `BacktestResult`(자본곡선·트레이드·이벤트) 산출 → Phase 7 입력
  - **계획서 대비 변경**: `engine/pipeline.py`는 별도 분리 대신 엔진 본체의 `_process_*` 절차로 통합(과분할 회피). 결과 자료구조는 `context.py`에 병치
  - 유닛테스트 180개 green (기존 174 + engine 6: 단일종목 진입·자본곡선, 피라미딩, 결정론 2회동일, 슬롯상한, 룩어헤드 가드, 무신호 자본보존)
- [x] **Phase 7 — 리포팅** (`reporting/{writer,trade_log,equity_curve,event_list,metrics,report}` + CLI `--out`)
  - §9 출력 4종: 트레이드 로그 CSV(진입·청산 매칭 1행, `trade_id`=(심볼,진입일) 그룹핑·부분청산 동일 id), 일별 자본곡선 CSV(`market_state`는 `KOSPI=NORMAL;…` 시장 사전순 직렬화), 육안검증 이벤트 CSV(detail에서 pivot/depth/weeks/stage 추출), 성과지표 `metrics.txt`+`metrics.json`
  - `PerformanceMetrics`(순수·결정론): 총수익·CAGR(자본곡선 첫~끝 달력일 연환산)·MDD(최고점 대비 최대낙폭)·승률·손익비(평균이익/|평균손실|)·기대값R(pnl_r 평균)·평균보유·평균노출·총비용(진입안분+청산)·청산분해(손절/60MA/방어). 무트레이드·자본≤0 방어
  - CSV는 `utf-8-sig`+`\n` 고정(`writer.write_csv`) → 엑셀/한글 호환 & 골든파일 재현성. `write_report(result, out_dir)`가 4종 기록 후 `Report`(지표+경로) 반환, CLI `--out`가 재사용
  - 유닛테스트 186개 green (기존 180 + reporting 6: 손계산 대조·CAGR 연환산·청산분해·무트레이드 방어·CSV 스키마·trade_id 그룹핑)
- [x] **Phase 8 — 통합·회귀·문서** (`tests/integration/*`, `data_example/`, `generate.py`, README)
  - `data_example/`: 결정론 생성기(`generate.py`, 난수 없이 순수 함수) + 산출 CSV. 3종목(KOSPI 승자·KOSPI 손절·KOSDAQ 승자)×320세션. 거래대금 200억으로 트렌드 템플릿 100억 게이트 통과. 시나리오가 돌파진입·피라미딩·손절·60MA청산을 자극
  - `tests/integration/test_smoke.py`(6): CsvDataSource 로드→엔진 완주, 자본곡선 범위·정렬·무중복, **회계 항등식**(equity=cash+holdings) 일별 검증, 노출도 [0,100], 진입·피라미딩·손절 이벤트 발생, 리포트 4종 산출물 스키마(metrics.json 키·행수 대조), 단일종목 모드 완주
  - `tests/integration/test_golden.py`(3): 2회 실행 비트동일, **골든 SHA-256 다이제스트** 고정(자본곡선·트레이드·이벤트 직렬화 → 회귀 감시), 파라미터 민감도(비중상한 20%→5% 시 결과·최대노출 변화 → 배선 확인)
  - 골든 데이터/규칙 변경 시 `GOLDEN_DIGEST` 갱신 필요. CLI 직접 실행은 `PYTHONPATH=src`(pytest는 pyproject `pythonpath`로 자동). README에 실행 예시·현재 상태(전 Phase 완료) 반영
  - 유닛+통합 195개 green (기존 186 + integration 9)

## 후속 과제 (계획서 §11 — v1 이후)

- [x] **파라미터 민감도 스윕 하니스** (`analysis/{override,sweep}` + `cli/run_sweep`)
  - `apply_overrides(cfg, {"sizing.max_weight_pct": 5.0, ...})`: frozen Config 트리를 점 경로로 치환한 **새 Config** 반환(원본 불변). `dataclasses.replace`를 경로 각 단계 재귀 적용. 잘못된 경로/스칼라 하강은 `OverrideError`로 즉시 실패(오타=조용한 no-op 스윕 방지). 값은 기존 필드 타입에 맞춰 Enum·bool·float·tuple만 최소 보정
  - `ParameterGrid.from_mapping({축: [값,...]})` → `itertools.product`로 결정론적 조합. `run_sweep`이 조합마다 오버라이드 적용한 새 엔진 실행·`compute_metrics` 수집(base cfg·source 불변, 지표 캐시가 config 의존이라 조합 간 미공유). `SweepResult.ranked(지표)`로 정렬, `write_sweep_csv`는 축 열+지표 열 조합당 1행(리포팅과 동일 `write_csv` utf-8-sig)
  - CLI `run_sweep`: `--param 점경로=v1,v2`(반복) + `--grid YAML` 병합, `--sort` 지표 랭킹 콘솔표, `--out` CSV. 값은 int→float→bool→str 순 파싱. `run_portfolio.build_source` 재사용
  - 유닛+통합 220개 green (기존 195 + unit 19: override 정확성·불변·타입보정·오류, 그리드 조합, 표 스키마 + integration 6: 조합수·결정론·민감도·오버라이드 무해성·CSV 스키마)
  - **남은 후속**: 워크포워드/롤링 검증, 몬테카를로 트레이드 순서, API 데이터소스 교체, 펀더멘털·수급 통합, 생존편향 보정

## 전략 보강 트랙 (strategy_enhancement_plan.md §8)

- [x] **P0 — 베이스라인 고정** (2026-07-13, 세부: `../plan/p0_baseline.md` 실행 기록)
  - 전 유니버스 수집 완료: 2,579종목 × 2015-10-01~2026-07-10, 배치 6개 실패 0. 전 종목 로더 통과·meta 정합(`out/p0/data_validation.txt`)
  - `analysis/capture.py`(캡처 세트 추출) + `analysis/capture_report.py`(캡처율·병목 집계) + 스윕 `capture_rate`·`capture_sum_r` 컬럼. 유닛+통합 272개 green (기존 261 + capture 6 + capture_report 5)
  - v3-3 베이스라인(2017-01-02~2026-07-10, 1억): **+118.99% / CAGR +8.59% / MDD -12.41% / 187트레이드 / 캡처율 3.2%** → `out/baseline_v3-3_2017_2026/`, 실행 243초
  - 발견: 미진입 병목 81%가 트렌드 템플릿(`gate_trend_ok`), 캡처율은 배수 티어 무관 ~3% 평평. Q8 재상정 → **(b) 확정(2026-07-13)**: 세트 정의 유지, P1~ 1차 정렬 지표 = **≥4× 티어 캡처율**(896종목, 베이스라인 3.0%)
- [x] **P1 — R1+R2 (수축 상대화 + 템플릿 완화) + k·룩백 스윕** (2026-07-14 완료·승인, 세부: `../plan/p1_gates.md`)
  - 구현: `quality.contraction_atr_mult`(R1: 임계 max(피벗10%, k×ATR@d-1))·`trend.ma200_rising_lookback_alt`(R2a: 룩백 OR) 신규 키, 기본 null=현행 비트 동치(골든 불변). R2b는 기존 키 스윕. `run_sweep` CLI `--capture-set/--capture-tier` + `--param` null 파싱. 유닛+통합 278개 green (기존 272 + 6)
  - 전 유니버스 16조합 스윕(66분): **k=5·alt5가 파레토 우위** — 캡처율(≥4×, 1,003종목) 2.99%→6.08~7.08%, 총수익 +119%→+431~462%, MDD -12.4%→-13.6~-15.3%, 기대값R 3.84→4.8~5.3(손절 비율 증가 없음). k 플래토 중앙=5(6은 캡처 +0.1%p에 MDD +3.2%p)
  - 검증: 분할(17-21/22-26) 랭킹 상관 0.92/0.84(과적합 신호 없음), 4-레짐 귀속 — 약세장(22-23)에서 오히려 -9.4R→+256.3R 개선, 비용은 코로나 전 횡보장 MDD(-10.4→-14.2%). 가드레일: 삼성 17·23 트레이드 보존, 25-09 트레이드는 자본 경합으로 12-23 이동(원인 규명, §5 Q13 신설 — 자본 제약은 P2 이후 상정)
  - **승인(2026-07-14)**: **적극 후보(k=5·alt5·밴드25) 채택** — Q4(밴드 25%) 확정과 정합. config 기본값 반영·`rulebook_version: v3-4`, 골든 불변 확인(합성 시나리오 둔감), 기본값 의존 테스트 5건 명시적 오버라이드 전환. 유닛+통합 280개 green. Q2=k=5 확정, 계획서 §5 Q13(자본 제약) 신설
- [x] **P2 — R3 (단계 리셋 + 4단계 감액) + Q11 클램프** (2026-07-14 완료·**조건부 승인**, 세부: `../plan/p2_stage.md`)
  - 구현: `base.stage.overlimit_weight_factor`(R3a 감액)·`reset_no_breakout_months`+`reset_min_depth_pct`(R3b 리셋)·`stop.no_lower_recalc`(Q11) 신규 키, 기본 null/false=현행 비트 동치(골든 불변). `StageTracker` 새 사이클 리셋 훅(`stage_for_new_base(d, depth)`), 엔진 감액 진입·손절 클램프. 유닛+통합 291개 green (기존 280 + 11)
  - 전 유니버스 8조합 스윕(전체+분할 2회, `out/p2/`): **R3b N=12가 정점** — 캡처(≥4×) 7.08% 동일, 총수익 +462.6→+475.1%, MDD -15.31% 전 조합 불변, 손절 -1건. **R3a(감액 0.5)는 전 조합 역효과 — 실측 기각**(캡처 -0.3%p·수익 -60~75%p, 자본 경합이 캡처 승자를 밀어냄 — Q13 메커니즘 실증). 분할 총수익 순위 상관 0.824
  - 존재 증명 완결: 후보(Q11+N=12)에서 **하이닉스 2025-12-30 진입 성사**(+9.58R, 계좌 +8.0%p) — v3-4 대비 순변화가 이 1건 교체. 2025-06-05는 all_pass·자본 경합 미체결(Q13 지속), 09-11은 수축만 실패(P4 몫). 삼성 3개 트레이드 손익 비트 동일 보존. Q11 격리: 083310 1건 조기 손절 개선(+0.6%p)
  - **승인(2026-07-14, 조건부)**: Q11 + R3b(N=12) 채택 — config 반영·`rulebook_version: v3-5`, 골든 불변 확인, 기본값 의존 테스트 3건 갱신. R3a 미채택 → Q5 (a)부분 재상정(Q13 이후). **조건: 실측 효과 표본이 9.5년 2건뿐 → 과적합 가능성 상시 추적**(매 Phase 리셋·클램프 발동 분리 집계 병기, P5 워크포워드 on/off 재검증, 데이터 누적 시 재측정 — `p2_stage.md` 승인·반영 절)
- [x] **P3 — R4a (핸들 피벗) — 구현·스윕 완료 → null-keep 결정** (2026-07-14, 세부: `../plan/p3_handle.md`)
  - 구현: `base.handle.{min_sessions,max_depth_pct}` 신규 키(기본 null=현행 절대 고점 피벗, 골든 불변·전 유니버스 핸들-off 행 v3-5와 비트 동일). `base_detector`가 최종 저점 이후 회복 랠리 정점(절대 고점 미만)의 얕은 눌림을 손잡이 피벗으로 인정 — 진입 피벗만 대체, 구조(깊이·티어·R3b 리셋)·스캔 돌파는 절대 고점 유지. 룩어헤드 없음(≤d-1 확정). **§3.3 발동 추적 인프라 신설**: 반사실 단계(`stage_no_reset`, 섀도 트래커)·`rule_activations.csv`·`analysis/activation_report.py`. 유닛+통합 306개 green
  - 스윕: 12조합(min_sessions {null,3,5,10}×depth {8,12,16}) 1차 소견은 **캡처 전 조합 감소** → 확장 스윕(10/15/20)에서 **min_sessions=15 캡처 무손실·20 캡처 초과(+1)** 확인(짧은 손잡이가 저품질 조기진입 남발). 후보 **min_sessions=20/12**: +504.13% / MDD -15.31%(=베이스) / ≥4× 캡처 7.2%(+1) / 기대값 5.04 / 311트레이드 (`out/p3/candidate_handle20`)
  - **정직한 인과**: 핸들 9.5년 전 유니버스 **단 1회 발동**(삼성 2023-05-19). 직접 효과 +0.26%p(≈0). 캡처 +1·수익 +29%p는 **그 1건이 촉발한 자본 경로 캐스케이드**(→047050 +94R 포착, bear 22-23 레짐 집중). 가드레일 청정(하이닉스 비트 동일·삼성 2023만 3일 조기). §3.3 트윈: R3b +8.97%p(방향 P2와 일치)·**Q11 -3.73%p(P2 +0.6%p 대비 부호 반전 — 자본경로 의존 단일사건)**
  - **결정(사용자, 2026-07-14): null-keep — Q13 후 재상정**. 이득이 자본경합 나비효과(Q13 병목 부산물)·단일사건·s1-s2 상관 0.38이라 **Q13(자본 경합) 해소 전에는 핸들 순수 엣지를 깨끗이 캘리브레이션 불가**(R3a 선례와 정합). config 기본 null 유지(rulebook v3-5 미변경), 코드·키·발동 추적 인프라 잔존 → Q13 재스윕. 규칙서 §4/§5 모순은 "절대 고점 피벗 운용·손잡이 피벗 유보"로 문서 해소
- [ ] **P4 — R4b (재진입) 설계 문서 → 구현**
- [ ] **P5 — 통합 워크포워드 + v4 확정**
- [ ] **F트랙 — F1(수집 배치·캐시) / F2(스크리너) / F3(매도 판정) / F4(운용 리포트)**

## §12 결정사항 확정 로그

계획서 §12의 질문을 확정할 때마다 여기에 날짜·결정·근거를 기록한다. (미확정은 계획서의 제안 기본값 사용)

| Q | 항목 | 확정값 | 확정일 | 비고 |
|---|---|---|---|---|
| Q2 | 버전 태그 | `rulebook_version: v3-3` | (계획서 채택) | 문서=v3-3, 베이스=v3.2 |
| — | (미확정) | 계획서 제안 기본값 사용 | — | Q1 손절 체결시점 등 결과 영향 큰 항목 확정 요망 |

## 메모

- Python: `C:\Users\mh.han\repos\daytrading\.venv` 공유.
- 각 Phase 착수 시 계획서 §8의 "세션 시작 컨텍스트"를 붙여넣어 독립 착수.
