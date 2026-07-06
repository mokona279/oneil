# 주도주 추세추종 매매규칙서 백테스트 — 구현 계획서

- **규칙 단일 진실 원천**: `oneil_strategy.md` (문서 표제 **v3-3**, 베이스 규칙 섹션 표제 v3.2)
- **계획서 상태**: 승인 대기. 이 세션은 계획만 산출하며 코드는 작성하지 않는다.
- **대상 언어/도구**: Python 3.11+, pandas/numpy, pytest, PyYAML(설정), (선택) pyarrow.
- **작성 관점**: 파이썬 퀀트 백테스트 아키텍트. §7 아키텍처 요구사항을 기본으로 하되 대안·트레이드오프를 병기한다.

> ⚠️ **먼저 §12(결정 필요 질문)와 §13(규칙서-요약 차이)을 읽어라.** 손절 체결 시점 충돌, 펀더멘털/대장주 데이터 부재 등 결과에 큰 영향을 주는 미결 사항이 있다. 이 계획서는 규칙서를 기본 해석으로 채택하되 충돌·모호점을 모두 config 파라미터화하고 질문으로 남겼다.

---

## 0. 백테스트가 모델링하는 범위 (경계 먼저 못박기)

규칙서는 **코어-새틀라이트** 구조(A안)다. 코어(미국·글로벌 지수 ETF, 60~80%)는 이 규칙서의 매매 대상이 아니다(연 1~2회 리밸런싱). **백테스트는 위성 슬리브(한국 주도주)만 모델링**한다.

- 백테스트의 "계좌"(equity) = **위성 운용 자금 전체 = 100%**. 코어 비중 20~40% 대 60~80% 메타배분은 백테스트 대상 밖.
- 따라서 시장필터 "방어: 주식 비중 50% 이하"는 **위성 슬리브 내부**의 축소로 해석한다.
- 레버리지·인버스·ETF·신용·미수는 규칙서상 금지 → 유니버스/주문에서 원천 배제.

---

## 1. 목적과 성공 기준

**목적 (프롬프트 §2)**
1. 규칙을 기계적으로 실행했을 때의 수익률·리스크 특성 검증.
2. 규칙 간 상호작용 점검: 과열 제외 ↔ 베이스 판별, 손절 캡 ↔ 포지션 사이징, 8주 룰 ↔ 시장방어, 동일일 다중신호 ↔ 슬롯/현금 제약.
3. 이후 파라미터 민감도 분석이 가능한 구조 확보(모든 수치 외부화 + 결정론).

**이 계획서의 성공 기준**
- 각 Phase가 **선행 Phase의 산출 파일·인터페이스·테스트만으로** 독립 착수 가능.
- 규칙서의 모든 매매 규칙이 §7 "규칙→컴포넌트 매핑" 표에서 정확히 하나의 소유 컴포넌트로 추적됨.
- 룩어헤드 없음 / 결정론 / 비용 반영이 설계에 내장.

---

## 2. 전체 아키텍처 개요

### 2.1 설계 원칙 (프롬프트 §7 준수)

1. **1 클래스 = 1 책임 = 1 파일.**
2. **의존성 주입 + 추상 인터페이스 선행.** 데이터 소스(`DataSource`), 지표(`Indicator`), 체결 모델(`FillModel`) 등은 `typing.Protocol`(구조적 서브타이핑, 상속 강제 없음)로 계약을 먼저 고정한다. `CsvDataSource` → 이후 API 소스 교체 가능.
3. **파라미터 전면 외부화.** 규칙의 모든 수치는 `config/rules_v3-3.yaml`. 코드 내 하드코딩 금지. config에 `rulebook_version` 태그로 재현성 확보.
4. **Phase 독립성.** Phase 간 계약은 인터페이스로 고정, Phase마다 유닛테스트를 남긴다.

### 2.2 아키텍처 스타일 결정: 지표 벡터화 + 일별 이벤트 루프 (하이브리드)

| 방식 | 장점 | 단점 | 채택 |
|---|---|---|---|
| 완전 벡터화 | 빠름 | 피라미딩·손절 평단 갱신·슬롯/현금·연속손절 쿨다운 등 **경로 의존 상태**를 표현하기 어렵고 룩어헤드 유입 위험 | ❌ |
| 완전 이벤트 루프 | 규칙 표현 직관적, 룩어헤드 통제 쉬움 | 지표 반복계산 시 느림 | 부분 |
| **하이브리드 (채택)** | 지표는 심볼별 1회 벡터 계산·캐시(과거 포함 롤링이라 룩어헤드 없음), 트레이드 라이프사이클만 일별 루프 | 두 패러다임 혼재 | ✅ |

**근거**: 이 전략의 핵심 난이도(분할매수 평단·손절 재계산, 8종목/현금, 동일일 우선순위, 베이스 스테이지 상태)는 전부 경로 의존적 → 이벤트 루프가 정답. 지표는 순수 함수라 사전 벡터화로 성능 확보. 유니버스가 수천 종목·수년이면 심볼별 지표는 병렬화 가능(결정론 유지: 정렬 고정).

### 2.3 레이어와 의존 방향 (텍스트 클래스 다이어그램)

의존은 항상 위→아래(고수준→저수준). 순환 없음. `domain`은 아무것도 의존하지 않는다.

```
                         ┌───────────────┐
                         │     cli       │  run_single / run_portfolio
                         └──────┬────────┘
                                │
                         ┌──────▼────────┐
        ┌────────────────┤    engine     ├────────────────┐
        │                │ BacktestEngine│                │
        │                │  DailyContext │                │
        │                └──────┬────────┘                │
        │                       │                          │
 ┌──────▼──────┐        ┌───────▼───────┐          ┌──────▼──────┐
 │  reporting  │        │   portfolio   │          │    rules    │
 │ TradeLog    │        │ Portfolio     │          │ MarketFilter│
 │ EquityCurve │        │ PositionSizer │          │ TrendTmpl   │
 │ Metrics     │        │ RiskGovernor  │          │ Overheating │
 │ EventList   │        └───────┬───────┘          │ RsFilter    │
 └──────┬──────┘                │                  │ BaseDetector│
        │               ┌───────▼───────┐          │ BaseQuality │
        │               │   execution   │          │ StageTracker│
        │               │ FillModel     │          │ StopRule    │
        │               │ CostModel     │          │ ExitRules   │
        │               │ Orders        │          └──────┬──────┘
        │               └───────┬───────┘                 │
        │                       │                  ┌──────▼──────┐
        │                       │                  │ indicators  │
        │                       │                  │ MA/ATR/HiLo │
        │                       │                  │ RS / IndSet │
        │                       │                  └──────┬──────┘
        │                       │                         │
        │                ┌──────▼─────────────────────────▼──────┐
        │                │                data                    │
        │                │ DataSource(Proto) CsvDataSource        │
        │                │ CsvBarLoader  TradingCalendar  MetaRepo │
        │                └───────────────────┬────────────────────┘
        │                                    │
        └────────────────────┬───────────────┘
                     ┌────────▼────────┐
                     │     domain      │  enums / Bar / Trade / Position
                     │  (의존 없음)     │  Signal / Order / Config DTO
                     └─────────────────┘
```

