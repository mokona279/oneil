# U0 — 사전 조사·결정 확정 (실행 기록)

**작성**: 2026-07-20 · **역할**: 조사 담당(코드 작성 없음) · **상태**: **결정 질문
Q1~Q12 전수 사용자 승인 완료(2026-07-20)** · **선행**: `docs/us/us_plan.md`(마스터 플랜),
`docs/us/u0_decisions.md`(단계 계획) · **불변 제약**: KR 자산(`data/`, `out/`,
`config/rules_v3-3.yaml`, `config/costs.yaml`) 읽기 전용.

> 이 문서는 U1(수집)·U2(번역)의 입력을 확정한다. **§4 결정 질문 Q1~Q12가 전부
> 사용자 답변으로 채워지면 U0 DoD 충족.** 미답 질문이 있으면 U1·U2 착수 금지.

---

## §1 데이터 소스 비교표

**조사 방법**: 각 소스 공식 문서/가격 페이지 + 제3자 리뷰를 2026-07 시점 웹 검색으로
확인(출처 §5). 기억 아님. 비교 축은 계획 문서 작업 항목 1을 그대로 사용.

평가 관점은 마스터 플랜 §1·§5·§6에 종속된다: 이건 **전이(transfer) 검증**이고
**절대 수익률 목표가 없으며 상대 비교가 본체**다. 따라서 1차 요구는 (a) 2015-10~
전 유니버스 일봉 커버, (b) 분할·(가능하면)배당 수정, (c) 전 유니버스 일괄 수집 +
증분 갱신의 **재현 가능성**, (d) 백테스트 허용 약관. 생존편향은 Q2에서 별도 결정.

| 소스 | 일봉 범위(2015-10 커버) | 수정주가 방식 | 상폐 종목 포함 | 전 유니버스 일괄 | 증분 갱신 | 호출 제한 | 비용 | TOS 백테스트 | 종합 |
|---|---|---|---|---|---|---|---|---|---|
| **Stooq** | 수십 년, 커버 ✅ | 조정 close(분할+배당 반영), 미조정 별도 없음 | ❌ 현행 상장 위주(벌크는 현재 목록) → 생존편향 | ✅ 무료 벌크 ZIP(US ASCII ~333MB) | 개별 심볼 CSV URL 또는 벌크 재다운로드 | 비공식, 과도 시 차단. **2020-12 이후 자동 다운로드 CAPTCHA 차단** | **무료** | 개인·비상업 관용, 명시적 문구 불명확 | 완전 무료·일괄 간편. 단 자동화 마찰·미조정 없음·value 컬럼 없음 |
| **yfinance(Yahoo)** | 장기, 커버 ✅ | adj close(분할+배당, total-return형) + raw OHLC | ❌ 상폐 티커는 **빈 DataFrame 조용히 반환** → 강한 생존편향 | 티커별 API(유니버스 목록 별도 확보 필요) | 쉬움(period/interval) | 비공식·비문서(대략 ~2000/hr, 초과 시 429·IP 차단). 페이지 구조 변경 시 6~12개월마다 파이프라인 중단 | **무료** | Yahoo TOS는 비상업·개인 한정, 대량 자동 수집은 약관 위반 소지(비공식 API) | 편하나 신뢰성·약관·재현성 취약. 파일럿용 |
| **Tiingo** | **1962~**, 커버 ✅ | raw + adjusted 둘 다(Dividend/Splits 컬럼) | 80,000+ 티커(OTC 포함), 상폐 포함 여부 **페이지 명시 없음 → U1 실측 확인 필요** | 무료 API(문서화) | API 용이 | 무료 50/hr·**1,000/day**·1GB/mo | 무료 티어 ○, Power **$30/mo**, Commercial $50/mo | 무료=Internal Use Only(개인 연구 허용, 재배포 금지) | 문서화된 API·raw+adj 품질 우수. 전 유니버스 초기 풀은 무료 1,000/day로 ~5~6일(또는 Power로 수 시간) |
| **Alpha Vantage** | 20년+, 커버 ✅ | adjusted + raw | listing_status API로 delisted 목록 제공(보정 가능) | 무료로는 불가 | API | 무료 **25/day**(사실상 무용) | 무료 티어 무용, **$49.99/mo~**(75/min) | 플랜별 | 무료 25/day로 5~6천 종목 수집 불가. 유료 가성비 낮음 |
| **EODHD** | US 26,000+ 티커, 대부분 2000~, 커버 ✅ | adjusted + raw | ✅ **delisted 별도 API 제공(생존편향-free 지향)**, Exchanges API로 목록 | ✅ EOD bulk API(거래소 단위, delisted 포함) | bulk-last-day API | 플랜별 콜/일 | **유료**(EOD/All-World ~$20~60/mo대, "예산형 역사데이터") | 상업·개인 플랜 | 유료지만 상폐 포함+일괄+value 근사 가능. 생존편향 보정 원하면 유력 후보 |
| **Norgate** | US **1950~**, 커버 ✅ | 조정/미조정 선택, 분할·배당 처리 정교 | ✅ **상폐 포함(생존편향-free)이 핵심**, Platinum 패키지 | ✅ 전용 클라이언트 + Python 라이브러리(`norgatedata`), 로컬 DB | EOD 업데이트 구독 | 구독제(로컬 데이터라 API 제한 무관) | **유료 구독**(6/12개월, 자동갱신 없음, **21일 무료체험**·2년 데이터) | 구독자 개인 사용, 재배포 금지 | 생존편향-free의 사실상 표준·최고 품질. 단 유료 + **Windows 전용 클라이언트 의존** |
| **Polygon(→Massive)** | 20년, 커버 ✅ | adjusted 지원 | ⚠ delisted 데이터 "spotty"(회사명·거래일 결측) → 보정 부적합 | ✅ grouped daily(하루 전체 티커) API 효율적 | grouped daily 반복 | 무료 5/min·**1년 히스토리만**. Starter unlimited | 무료 제한적, **Starter ~$29/mo**(15분 지연), 상위 실시간 | 플랜별 | 일괄 효율 좋으나 유료 필요 + 상폐 품질 약함. 2026-07 Massive.com으로 리브랜딩 |