### 2.4 디렉토리 구조

```
oneil/
├─ backtest_plan.md                 # (본 문서)
├─ oneil_strategy.md                # 규칙 원문 v3-3
├─ pyproject.toml
├─ config/
│  ├─ rules_v3-3.yaml               # 모든 규칙 수치 + 버전 태그 (§5)
│  └─ costs.yaml                    # 수수료·거래세(기간별)·슬리피지
├─ data_example/                    # 스모크 테스트용 소형 실데이터
├─ src/oneil_bt/
│  ├─ domain/     enums.py bar.py trade.py signal.py config.py
│  ├─ data/       datasource.py csv_source.py loader.py calendar.py metadata.py
│  ├─ indicators/ base.py moving_average.py atr.py rolling_extremes.py
│  │              relative_strength.py indicator_set.py
│  ├─ rules/      market_filter.py trend_template.py overheating.py rs_filter.py
│  │              base_detector.py base_quality.py stage_tracker.py
│  │              stop_rule.py exit_rules.py
│  ├─ execution/  fill_model.py orders.py cost_model.py
│  ├─ portfolio/  position_sizer.py portfolio.py risk_governor.py
│  ├─ engine/     context.py engine.py pipeline.py
│  ├─ reporting/  trade_log.py equity_curve.py metrics.py event_list.py report.py
│  └─ cli/        run_single.py run_portfolio.py
└─ tests/
   ├─ fixtures/   synthetic.py  (합성 OHLCV 빌더)
   ├─ unit/       (모듈별 미러링)
   └─ integration/ test_smoke.py test_golden.py
```

---

## 3. 핵심 인터페이스 시그니처 (의사코드)

> 계약만 고정. 구현은 Phase에서. 타입은 예시(pandas 사용 가정).

### 3.1 domain (값 객체 — 의존 없음)

```python
# enums.py
class Market(StrEnum): KOSPI; KOSDAQ
class EntryReason(StrEnum): BREAKOUT_T1; PYRAMID_T2; PYRAMID_T3
class ExitReason(StrEnum):
    STOP; TREND_60MA_HALF; TREND_60MA_REST; TREND_60MA_VOLBREAK
    MARKET_DEFENSE_120MA
class FillModelType(StrEnum): CLOSE_CONFIRMED_NEXT_OPEN; INTRADAY_TOUCH  # §12 Q1
class StopMethod(StrEnum): ATR2X; FIXED_PCT                              # §12 Q14

# bar.py  — 심볼 1개의 일봉 프레임 래퍼(불변)
class PriceFrame:
    symbol: str
    df: pd.DataFrame          # index=date(정렬·중복없음), cols: open high low close volume [value]
    def slice(self, start: date, end: date) -> "PriceFrame": ...
    def asof(self, d: date) -> pd.Series | None: ...

# trade.py
@dataclass(frozen=True)
class Fill: date: date; price: float; qty: int; reason: EntryReason|ExitReason; cost: float
@dataclass
class Position:
    symbol: str; market: Market
    tranches: list[Fill]               # 진입 체결들
    avg_price: float; qty: int
    stop_price: float                  # 평단 갱신 시 재계산
    entry_date: date; pivot: float; base_stage: int
    breakout_high_20d_ok: bool         # 8주 룰 자격(돌파후 3주 +20%)
    ma60_break_date: date | None       # 60MA 이탈 카운트 시작일
@dataclass
class ClosedTrade:                      # 트레이드 로그 1행(트랜치별로도 전개 가능)
    symbol; market; tranche_no; entry_fill: Fill; exit_fill: Fill
    pnl: float; pnl_r: float; hold_days: int

# signal.py
@dataclass(frozen=True)
class Signal:
    symbol; date; kind: Literal["ENTRY","PYRAMID","EXIT_STOP","EXIT_TREND","EXIT_DEFENSE"]
    priority: float                     # 동일일 정렬용(§8.4)
    payload: dict                       # pivot, trigger_price, tranche_no 등
@dataclass(frozen=True)
class Order:
    symbol; kind: Literal["STOP_BUY","LIMIT_BUY","MARKET_SELL"]
    trigger: float | None; limit_cap: float | None; qty: int | None; reason

# config.py
@dataclass(frozen=True)
class Config:
    rulebook_version: str
    trend: TrendCfg; base: BaseCfg; quality: QualityCfg; overheating: OverheatCfg
    rs: RsCfg; entry: EntryCfg; stop: StopCfg; exit: ExitCfg
    sizing: SizingCfg; portfolio: PortfolioCfg; market_filter: MarketFilterCfg
    fill: FillCfg; cost: CostCfg
    @staticmethod
    def load(rules_yaml: Path, costs_yaml: Path) -> "Config": ...
```

### 3.2 data

```python
# datasource.py
class DataSource(Protocol):
    def symbols(self) -> list[str]: ...
    def load_prices(self, symbol: str) -> PriceFrame: ...
    def load_index(self, market: Market) -> PriceFrame: ...
    def meta(self, symbol: str) -> "SymbolMeta": ...

# metadata.py
@dataclass(frozen=True)
class SymbolMeta:
    symbol: str; name: str; market: Market
    listing_date: date | None            # IPO 52주 미만 배제용
    shares_out: int | None               # (선택) 유동성 필터용
class MetaRepository:                     # meta.csv 로드 (§4.2 결정)
    def get(self, symbol: str) -> SymbolMeta: ...

# calendar.py
class TradingCalendar:                    # 지수 CSV 날짜 기준
    def __init__(self, sessions: Sequence[date]): ...
    def sessions_between(self, start, end) -> list[date]: ...
    def shift(self, d: date, n: int) -> date | None:      # n거래일 이동(+/-)
    def lookback_window(self, d: date, n: int) -> list[date]: ...
    def calendar_days_between(self, a: date, b: date) -> int:   # 베이스 기간(달력일)

# loader.py
class CsvBarLoader:                        # 인코딩 UTF-8/CP949 자동, 검증 포함
    def load(self, path: Path) -> PriceFrame: ...
    # 검증: 컬럼 존재, 날짜 정렬·중복, high>=low>=0, close in [low,high], 결측
class ValidationError(Exception): ...
```

### 3.3 indicators

```python
# base.py
class Indicator(Protocol):
    def compute(self, prices: PriceFrame) -> pd.Series: ...   # index=date, 값@D는 ≤D만 사용

# indicator_set.py  — 심볼별 사전계산·캐시 (룩어헤드 없음: 전부 과거포함 롤링)
class IndicatorSet:
    def __init__(self, prices: PriceFrame, index_prices: PriceFrame, cfg: Config): ...
    ma50: pd.Series; ma60: pd.Series; ma120: pd.Series; ma150: pd.Series; ma200: pd.Series
    atr14: pd.Series
    high_52w: pd.Series; low_52w: pd.Series      # 장중 고저 기준 롤링(252거래일)
    turnover_20d: pd.Series                       # 20일 평균 거래대금
    ret_20d: pd.Series                            # 과열 판정용
    rs_6m: pd.Series                              # §12 Q5: 정의 확정 필요
    def ma200_rising(self, d: date) -> bool:      # 200MA[d] > 200MA[d-rise_lookback]
```

### 3.4 rules

```python
# market_filter.py  — 지수 상태머신(정상/경계/방어) + 복귀 3거래일
class MarketState(StrEnum): NORMAL; CAUTION; DEFENSE
class MarketFilter:
    def __init__(self, index_prices: PriceFrame, ind: IndicatorSet, cfg): ...
    def state_asof(self, d: date) -> MarketState: ...          # d 종가 기준
    def new_entry_allowed(self, d: date) -> bool: ...          # 진입 판정엔 d-1 종가 사용
    def defense_triggered_on(self, d: date) -> bool: ...       # 120MA 이탈 발생일

# trend_template.py / overheating.py / rs_filter.py — 셋업 게이트(순수 판정, d-1 종가 기준)
class TrendTemplateFilter:
    def passes(self, symbol, d: date) -> bool: ...             # 7개 조건 AND
class OverheatingFilter:
    def excluded(self, symbol, d: date) -> bool: ...           # 해당 시 진입 금지
class RsFilter:
    def passes(self, symbol, d: date) -> bool: ...             # 6M 종목수익률 > 지수수익률

# base_detector.py  — 심볼별 전방 스캔(상태 유지). 값@D는 ≤D-1만 사용해 피벗 확정
@dataclass(frozen=True)
class Base:
    start: date; pivot: float; base_low: float; depth_pct: float
    weeks_elapsed: float; min_weeks: int; tier: str; stage: int
class BaseDetector:
    def __init__(self, prices, ind: IndicatorSet, cfg): ...
    def base_asof(self, d: date) -> Base | None:               # d-1까지로 확정된 유효 베이스
    def is_breakout(self, d: date, base: Base) -> bool:        # d 장중 고가 >= pivot
# stage_tracker.py — 직전 돌파가·직전 베이스 저점 기억 → 단계 카운트/리셋
class StageTracker:
    def stage_for(self, d: date, base: Base) -> int: ...
    def reset_check(self, d: date) -> None: ...

# base_quality.py — 진입 가능 품질 4요건
class BaseQualityCheck:
    def passes(self, d: date, base: Base) -> QualityResult: ...
    # 1) 과열 미해당  2) 2*ATR <= pivot*10%  3) 수축(직전10일 레인지<=pivot*10%)
    # 4) 드라이업(직전10일 평균거래량 < 베이스 일평균 거래량)

# stop_rule.py
class StopRule:
    def stop_price(self, avg_price: float, atr: float) -> float: ...  # avg-2*ATR, 캡 -10%
    def hit(self, pos: Position, d: date) -> bool: ...               # 종가 <= stop (기본)

# exit_rules.py
class TrendExitRule:        # 60MA 이탈: 절반 → 3거래일 회복 실패 시 잔량
    def evaluate(self, pos: Position, d: date) -> Signal | None: ...
class MarketDefenseRule:    # 지수 120MA 이탈 → 해당 시장 종목 절반 (8주 룰 예외)
    def evaluate(self, pos, d, mstate: MarketState) -> Signal | None: ...
class EightWeekGuard:
    def protected(self, pos: Position, d: date) -> bool: ...         # ③ 정지 여부
```

### 3.5 execution / portfolio / engine

```python
# fill_model.py
class FillModel(Protocol):
    def fill_entry(self, bar: pd.Series, order: Order) -> Fill | None: ...
    def fill_pyramid(self, bar: pd.Series, order: Order) -> Fill | None: ...
    def fill_exit(self, bar: pd.Series, order: Order) -> Fill: ...
class DailyBarFillModel:    # §8.3 체결 가정표 구현. cost 반영은 CostModel 주입
    def __init__(self, cost: CostModel, cfg: FillCfg): ...

# cost_model.py — 기간별 거래세 지원
class CostModel:
    def buy_cost(self, price, qty, d) -> float: ...
    def sell_cost(self, price, qty, d, market: Market) -> float: ...  # 세금은 매도·시장·기간별

# position_sizer.py
class PositionSizer:
    def target_weight(self, entry, atr) -> float: ...        # min(20, 1/stop% *100)
    def tranche_qty(self, equity, weight, tranche_ratio, price) -> int: ...  # floor 정수주

# portfolio.py — 현금/포지션/슬롯의 단일 소유자
class Portfolio:
    cash: float; positions: dict[str, Position]
    def equity(self, marks: dict[str, float]) -> float: ...
    def can_open(self, needed_cash: float) -> bool: ...       # 슬롯<max AND 현금충분
    def apply_fill(self, fill: Fill) -> None: ...
# risk_governor.py — 연속손절 3회 → N거래일 신규중단 (§12 Q12, config 토글)
class RiskGovernor:
    def new_trades_blocked(self, d: date) -> bool: ...
    def record_exit(self, trade: ClosedTrade) -> None: ...

# engine.py — 일별 이벤트 루프
class BacktestEngine:
    def __init__(self, source: DataSource, cfg: Config,
                 indicators, filters, base_detector, quality,
                 stop, exits, sizer, fill_model, portfolio, governor, reporters): ...
    def run(self, start: date, end: date) -> "BacktestResult": ...
    # 하루 처리 순서는 §8.4 우선순위표를 따른다
class DailyContext:      # 하루치 상태 스냅샷(마크가격, 시장상태, 후보목록)
    ...
```

---

## 4. 데이터 사양과 계약 (프롬프트 §5)

### 4.1 종목 일봉 CSV (종목당 1파일)

| 컬럼 | 필수 | 용도 |
|---|---|---|
| date | ✅ | YYYY-MM-DD |
| open, high, low, close | ✅ | **수정주가**여야 함(§13 주의) |
| volume | ✅ | 돌파일 거래량 검증(1.5×), 드라이업, 과열 |
| value(거래대금) | 선택 | 없으면 `close*volume`로 대체(근사, config 플래그) |

### 4.2 종목 메타데이터 — **결정: 별도 `meta.csv` 채택**

파일명 규칙(브리틀)·행별 컬럼(중복) 대신 **단일 메타 CSV**를 채택한다: `symbol,name,market,listing_date[,shares_out]`.
- `market`(KOSPI/KOSDAQ)은 RS 벤치마크·시장필터 매칭에 **필수**.
- `listing_date`로 IPO 베이스(상장 52주 미만) 유니버스 배제.
- `shares_out`은 선택(유동성 하한 대용). → 확정 질문 §12 Q17.

### 4.3 레퍼런스 CSV 2개 — 코스피/코스닥 지수 일봉

- `date, close` 필수(OHLC 있어도 무방). 용도: RS, 시장필터, **거래일 캘린더 기준**.

### 4.4 데이터 요건과 검증