**공통 주의**: 대부분의 소스가 **거래대금(value) 컬럼을 제공하지 않는다** → 유동성
게이트(`turnover_20d_min`)가 value를 요구하므로 **수집기에서 `close×volume` 근사
생성 필수**(마스터 플랜 §3 항목 4, U1 담당). 이는 소스 선택과 무관한 공통 작업.

**요약 판정**:
- **무료·현존상장 감수 노선(v1 기본, Q2와 정합)**: Tiingo 무료 티어가 1순위 —
  문서화된 API·raw+adjusted 동시 제공·명시적 개인용 약관으로 **재현성**이 Stooq
  스크래핑(2020 CAPTCHA)보다 견고. Stooq는 완전 무료·벌크 원샷이 강점이나 자동화
  마찰과 미조정 부재가 약점. yfinance는 약관·신뢰성 취약으로 비권장.
- **생존편향 보정 노선(유료, Q2 상향)**: EODHD(예산형·API 친화)가 가성비 1순위,
  Norgate(품질 표준·로컬 DB)가 대안. 둘 다 상폐 포함.
- Alpha Vantage(무료 25/day)·Polygon 무료(1년)는 전 유니버스 초기 수집에 부적합.

---

## §2 SEC Section 31 + FINRA TAF 수수료 이력 → `sell_tax_schedule` 초안

**핵심 사실**: 미국은 매도 거래세가 없다. 매도 측 법정 비용은 두 가지뿐이며 **둘 다
KR 증권거래세(15~30bp)의 ~1/100 규모**다.

### (a) SEC Section 31 수수료 (매도 측 부과)

SEC가 회계연도(10/1~9/30)마다 요율을 조정하고, 예산 확정 시점 때문에 **연중
시행일**에 계단식으로 바뀐다(연 1월 고정 아님). 요율은 `$/백만달러` 단위로 고시.
**bp 환산 = ($/백만) ÷ 100** (예: $22.10/백만 = 0.221bp).

출처: SEC Fee Rate Advisory·FINRA Information Notice·NYSE/Cboe 규제수수료 공지·
TraderStatus 컴파일 교차 확인(§5). 워밍업 시작(2015-10) 시점에 유효한 값부터 수록:

| 시행일 | $/백만 | bp 환산 | 근거 |
|---|---|---|---|
| (2015-10 시점 유효) 2015-02-14 | 18.40 | 0.184 | FY2015 |
| 2016-02-16 | 21.80 | 0.218 | FY2016 |
| 2017-07-04 | 23.10 | 0.231 | FY2017 중간조정 |
| 2018-05-22 | 13.00 | 0.130 | FY2018 중간조정 |
| 2019-04-16 | 20.70 | 0.207 | FY2019 |
| 2020-02-18 | 22.10 | 0.221 | FY2020 중간조정 |
| 2021-02-25 | 5.10 | 0.051 | FY2021 |
| 2022-05-14 | 22.90 | 0.229 | FY2022 중간조정 |
| 2023-02-27 | 8.00 | 0.080 | FY2023 |
| 2024-05-22 | 27.80 | 0.278 | FY2024 중간조정 |
| 2025-05-14 | 0.00 | 0.000 | FY2025(과다징수로 요율 0 설정) |
| 2026-04-04 | 20.60 | 0.206 | FY2026 |

**관찰**: 전 구간 **0.05~0.28bp**를 오간다. 슬리피지 5bp의 1/20 미만이며 연도별
변동폭조차 슬리피지 노이즈에 묻힌다. 2025-05~2026-04는 **0bp**(요율 정지)였다.

### (b) FINRA TAF (Trading Activity Fee, 매도 측 주당 과금)

- 현행: **$0.000166/주**(거래당 상한 $8.30), **2026-01-01부터 $0.000195/주**
  (상한 $9.79)로 인상. 매도 측 부과.
- **주당** 과금이라 명목가 대비 bp는 주가에 반비례: $50 주식 1주 매도 시
  $0.000166 = 명목의 **0.00033bp**. Section 31보다도 100배 작다 → **완전 무시 가능**.

### (c) `costs_us.yaml` 초안 제안

Section 31은 거래소 무관(NYSE=NASDAQ 동일 요율)이므로 기존 `sell_tax_schedule`
메커니즘을 그대로 재사용하되 시장별 필드를 동일 값으로 채우거나 단일 필드로 단순화
(정확한 필드명·엔진 매핑은 U2/U3 결정). 초안:

```yaml
# costs_us.yaml (U0 초안 — U2에서 확정)
commission_bp: 0        # 미국 제로커미션 관행 (Q9)
slippage_bp: 5          # KR과 동일 유지 + U5 민감도(15/30/50) (Q9)

sell_tax_schedule:      # SEC Section 31 (매도), 시행일 계단. bp=($/백만)/100
  - {from: "2015-02-14", sec_fee_bp: 0.184}
  - {from: "2016-02-16", sec_fee_bp: 0.218}
  - {from: "2017-07-04", sec_fee_bp: 0.231}
  - {from: "2018-05-22", sec_fee_bp: 0.130}
  - {from: "2019-04-16", sec_fee_bp: 0.207}
  - {from: "2020-02-18", sec_fee_bp: 0.221}
  - {from: "2021-02-25", sec_fee_bp: 0.051}
  - {from: "2022-05-14", sec_fee_bp: 0.229}
  - {from: "2023-02-27", sec_fee_bp: 0.080}
  - {from: "2024-05-22", sec_fee_bp: 0.278}
  - {from: "2025-05-14", sec_fee_bp: 0.000}
  - {from: "2026-04-04", sec_fee_bp: 0.206}
# FINRA TAF: 주당 $0.000166(26-01 $0.000195), ~0.0003bp → 무시(문서화만)
```

**대안(더 단순)**: 계단 전체를 **상수 0.2bp** 또는 **0bp**로 두어도 골든 수치 영향은
소수점 이하. 그럼에도 **계단 유지를 권장** — (i) KR `sell_tax_schedule`과 메커니즘
대칭이라 코드 무수정, (ii) 감사 가능성, (iii) 비용은 거의 0이라 정확도 손해 없음.

---

## §3 유니버스 정의 초안

**심볼 원천(무료·현행)**: NASDAQ Trader Symbol Directory FTP —
`nasdaqlisted.txt`(NASDAQ 상장) + `otherlisted.txt`(그 외 거래소). 매 거래일 갱신.
출처 §5.

**거래소 범위**: NYSE + NASDAQ + NYSE American(구 AMEX). `otherlisted.txt`의
Exchange 코드로 필터 — `N`=NYSE, `A`=NYSE American(AMEX), `nasdaqlisted`=NASDAQ.
`P`=NYSE Arca·`Z`=Cboe BZX·`V`=IEX는 대부분 ETF/2차 상장이라 **제외**.