- **워밍업**: 시작일 이전 **약 300거래일**(200MA + 52주 + 6M RS 여유). 부족 종목은 **판정 시작일을 자동으로 뒤로 미룬다**(종목별 `first_eligible_date` 계산).
- **거래일 캘린더 = 지수 CSV 날짜**. 종목은 이 캘린더로 reindex.
- **결측일(거래정지 등) 처리(제안, §12 Q20)**: reindex 후 결측 바는 (a) 신규 판정·진입 스킵, (b) 보유 포지션은 **마지막 종가로 평가만** 지속, (c) 손절/60MA 등 종가 기반 판정은 실거래 바가 있는 날만. forward-fill로 가짜 신호를 만들지 않는다.
- **로더 검증**: 컬럼 존재, 날짜 정렬·중복, `high>=low>=0`, `low<=close<=high`, 결측치, 인코딩(UTF-8/CP949 자동감지).
- **데이터 주의(문서화)**: 수정주가 확인 책임(입력 전제), 생존편향(상장폐지 종목 포함 여부, §12 Q18).

---

## 5. 설정 스키마 (프롬프트 §7.3) — `config/rules_v3-3.yaml`

모든 규칙 수치를 외부화. 아래는 **기본값 초안**(규칙서 매핑 주석 포함). 범위 표기(예: +25~30%)는 단일값 확정 필요(§12).

```yaml
rulebook_version: "v3-3"          # 재현성 태그
calendar_source: index            # 거래일 = 지수 CSV

trend_template:                   # §3 0단계
  above_ma: [150, 200]            # 주가 > 150 & 200
  ma150_gt_ma200: true
  ma200_rising_lookback: 20       # "200MA 1개월 이상 상승" 정의 → Q6
  ma50_gt_ma150: true
  low_52w_gain_min_pct: 25        # +25~30% → 25 채택(Q6)
  high_52w_within_pct: 15         # 고가 -15% 이내
  turnover_20d_min_krw: 1.0e10    # 100억
  # (규칙서 7개만. 고전 미너비니의 '주가>50MA','50MA>200MA'는 미추가 → Q6)

overheating:                      # §3 과열 제외 / §5 품질1
  ret_lookback_days: 20
  ret_threshold_pct: 50
  require_no_base: true           # "베이스 없이" 조건 → Q4
  limitup_lookback_days: 10       # 2주내 상한가 → Q3(데이터 필요)
  swing_pct: 15                   # ±15% 스윙 반복 → Q3(횟수 정의 필요)
  swing_min_count: null

rs:                               # §3 1단계
  lookback_days: 126              # 6개월
  method: return_diff             # 종목6M수익 - 지수6M수익 > 0 → Q5

base:                             # §5 베이스 규칙
  depth_tiers:
    - {max_depth_pct: 15, min_weeks: 5}     # 플랫
    - {max_depth_pct: 33, min_weeks: 7}     # 컵/더블바텀
  invalid_depth_pct: 33           # 초과 시 패턴 무효
  min_days_per_week: 7            # 최소 N주 = 경과 >= 7*N 달력일
  stage:
    step_up_close_gain_pct: 20    # 유효돌파 후 +20% 상승 뒤 베이스 = 단계+1
    max_stage: 3                  # 1~3 허용, 4+ 금지

quality:                          # §5 품질요건 (전부 충족)
  atr_le_pivot_pct: 10            # 2*ATR <= 피벗*10%
  contraction_lookback: 10        # 직전10일 레인지 <= 피벗*10%
  contraction_le_pivot_pct: 10
  dryup_lookback: 10              # 직전10일 평균거래량 < 베이스 일평균

entry:                            # §4 매수
  breakout_use_intraday: true     # 돌파만 장중, 나머지 종가
  chase_limit_pct: 5              # 피벗 +5% 이내만
  breakout_volume_mult: 1.5       # 돌파일 거래량 >= 20일평균*1.5 (2·3차 게이트)
  tranche_ratios: [0.5, 0.3, 0.2] # 목표비중 분할
  pyramid_triggers_pct: [2.5, 5]  # 2차: 1차+2~3%(→2.5), 3차: 1차+5%
  tranche_price_cap_pct: 3        # 트리거가 +3% 상한, 초과 갭 시 스킵

stop:                             # §6-①
  method: atr2x                   # atr2x | fixed_pct  → Q14
  atr_mult: 2.0
  max_stop_pct: 10                # 손절폭 캡
  fixed_pct: 8                    # 대안(고정 -7~8%)
  fill_model: close_confirmed_next_open   # ⚠ Q1: vs intraday_touch

exit:                             # §6-②③ + 보조
  ma_trend: 60
  trend_break_partial: 0.5        # 절반 매도
  trend_recover_days: 3           # 3거래일 내 회복 실패 시 잔량
  volbreak_full: false            # 거래량급증 이탈 즉시전량 사용? → Q7
  volbreak_mult: 2.0
  market_defense_ma: 120
  market_defense_reduce: 0.5      # 해당 시장 종목 절반
  eight_week:
    fast_gain_pct: 20             # 돌파후 3주 내 +20%
    fast_window_days: 21
    min_hold_days: 56             # 8주 보유(①② 외 정지)

sizing:                           # §1
  risk_per_trade_pct: 1           # 참고(공식이 자동보장)
  max_weight_pct: 20              # 1종목 상한(살 때)
  min_weight_pct: null            # 하한 없음
  reserve_pyramid_cash: true      # 2·3차 자금 현금 예약 → Q13

portfolio:                        # §1/§3.10
  max_positions: 8
  min_positions_soft: 5           # 강제 아님(살 게 없으면 현금)

market_filter:                    # §2
  entry_ma: 60                    # 지수>60MA 정상
  defense_ma: 120
  defense_max_equity_pct: 50      # 방어 시 주식비중 상한
  recover_days: 3                 # 60MA 위 3거래일 유지 → 정상 복귀

risk_governor:                    # §7 생존수칙
  enabled: true                   # → Q12
  consecutive_stops: 3
  halt_days: 10                   # 2주
```

`config/costs.yaml` (기간별 거래세, §8):
```yaml
commission_bp: 1.5                # 편도 수수료(예시, 확인 §12 Q19)
slippage_bp: 5                    # 체결 슬리피지(편도)
sell_tax_schedule:                # 매도 거래세(농특세 포함), 시행일 기준 계단
  - {from: "2000-01-01", kospi_bp: 30, kosdaq_bp: 30}
  - {from: "2019-06-03", kospi_bp: 25, kosdaq_bp: 25}
  - {from: "2021-01-01", kospi_bp: 23, kosdaq_bp: 23}
  - {from: "2023-01-01", kospi_bp: 20, kosdaq_bp: 20}
  - {from: "2024-01-01", kospi_bp: 18, kosdaq_bp: 18}
  - {from: "2025-01-01", kospi_bp: 15, kosdaq_bp: 15}
  # ⚠ 실제 스케줄은 사용자 확인(§12 Q19). 농특세·유관기관수수료 포함 여부 명시 필요
```

---

## 6. 백테스트 정합성 (프롬프트 §8)

### 6.1 룩어헤드 금지 — 판정 vs 체결 시점 분리 (타이밍 계약)

| 이벤트 | 판정 입력(사용 가능 정보) | 판정 시점 | 체결일 | 근거 |
|---|---|---|---|---|
| 신규 돌파 1차 진입 | 베이스·피벗·품질(≤D-1 확정), 셋업게이트·시장필터(**D-1 종가**) | D 장중 | **D** | 돌파만 장중(§4). 당일 종가·지수 미확정이라 게이트는 D-1 |
| 돌파일 거래량 검증 | D **종가 확정** 거래량 vs 20일평균 | D 종가 | (게이트) | 2·3차 예약 여부 결정(1차는 이미 체결) |
| 피라미딩 2·3차 | 1차 체결가 기준 트리거 | E>D 장중 | **E** | 트리거가 도달 시 장중 |
| 손절(기본) | **D 종가** ≤ 손절가 | D 종가 | **D+1 시가** | §6①"종가 도달→다음날 매도", §9"판단은 종가" |
| 손절(대안) | D 장중 저가 ≤ 손절가 | D 장중 | **D**(자동스탑) | §7 자동감시주문. config로 선택(Q1) |
| 60MA 절반/잔량 | **D 종가** < 60MA | D 종가 | **D+1 시가** | §6② |
| 시장방어 120MA | 지수 **D 종가** < 120MA | D 종가 | **D+1 시가** | §6③ |

- 지표 값@D는 과거포함 롤링이라 구조적으로 룩어헤드 없음. 진입 게이트가 D-1을 쓰는 이유는 "장중 D에 진입 결정 시 D 종가·지수 종가는 아직 없음"이기 때문.
- 베이스 피벗·깊이·품질은 **[start, D-1]**만으로 확정 → D의 돌파를 편향 없이 검정.

### 6.2 일봉 기반 체결 모델 (핵심 — 결정론적)

기호: `P`=피벗, `O/H/L/C`=당일 시고저종, `cap`=지정가 상한.

| 상황 | 조건 | 체결가 | 미체결/엣지 처리 |
|---|---|---|---|
| **1차 돌파** | `H >= P` (장중 피벗 터치) | `fill = max(O, P)` | (아래 추격한도) |
| — 갭업 | `O > P` | `O`가 `P` 이상에서 시작 | `O <= P*(1+chase)` 이면 `O`체결 |
| — 추격 초과 | `max(O,P) > P*(1+chase)` | — | `L <= P*(1+chase)`면 `cap`체결, 아니면 **미체결**(EventList에 "추격한도 초과" 기록) |
| **거래량 게이트** | `Vol(D) >= 1.5*MA20(Vol)` | — | 실패 시 2·3차 예약 안함, **1차만 유지**(이후 매도규칙 처리) |
| **2·3차 트랜치** | 이후일 `H >= trigger` | `fill = max(O, trigger)`, `<= trigger*1.03` | `O > trigger*1.03`(갭이 상한 초과)면 그 회차 **스킵**(규칙서 명시) |
| **손절-종가확정(기본)** | `C(D) <= stop` | `D+1`: `open` | `D+1` 갭 여부 무관, 시가 전량 |
| **손절-장중(대안)** | `L <= stop` | `min(O, stop)` | 갭하락 `O < stop`면 `O`체결 |
| **60MA 절반** | `C(D) < MA60` | `D+1` 시가로 `floor(qty/2)` | 잔량은 카운터 시작 |
| **60MA 잔량** | 이탈 후 3거래일 내 `C >= MA60` 실패 | `D+1` 시가 잔량 | 3일 내 회복 시 취소·보유 |
| **시장방어** | 지수 `C < MA120` | `D+1` 시가 해당시장 각 포지션 절반 | 8주 룰 보호 종목 제외 |
| **동일일 다중 신호** | — | §8.4 우선순위 | — |

### 6.3 동일일 신호 우선순위 (하루 처리 순서)

1. **강제 청산 먼저**: 손절 → 60MA → 시장방어 (현금·슬롯 확보 우선).
2. **기존 포지션 피라미딩** (이미 잡은 트레이드 관리).
3. **신규 돌파 진입**: 슬롯·현금 제약 하에서 후보 정렬. **정렬 키 = RS 내림차순, 동점 시 심볼 사전순**(결정론). → 정렬 기준 확정 §12 Q10.

### 6.4 비용·결정론

- **비용 파라미터화**: 편도 수수료(bp), 매도 거래세(시장·기간별 계단), 슬리피지(bp). CostModel이 fill에 반영.
- **정수주 체결**: 수량 `floor`. 잔여 현금 이월.
- **결정론**: 난수 없음, 심볼 반복 순서 고정(정렬), 안정 정렬 사용 → 동일 입력·설정 = 동일 결과. 회귀 골든파일로 보증(Phase 8).

---

## 7. 규칙 → 컴포넌트 매핑 (추적성)

| 규칙서 조항 | 규칙 | 소유 컴포넌트 | Phase |
|---|---|---|---|
| §2 | 시장필터 정상/경계/방어, 복귀 3일 | `MarketFilter` | 2 |
| §3 0단계 | 트렌드 템플릿 7조건 | `TrendTemplateFilter` | 2 |
| §3 1단계 | 6M RS > 지수 | `RsFilter` | 2 |
| §3 1단계 | 분기 영업이익 +20% 흑자 | ❌ **데이터부재 → 범위제외** | — (§11) |
| §3 1단계 | 테마 1~2등 대장주 | ❌ **유니버스/피어 데이터 → 범위제외** | — (§11) |
| §3 과열제외 | 20일 +50%(베이스없이) | `OverheatingFilter` | 2 |
| §3 과열제외 | 상한가/±15% 스윙 | ⚠ 데이터·정의 필요 → Q3 | 2(부분) |
| §5 정의·기간·깊이 | 피벗/깊이/기간/무효화 | `BaseDetector` | 3A |
| §5 단계 카운트 | 1~3 허용, 리셋 | `StageTracker` | 3A |
| §5 품질요건 4 | ATR캡·수축·드라이업·과열 | `BaseQualityCheck` | 3B |
| §4 진입·추격·거래량 | 1차/추격한도/1.5× | `DailyBarFillModel`+`pipeline` | 4A |
| §4 분할 50/30/20 | 피라미딩 트리거·상한 | `DailyBarFillModel`+`PositionSizer` | 4A/5 |
| §4 평단 갱신 손절 | 재계산·스탑 갱신 | `StopRule`+`Portfolio` | 4B |
| §6① | 2×ATR, -10%캡 | `StopRule` | 4B |
| §6② | 60MA 절반→잔량 | `TrendExitRule` | 4B |
| §6③ | 120MA 방어 절반 | `MarketDefenseRule` | 4B |
| §6 보조 | 8주 룰 | `EightWeekGuard` | 4B |
| §1 | 비중=1/손절%×100, 상한20% | `PositionSizer` | 5 |
| §1/§3.10 | 8종목 상한·현금 | `Portfolio` | 5 |
| §7 | 연속손절 3회→2주 중단 | `RiskGovernor`(토글) | 5 |
| §7 | 레버리지·인버스·ETF 금지 | 유니버스/주문 배제 | 0 |
| §8 실행루틴 | 일별·주말 판정 시점 | `BacktestEngine` 루프 | 6 |
| §6 출력 | 트레이드/자본곡선/지표/이벤트 | `reporting/*` | 7 |

---

## 8. Phase별 상세 계획

각 Phase는 **1세션 내 완료 가능** 크기. 각 Phase 말미의 "세션 시작 컨텍스트"는 구현 세션 시작 시 붙여넣을 최소 정보다.