**증권 유형 필터(보통주만)**: 디렉터리에 명시적 "증권유형" 필드가 없어 두 단계로:
1. **ETF 플래그**(`ETF`=Y) 제거, **Test Issue 플래그**(`Test Issue`=Y) 제거.
2. **Security Name 접미사 파싱**으로 우선주·워런트·유닛·권리·예탁증서 제거 —
   `"- Common Stock"`만 채택하고 `"- Preferred"`, `"- Warrant"`, `"- Unit"`,
   `"- Right"`, `"- Depositary"`, `"- Notes"`, `"% "`(우선주 배당률) 등 접미사는 배제.
   SPAC은 유닛/워런트 접미사와 이름 패턴으로 상당수 걸러지나 완전하지 않음 → U1
   검증 리포트에 잔여 비보통주 표본 점검 편입.

**ADR 포함 여부**: 초안은 **미국 상장 보통주와 동일 취급하여 포함**(NYSE/NASDAQ
상장 ADR은 거래·유동성 구조가 보통주와 같음). Security Name의 `"- American
Depositary"` 표기로 식별 가능하므로, 사용자가 제외를 원하면 접미사 필터에 추가.
→ **Q에 없던 항목이라 §4 뒤 "추가 확인"으로 상정**.

**생존편향 경고**: NASDAQ Trader 디렉터리는 **현행 상장만** 담는다(과거 시점
상장 목록·상폐 종목 없음). 따라서 무료 노선(Q2=현존 감수)에서는 이 목록이 곧
유니버스이고, 상폐 종목은 구조적으로 빠진다. 이것이 Q2의 핵심 트레이드오프이며,
KR P7 후속에서 언급된 "생존편향 상한(과거 시점 티커 목록)" 문제와 동일 성질.

**규모 추정**: 필터 후 보통주 ~5,000~6,000종목(마스터 플랜 §3 항목 11) → KR
2,580종목의 ~2배, 런타임 ~2배 → U4~U5는 분리 실행·야간 배치.

---

## §4 결정 질문 Q1~Q12 (권장안 + 사용자 답변)

> 권장안은 조사 결과로 확정한 값이다. **각 질문에 사용자 답변을 채워야 U0 DoD 충족.**
> 원칙(마스터 플랜 §1): 미국 데이터로 먼저 튜닝하지 않는다 — 단위·시장구조상
> 불가피한 변환(통화·지수·세금)만 허용.