> 참고 로드맵(프롬프트 §10) 대비 변경: **Phase 3을 3A(감지)/3B(품질·단계)로, Phase 4를 4A(진입·피라미딩·비용)/4B(손절·청산)로 분할**했다. 근거: 베이스 감지 상태머신과 체결 모델은 각각 단독으로도 1세션을 꽉 채우는 난이도·테스트량이라 원안대로 묶으면 1세션을 초과한다.

### Phase 0 — 골격 · 설정 · 캘린더 · 로더
- **목표**: 프로젝트 스캐폴딩, config 로드, 거래일 캘린더, CSV/메타 로더+검증.
- **산출 파일**: `pyproject.toml`, `config/*.yaml`, `domain/{enums,bar,config}.py`, `data/{datasource,csv_source,loader,calendar,metadata}.py`, `tests/fixtures/synthetic.py`.
- **구현 클래스**: `Config(+load)`, `PriceFrame`, `TradingCalendar`, `CsvBarLoader`, `MetaRepository`, `CsvDataSource(DataSource)`.
- **입력 계약**: 없음(최초). 예제 CSV 스키마(§4).
- **출력 계약**: `DataSource` Protocol, `Config` DTO, `TradingCalendar` API 확정.
- **테스트**: 인코딩(UTF-8/CP949), 날짜 정렬·중복·결측, `high>=low`·`low<=close<=high`, 캘린더 shift/window 경계, config 파싱·버전태그, 잘못된 CSV→`ValidationError`.
- **DoD**: `Config.load` + `CsvDataSource`로 합성 CSV 왕복, 전 테스트 green.
- **세션 시작 컨텍스트**: 본 §3.1~3.2 계약, §4 데이터 사양, §5 config 스키마.

### Phase 1 — 지표
- **목표**: MA(50/60/120/150/200), ATR(14), 52주 고저, 20일 거래대금, 20일 수익률, RS(6M), `ma200_rising`.
- **산출**: `indicators/{base,moving_average,atr,rolling_extremes,relative_strength,indicator_set}.py`.
- **구현**: `Indicator` Protocol, 각 지표, `IndicatorSet`.
- **입력 계약**: `PriceFrame`, 지수 `PriceFrame`(Phase 0).
- **출력 계약**: `IndicatorSet` 필드·시그니처.
- **테스트**: 알려진 값 대조(작은 시계열 손계산), ATR True Range 경계(갭 포함), 52주 롤링 창 길이, RS 정의 단위테스트, **값@D가 D 이후 데이터를 안 씀**(룩어헤드 회귀: 뒷날 조작해도 과거 값 불변).
- **DoD**: 합성 시계열에서 전 지표 수치 검증.
- **세션 시작 컨텍스트**: §3.3 계약, `PriceFrame` API, config `trend/rs` 키.

### Phase 2 — 셋업 필터 + 시장필터
- **목표**: 트렌드 템플릿, 과열 제외, RS 게이트, 시장 상태머신.
- **산출**: `rules/{trend_template,overheating,rs_filter,market_filter}.py`.
- **구현**: `TrendTemplateFilter`, `OverheatingFilter`, `RsFilter`, `MarketFilter(+MarketState)`.
- **입력 계약**: `IndicatorSet`(Phase 1), 지수 프레임.
- **출력 계약**: 각 `passes/excluded/state_asof/new_entry_allowed` 시그니처.
- **테스트**: 7조건 각 경계(150/200MA, 52주 ±%), 과열 50% 경계, RS 경계, 시장필터 정상↔경계↔방어 전이 + **복귀 3거래일** 유지/실패, `new_entry_allowed`가 D-1 종가 사용 확인.
- **DoD**: 각 게이트 boolean 경계 테스트 green.
- **범위 주의**: 과열의 상한가/±15% 스윙은 Q3 확정 전까지 **플래그만**(20일 +50%는 완비).
- **세션 시작 컨텍스트**: §3.4 계약, config `trend_template/overheating/rs/market_filter`.

### Phase 3A — 베이스 감지기
- **목표**: 시작점·피벗·깊이·기간·무효화/리셋 + 단계 카운트.
- **산출**: `rules/{base_detector,stage_tracker}.py`.
- **구현**: `Base` DTO, `BaseDetector(base_asof,is_breakout)`, `StageTracker`.
- **입력 계약**: `PriceFrame`, `IndicatorSet`, `TradingCalendar`.
- **출력 계약**: `base_asof(d)->Base|None`(≤d-1로 확정), `is_breakout(d,base)`.
- **알고리즘(전방 스캔·상태유지)**: 현재 베이스 시작=조정을 시작한 신고가일. `H>running_pivot`이고 기간 미충족이면 **무효→시작점 이동**; 기간 충족이면 그 날이 돌파 후보. 깊이=`(pivot-min_low)/pivot`; `>33%`면 패턴 무효(회복 후 신고가에서 재시작). 단계: 직전 유효돌파 대비 종가 +20% 상승 후 형성=+1, 미달 재베이스=유지, 직전 베이스 저점 이탈=리셋(1).
- **테스트(합성)**: 깊이 **15%/33% 경계**, 기간 **5주/7주 경계**(달력일 7×N), 기간 미충족 조기 상회→리셋, D>33% 무효·재시작, **단계 1→2→3 상승/4 금지**, 직전 저점 이탈 리셋, 피벗이 [start,D-1]만으로 확정(룩어헤드 회귀).
- **DoD**: 경계 픽스처 전부 기대 `Base`/`None` 산출.
- **세션 시작 컨텍스트**: §3.4 `Base`/`BaseDetector` 계약, config `base`, §5 원문 인용.

### Phase 3B — 베이스 품질
- **목표**: 진입 품질 4요건.
- **산출**: `rules/base_quality.py`.
- **구현**: `BaseQualityCheck(passes)->QualityResult`.
- **입력 계약**: `Base`(3A), `IndicatorSet`, `OverheatingFilter`(과열 재사용).
- **테스트**: `2*ATR<=pivot*10%` 경계, 직전10일 레인지·드라이업 경계, 과열 연동, 4요건 AND.
- **DoD**: 각 요건 개별·복합 경계 green.
- **세션 시작 컨텍스트**: §3.4 `BaseQualityCheck`, config `quality`, Phase 3A 산출.

### Phase 4A — 체결 프리미티브(진입·피라미딩·비용)
- **목표**: 일봉 체결 모델(1차·2·3차) + 비용 + 주문 객체.
- **산출**: `execution/{orders,cost_model,fill_model}.py`.
- **구현**: `Order`, `CostModel(기간세)`, `DailyBarFillModel.fill_entry/fill_pyramid`.
- **입력 계약**: config `entry/cost`, `CostModel` 주입.
- **테스트**: §6.2 표 전 케이스 — 정상 돌파, **갭업 추격한도 내/초과 미체결**, 트랜치 트리거·+3% 상한·갭 스킵, 거래량 게이트 성공/실패, 비용 정확성(매수/매도·시장·기간별 세금), 정수주 floor.
- **DoD**: 체결가·비용이 표와 일치.
- **세션 시작 컨텍스트**: §6.2 체결표, §3.5 `FillModel/CostModel`, config `entry/cost`.

### Phase 4B — 손절·청산 규칙
- **목표**: 손절(2×ATR/-10%, 평단 갱신), 60MA 절반→잔량, 시장방어, 8주 룰.
- **산출**: `rules/{stop_rule,exit_rules}.py`.
- **구현**: `StopRule`, `TrendExitRule`, `MarketDefenseRule`, `EightWeekGuard`.
- **입력 계약**: `Position`, `IndicatorSet`, `MarketFilter`, `FillModel`(4A).
- **테스트**: 종가확정 손절 D+1 체결, **갭하락 손절**(대안 모델), ATR 캡 -10% 발동, 평단 상승 후 손절 재계산, 60MA 절반→3거래일 회복/실패 분기, 방어 절반, **8주 룰이 ③ 정지**(①② 유지) 확인.
- **DoD**: 각 청산 사유 트리거·체결일·수량 정확.
- **세션 시작 컨텍스트**: §3.4 청산 계약, §6.1 타이밍표, config `stop/exit`.

### Phase 5 — 포지션 사이저 + 포트폴리오 + 리스크 거버너
- **목표**: 비중 공식, 트랜치 수량, 현금/슬롯/예약, 연속손절 쿨다운.
- **산출**: `portfolio/{position_sizer,portfolio,risk_governor}.py`.
- **구현**: `PositionSizer`, `Portfolio`, `RiskGovernor`.
- **입력 계약**: config `sizing/portfolio/risk_governor`, `Fill`.
- **테스트**: 비중=1/손절%×100·상한20 경계, 트랜치 정수주, **8종목 상한 도달** 시 신규 거부, 현금부족 거부, 예약현금(2·3차) 회계, 연속손절 3회→N일 차단·해제.
- **DoD**: 포트폴리오 회계 항등식(현금+평가=equity) 유지.
- **세션 시작 컨텍스트**: §3.5 계약, §6.3 우선순위, config 관련 키.

### Phase 6 — 백테스트 엔진(일별 루프)
- **목표**: 전 컴포넌트 조립, 단일종목·포트폴리오 모드.
- **산출**: `engine/{context,pipeline,engine.py}`, `cli/{run_single,run_portfolio}.py`.
- **구현**: `BacktestEngine.run`, `DailyContext`, 신호 파이프라인.
- **입력 계약**: Phase 0~5 전부(DI로 주입).
- **하루 처리**: §6.3 순서(청산→피라미딩→신규). 판정/체결 시점 §6.1.
- **테스트**: 단일종목 모드가 날짜별 판정 로그 + 트레이드 산출, 소형 포트폴리오에서 슬롯·현금·우선순위 상호작용, 결정론(2회 실행 동일).
- **DoD**: 두 모드 end-to-end 동작, 룩어헤드 가드 통과.
- **세션 시작 컨텍스트**: §2.3 다이어그램, §6 전체, 모든 Phase 인터페이스.

### Phase 7 — 리포팅
- **목표**: 트레이드 로그 CSV, 일별 자본곡선 CSV, 성과 리포트, 이벤트 목록.
- **산출**: `reporting/{trade_log,equity_curve,metrics,event_list,report}.py`.
- **구현**: 각 리포터.
- **출력 계약(§9 상세)**: 아래 §9.
- **테스트**: 알려진 트레이드셋 → 총수익·CAGR·MDD·승률·손익비·기대값(R)·평균보유·평균노출·총비용 수치 검증, CSV 스키마 고정.
- **DoD**: 골든 CSV 대조.
- **세션 시작 컨텍스트**: §9 출력 사양, `BacktestResult` 구조(Phase 6).

### Phase 8 — 통합·회귀·문서
- **목표**: 소형 실데이터 스모크, 골든파일 회귀, 사용 문서.
- **산출**: `tests/integration/{test_smoke,test_golden}.py`, `data_example/`, `README` 사용법.
- **테스트**: 실데이터 1회 완주(에러 없음·수치 sanity), 결정론 골든 해시, 파라미터 1개 변경 시 결과 변화(민감도 훅 확인).
- **DoD**: `pytest` 전체 green, 재현 가능한 실행 예시 문서화.
- **세션 시작 컨텍스트**: 전 Phase 산출, §11 범위제외 목록.

---

## 9. 출력 사양 (프롬프트 §6)

- **트레이드 로그 CSV**: `symbol, market, trade_id, tranche_no, entry_reason(rule_id), entry_date, entry_price, entry_qty, entry_cost, exit_reason(rule_id), exit_date, exit_price, exit_qty, exit_cost, pnl, pnl_r, hold_days, base_stage, pivot`.
- **일별 자본곡선 CSV**: `date, cash, holdings_value, equity, n_positions, exposure_pct, market_state`.
- **성과 리포트**(txt/json): 총수익률, CAGR, MDD, 승률, 손익비(평균이익/평균손실), 기대값(R 단위), 평균 보유기간, 평균 노출도, 총 거래비용, 손절/60MA/방어별 청산 분해.
- **육안 검증 이벤트 목록 CSV**: `date, symbol, event(BREAKOUT_CANDIDATE/CHASE_SKIP/VOL_FAIL/...), pivot, depth_pct, weeks, stage` — 자동 필터가 V자 회복과 정상 조정을 완전 구분 못하므로 차트 확인 워크플로에 연결.

---

## 10. 테스트 전략 (프롬프트 §9)

- **프레임워크**: pytest. Phase마다 유닛테스트 필수.
- **합성 픽스처**(`tests/fixtures/synthetic.py` — OHLCV 빌더): 깊이 15%/33% 경계, 5주/7주 경계, 단계 리셋, 과열 50% 경계, 갭 손절, ATR 캡 발동, 8종목 상한, 추격한도 초과, 트랜치 갭 스킵, 60MA 3일 회복/실패.
- **룩어헤드 회귀 테스트**: 미래 바 조작 시 과거 판정 불변.
- **결정론 테스트**: 동일 입력·설정 2회 → 동일 산출(해시).
- **통합 스모크**: 소형 실데이터 1건(Phase 8).

---

## 11. 범위 제외와 후속 과제 (프롬프트 §11.5)

**v1 범위 제외(데이터 부재/불확정)**
1. **분기 영업이익/EPS +20%·흑자**(§3 1단계): OHLCV만으로 불가. 별도 펀더멘털 데이터 필요 → **제외**(제공 시 `FundamentalFilter` 추가). 근거·질문 §12 Q15.
2. **대장주(테마 1~2등) 판별·피어그룹 RS 랭크**: 종목 유니버스+테마분류 필요. v1은 RS를 **지수 대비 상대수익(불리언)**으로만 구현, 피어랭크는 **후속**. §12 Q16.
3. **수급(기관·외국인 순매수)·밸류업 공시 가점**: 데이터 부재 → 제외.
4. **과열 제외의 상한가/±15% 스윙 반복**: 상한가 데이터·"반복" 정의 필요 → 부분/후속. §12 Q3.
5. **연속손절 쿨다운**: 구현하되 config 토글(기본 on) — 포함/제외 판단 §12 Q12.
6. **하이타이트플래그·어센딩·스퀘어박스·IPO 베이스**: 규칙서 명시적 불채택 → 미구현.
7. **코어(미국·글로벌 지수) 배분**: 백테스트 대상 밖(§0).