| # | 질문 | 권장안(조사 확정) | 사용자 답변 |
|---|---|---|---|
| **Q1** | 데이터 소스는? | **하이브리드**(§4.1): Norgate 과거 base + Tiingo 증분 | **✅ 승인** — Norgate 무료체험 기간에 생존편향-free 과거 데이터(상폐 포함) 확보 → 이후 증분 갱신은 API 깔끔한 **Tiingo(1번)**로 전환(Stooq는 벌크 재다운로드 마찰이라 열세). ⚠ 체험판 데이터 깊이·약관은 U1 착수 전 검증(§4.1 캐비앗). |
| **Q2** | 생존편향: v1은 현존 상장만 감수(KR 동일 방침)? vs 보정 소스(유료) 채택? | **보정 채택**(Norgate 상폐 포함) | **✅ 승인(상향)** — KR보다 강한 방어. Q1 하이브리드로 과거 base가 생존편향-free가 되어 §6 최대 리스크를 v1부터 직접 공략. 절대 수치 캐비앗은 완화되나, 증분(Tiingo) 구간·근사 value엔 여전히 병기. |
| **Q3** | 수정주가: 배당 포함(total return) vs 분할만(KR 정합) | **소스 제공 방식 따름 + 차이 문서화** | **✅ 승인** — Norgate/Tiingo 모두 분할·배당 조정 close 제공 → 그대로 사용. 성장주는 배당수익률 낮아 영향 미미. Norgate는 조정/미조정 선택 가능하므로 U2에서 KR 정합(분할만) 대안도 열림(기록). |
| **Q4** | RS·시장필터 지수 매핑: 이중 구조 vs 단일 벤치마크 | **이중 구조 보존**(NYSE→S&P500, NASDAQ→나스닥 종합) | **✅ 승인** — KR KOSPI/KOSDAQ 이중 구조 정합, 엔진 `calendar_source: index`·`market_filter` 무수정 재사용. 지수 CSV 2종 수집(U1). |
| **Q5** | 유동성 하한: 100억원의 달러 환산값 | **$7M(직접 환산)** | **✅ 승인** — 마스터 플랜 §1 "통화 변환만 허용" 원칙 충실. 100억원 ÷ ~1,350~1,400 KRW/USD ≈ $7.1~7.4M → 라운드 $7M. U5에서 $5M/$10M 민감도. |
| **Q6** | 가격 하한(저가주 제외) 신설? KR엔 없음 | **신설 안 함(KR 정합)** | **✅ 승인** — 거래대금 게이트가 사실상 저가·저유동 필터. 오닐 $10 하한은 재튜닝이라 v1 미도입. |
| **Q7** | 계좌: 초기 자본·통화 | **$100,000 (1e5 USD)** | **✅ 승인** — KR 1억원과 규모 정합. |
| **Q8** | 기간: 워밍업·본검증 창 | **워밍업 2015-10~, 본검증 2017-01~최근** | **✅ 승인** — KR과 동일 창(비교 가능성). 전 소스 커버. |
| **Q9** | 비용: 커미션·슬리피지 | **커미션 0bp + 슬리피지 5bp + Section 31 계단** | **✅ 승인** — 미국 제로커미션 관행(KR 1.5bp보다 실측적) + 슬리피지 5bp 유지 + Section 31 계단(§2, ~0.2bp) + U5 민감도(15/30/50bp). FINRA TAF 무시(문서화). |
| **Q10** | 코드 적응: Market enum 일반화(정공) vs 라벨 재사용(무수정) | **일반화 정공(U3)** | **✅ 승인** — KR 골든 비트 동일 게이트 통과 조건. 라벨 재사용은 U3 착수 전 스모크 테스트에만 허용. |
| **Q11** | 고가주 처리: 1주 가격 > 슬롯 예산($20k) 종목의 미체결 허용? | **엔진 현행 동작 유지** | **✅ 승인** — 정수 주식 수, 예산 미달 시 자연 스킵 + U4에서 발생 빈도 확인. BRK.A 등 극소수. |
| **Q12** | 배치 커밋 정책: `data_us/`는 gitignore? | **gitignore 추가** | **✅ 승인** — 수집 재현은 스크립트 + 상태 파일로 보장. `out/us/` 대용량도 동일. |

**추가 확인(표 밖, 조사 중 발생)**:
- **ADR 포함 여부**(§3): **✅ 포함으로 승인**(2026-07-20) — 미국 상장 ADR을 보통주와 동일 취급하여 유니버스에 포함.
- **티커 정규화**(마스터 플랜 §3 항목 7): `BRK.B` 등 점·특수문자, Windows 예약어
  (`CON`/`PRN`/`AUX`) 파일명 충돌 → U1 정규화 규칙. U0 결정 불필요(U1 기술 항목),
  기록만.

### §4.1 Q1/Q2 하이브리드 데이터 아키텍처 (사용자 승인 2026-07-20)

**결정**: 단일 소스가 아니라 **역할 분리 하이브리드**:
- **과거 base (백테스트 본체, 2015-10 → 수집일)**: **Norgate** — 생존편향-free
  (상폐 포함)를 확보해 마스터 플랜 §6 "최대 리스크"를 v1부터 직접 공략. U-트랙
  백테스트(U4/U5)의 데이터는 사실상 전부 이 base에서 온다.
- **증분 갱신 (수집일 이후 순방향)**: **Tiingo** — 문서화된 API로 일일 증분이
  깔끔. Stooq는 벌크 재다운로드 + 2020 CAPTCHA 마찰이라 증분엔 열세(사용자 판단
  정합). 이후 라이브/데일리 스크리닝의 순방향 데이터 공급원.

**근거**: 무료 소스는 전부 상폐 미포함이라, 유료 품질 소스의 **무료체험/단기 구독**
창에 과거 base를 확보하고 이후는 무료 API로 유지하면 비용 최소로 생존편향을 없앤다.

**⚠ U1 착수 전 필수 검증 캐비앗**(2026-07-20 웹 API 과부하로 U0에서 재확인 실패):
1. **체험판 데이터 깊이** — 앞선 조사 문구는 "21일 무료체험 = **2년치** 데이터
   접근". 사실이면 체험판만으로 2015-10~ 전체 window 미확보 → **Norgate 유료
   1텀(최소 6개월) 결제** 또는 **EODHD 선회**가 대안. U1 첫 작업으로 실측 확인.
2. **라이선스·데이터 보존** — 구독/체험 종료 후 수집분을 백테스트에 계속 사용
   가능한지 Norgate 약관 확인(구독제 데이터 서비스는 통상 종료 후 사용 제한).
3. **조정 방법론 이음새** — Norgate-history + Tiingo-증분은 분할·배당 역조정 방식이
   달라 접합부(수집일 근처) 불연속 위험. 백테스트 창은 대부분 Norgate라 영향은
   최근 며칠뿐이나 U1에서 중첩 구간 대조 검증. 순방향 신규 상폐는 Tiingo가 조용히
   드롭하므로 라이브 운영 시 별도 캡처 필요(백테스트 창엔 무영향).

이 3건은 **U0 결정을 막지 않는다**(전략 결정은 확정). U1 데이터 계획(`u1_data.md`)의
선행 검증 항목으로 이월한다.

---

## §5 출처 (2026-07 웹 조사, 기억 아님)

**SEC Section 31 / FINRA TAF**
- SEC Fee Rate Advisories (index): https://www.sec.gov/rules-regulations/fee-rate-advisories
- SEC FY2025 Section 31 Advisory: https://www.sec.gov/rules-regulations/fee-rate-advisories/2025-2
- SEC FY2026 Section 31 Advisory: https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2
- Federal Register, FY2024 Order (Transaction Fee Rates): https://www.federalregister.gov/documents/2024/04/22/2024-08512/order-making-fiscal-year-2024-annual-adjustments-to-transaction-fee-rates
- Federal Register, FY2025 Order: https://www.federalregister.gov/documents/2025/04/11/2025-06214/order-making-fiscal-year-2025-annual-adjustments-to-transaction-fee-rates
- FINRA Information Notice 2025-04-24 (Section 31 rate): https://www.finra.org/rules-guidance/notices/information-notice-20250424
- FINRA Information Notice 2026-03-17 (Section 31 rate): https://www.finra.org/rules-guidance/notices/information-notice-20260317
- FINRA Trading Activity Fee: https://www.finra.org/rules-guidance/guidance/trading-activity-fee
- FINRA Fee Adjustment Schedule (SR-FINRA-2024-019): https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule
- TraderStatus SEC Fee Rates (컴파일, 교차확인용): https://traderstatus.com/traders/trader-info/sec-fee-rates/

**데이터 소스**
- Stooq 무료 히스토리 DB: https://stooq.com/db/h/ · 다운로드 도움말: https://www.chartoasis.com/free-data-download-stooq-help-cop3/
- yfinance: https://pypi.org/project/yfinance/ · 유효 티커 논의: https://github.com/ranaroussi/yfinance/discussions/1699
- Tiingo EOD 제품·가격: https://www.tiingo.com/products/end-of-day-stock-price-data · https://www.tiingo.com/documentation/
- Alpha Vantage 가격/한도: https://tradingtoolshub.com/review/alpha-vantage/
- EODHD 상폐/일괄: https://eodhd.com/financial-apis/delisted-stock-companies-data · 가격: https://eodhd.com/pricing
- Norgate: https://norgatedata.com/ · 구독: https://norgatedata.com/subscribe/subscribe.php
- Polygon(→Massive): https://massive.com/pricing

**유니버스·심볼**
- NASDAQ Trader Symbol Directory: https://www.nasdaqtrader.com/trader.aspx?id=symbollookup
- 필드 정의: https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs
- nasdaqlisted.txt: https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt
- otherlisted.txt: https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt

---

## §6 DoD·다음 단계

- **DoD 충족(2026-07-20)**: §4 Q1~Q12 + ADR 전부 사용자 승인 완료. U1·U2 착수 가능.
- 확정된 값의 흐름:
  - **U1(수집)** ← Q1·Q2 하이브리드(§4.1) + Q5 유동성 $7M + §3 유니버스 + §4.1 캐비앗 3건(선행 검증).
  - **U2(번역)** ← Q3 수정주가 + Q4 지수 이중구조 + Q9 비용 + §2 `costs_us.yaml` 초안.
  - **U3(코드)** ← Q10 Market enum 일반화 + Q11 고가주 현행.
- **U1 선행 검증(§4.1)**: Norgate 체험판 깊이·라이선스·이음새 3건을 `u1_data.md`
  첫 작업으로 실측 확인. 체험판이 2015-10~ 전체를 못 받히면 유료 1텀 or EODHD 선회.