**후속 과제(구조는 v1에서 확보)**
- 파라미터 민감도 스윕 하니스(모든 수치 외부화 완료 → 그리드 실행기만 추가).
- 워크포워드/롤링 검증, 몬테카를로 트레이드 순서.
- API 데이터 소스(`DataSource` 교체), 생존편향 보정 유니버스, 펀더멘털·수급 소스 통합.

---

## 12. 결정 필요 질문 목록 (임의 가정 금지 — 확정 요망)

> 아래는 규칙서가 모호하거나 §3 요약/프롬프트와 충돌하거나 데이터가 필요한 지점이다. 각 항목에 **제안 기본값**을 달아두었으니, 다르면 알려달라.

**A. 규칙 해석·충돌 (결과 영향 큼)**
1. **⚠ 손절 체결 시점 충돌**: §6①·§9는 "**종가 확정 → 다음날 매도**"(종가기준), §7·§8예시는 "**자동감시 스탑(장중 터치 체결)**". → 제안 기본값 **`close_confirmed_next_open`**(규칙서 본문·"모든 판단은 종가" 원칙 우선), `intraday_touch`는 config 대안. **갭하락장에서 결과가 크게 갈림.**
2. **버전 태그**: 문서=v3-3, 베이스=v3.2, 프롬프트=v3.2. → config `rulebook_version: v3-3` 제안.
3. **과열 제외 (b)(c)**: "2주내 상한가", "±15% 스윙 반복"의 데이터(상한가=±30% or 시기별)와 "반복" 횟수 정의. → 제안: v1은 "20일 +50%"만 완비, (b)(c)는 데이터 확보 시 추가.
4. **"베이스 없이 +50%"의 '베이스 없이' 판정**: 베이스 감지기와 어떻게 연동? → 제안: 직전 20일 내 유효 베이스 부재로 근사.
5. **RS 정의**: "6개월 수익률 > 소속 지수" 불리언 확정? 값 산식은 차이(종목-지수) vs 비율? 피어랭크 대장주는 v1 제외 확인. → 제안 `return_diff`, 126거래일.
6. **트렌드 템플릿 세부**: (i) "200MA 1개월 이상 상승" = `MA200[D]>MA200[D-20]`? (ii) "52주 저가 +25~30%" → **25** 채택? (iii) 규칙서 7조건만(고전 미너비니의 `주가>50MA`,`50MA>200MA` 미추가) 유지?
7. **60MA 이탈 세부**: "회복" = 종가 ≥ 60MA? 3거래일 카운트 시작(이탈일 다음날?). "거래량 급증 이탈 즉시 전량" 사용 여부·배수(기본 off, 2.0×)?
8. **시장방어 축소 대상**: §6③"해당 시장 종목 절반"(포지션별) vs §2방어"주식비중 50% 이하"(총량). → 제안: **포지션별 절반**(§6③ 문언). 총량 초과분 추가 축소는 옵션.
9. **8주 룰 범위**: "①②만 적용"이 ③·방어축소를 정지시키는 것 확인. +20%/3주 판정은 종가 기준?
10. **동일일 다중 신규진입 정렬 키**: RS 내림차순(제안) vs 돌파 거래량 vs 베이스 품질? 동점 심볼 사전순.
11. **피라미딩과 시장필터**: 2·3차가 시장필터 OFF(경계/방어)에서도 진행? → 제안: **진행**(신규진입이 아닌 보유관리).
12. **연속손절 쿨다운**: v1 포함(기본 on)? "연속" 정의 = 포트폴리오 전체·청산일 순 연속 손절 3회 → 10거래일 신규중단.
13. **현금 예약**: 2·3차 자금을 1차 진입 시 **목표 전액 현금 확보**로 예약(제안) vs 트리거 시점 재확인? 슬롯=심볼 1개 점유.
14. **손절 방식 기본값**: **2×ATR(-10%캡)** 확정(제안), 고정 -7~8%는 config 대안.

**B. 데이터·스코프**
15. **펀더멘털 필터**(분기 영업이익/EPS +20%·흑자): v1 제외 확정? 데이터 제공 가능?
16. **대장주·수급·밸류업 가점**: v1 제외 확정?
17. **메타데이터 방식**: 별도 `meta.csv`(symbol,market,listing_date[,shares_out]) 채택 확정?
18. **생존편향**: 유니버스에 상장폐지 종목 포함? 데이터 확보 가능?
19. **거래비용 실값**: 수수료(bp), 연도별 거래세 스케줄(농특세·유관수수료 포함 여부), 슬리피지(bp).
20. **결측일(거래정지) 처리**: reindex + 보유는 마지막 종가 평가 + 신규판정 스킵(제안) 확정?
21. **수정주가**: 입력이 수정주가 전제 — 확인 책임·방법.

---

## 13. 규칙서 ↔ 프롬프트 §3 요약 차이점 (프롬프트 §1 기록 의무)

| 항목 | 프롬프트 §3 요약 | 규칙서(v3-3) 실제 | 채택 |
|---|---|---|---|
| 과열 제외 | "20일 수익률 >50% 진입금지" | +50%는 **"베이스 없이"** 한정 + 상한가/±15% 스윙 별도 조항 | 규칙서. (b)(c)는 Q3 |
| RS | "벤치마크 대비 RS 계산" | 1단계 체크리스트 "6개월 수익률 > 소속 지수"(미너비니 RS70 대체) | 규칙서(불리언) |
| 손절 | "2×ATR, -10% 캡" | 동일 + **종가확정·다음날 매도**(§6①) vs 자동스탑(§7) 충돌 | 규칙서 §6① 기본, Q1 |
| 손절 체결(§8 예시) | "저가<손절가면 손절가 체결"(장중) | §6①은 종가확정 | 규칙서 기본, §8예시는 대안 config |
| 시장필터 | (요약에 없음) | §2 정상/경계/방어 + 복귀 3일 — **필수 구현** | 규칙서 반영 |
| 트렌드 템플릿 | "MA 배열·52주 위치" | 규칙서 7조건(거래대금 100억 포함) | 규칙서 7조건 |
| 8주 룰·60MA·방어 | "규칙서 명시 청산 전부" | §6 ①②③ + 보조(8주) 명시 | 전부 반영 |
| 펀더멘털/대장주 | (구현대상에 없음) | 1단계 체크리스트에 존재하나 데이터 필요 | v1 범위제외(§11) |
| 버전 | v3.2 | 문서 v3-3(베이스만 v3.2) | v3-3 태그 |

---

## 부록 A. 진행 규칙 (프롬프트 §12)

- 이 세션은 **계획서만** 산출한다.
- 승인 후 Phase 단위로 구현. 각 Phase의 "세션 시작 컨텍스트"(§8)를 붙여넣어 독립 착수한다.
- 규칙서가 단일 진실 원천. 구현 중 새 모호점 발견 시 §12에 추가하고 임의 가정 금지.
- 권장 착수 순서: Phase 0 → 1 → 2 → 3A → 3B → 4A → 4B → 5 → 6 → 7 → 8. (3B는 3A·2, 4B는 4A·2·3에 의존.)
```