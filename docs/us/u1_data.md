# U1 — 데이터 수집 파이프라인

**목표**: U0에서 확정한 소스·유니버스로 미국장 일봉 데이터를 수집해
기존 엔진 로더(`CsvBarLoader`)가 무수정으로 읽는 `data_us/`를 만든다.

**선행**: U0 승인 완료(`plan/us/u0_decisions.md`). U2와 병행 가능.

## 설계 — `src/us_fetch/` (oneil_fetch 구조 미러)

| 모듈 | 책임 (oneil_fetch 대응) |
|---|---|
| universe.py | 심볼 목록 수집(U0 확정 원천) + 증권 유형 필터 |
| client.py | 소스 API/다운로드 클라이언트 (krx_client 대응) |
| transform.py | 표준 스키마 변환 + **value = close×volume 근사 생성** |
| writer.py | data_us/prices/{symbol}.csv, meta.csv, 지수 CSV |
| incremental.py / state.py | 증분 갱신(기존 파일 뒤에 신규 세션만 append) |
| cli.py / __main__.py | `python -m us_fetch --start --end --out data_us` |

**출력 스키마 — KR과 동일(로더 무수정이 목표):**

- `data_us/prices/{symbol}.csv`: date,open,high,low,close,volume,value
  (수정주가, value는 근사임을 README에 명기)
- `data_us/meta.csv`: symbol,name,market,listing_date,shares_out
  (market = U0 확정 라벨. listing_date 미확보 시 데이터 첫 세션으로 대체하고
  그 사실을 기록 — 워밍업 로직이 자동 방어)
- `data_us/sp500.csv`, `data_us/nasdaq.csv`: date,close (지수 — RS·시장필터·캘린더)

**티커 정규화 규칙(문서화 필수):**

- 클래스 주식 구분자 통일: `BRK.B`/`BRK-B`/`BRK/B` → 파일명 안전형 1개로 고정
- **Windows 예약어 충돌**: CON, PRN, AUX, NUL, COM1~9, LPT1~9 티커는 파일명으로
  쓸 수 없다 — 접미사 규칙(예: `PRN_.csv`) 정의 + meta에 원심볼 보존
- 대문자 고정, 정규화 매핑은 meta 생성 시 1곳에서만 수행

## 검증 (수집 후 필수)

1. **로더 전수 통과**: 전 파일을 CsvBarLoader.load()로 읽어 ValidationError 0건
   (컬럼·정렬·중복·NaN·high≥low).
2. **분할 이벤트 대조**: 분할 이력이 알려진 종목 3~5개(예: AAPL 2020-08 4:1,
   NVDA 2024-06 10:1)에서 분할일 전후 수정주가 연속성 확인 — 원시가 점프가
   남아 있으면 소스 수정주가 결함.
3. **지수 정합**: 종목 세션 날짜 ⊆ 지수 세션 날짜(캘린더 기준이 지수이므로).
   위반 종목 목록화 후 처리 방침 기록.
4. **증분 재실행 무변경**: 같은 날 재실행 시 파일 변경 0 (append-only 확인).
5. **규모 리포트**: 종목 수·기간 커버리지 분포·필터 단계별 탈락 수.

## 산출물·DoD

- src/us_fetch/ + `scripts/fetch_us.ps1`(ASCII 전용) + .gitignore에 `/data_us/` 추가
- data_us/ 전 유니버스 수집 완료(워밍업 2015-10~, U0 Q8 확정 창)
- 검증 1~5 전부 통과, 결과를 `plan/us/u1_data.md`에 기록
- 유닛테스트: transform(value 생성·정규화 매핑·예약어), incremental 경계

---

## 실행 프롬프트 (새 세션에 붙여넣기)

```
U트랙(미국장 전이 백테스트) U1 단계를 진행한다. 먼저 읽어라:
docs/us/us_plan.md(마스터), docs/us/u1_data.md(본 단계), plan/us/u0_decisions.md
(확정된 소스·유니버스·라벨 — 이 결정을 그대로 따른다. 재논의 금지).

참고 구현: src/oneil_fetch/ (KRX 수집기 — 구조를 미러하되 코드는 새로 작성),
src/oneil_bt/data/loader.py (출력이 통과해야 할 로더 검증).

작업: u1_data.md의 설계대로 src/us_fetch/ 패키지와 scripts/fetch_us.ps1을
구현하고, 전 유니버스를 data_us/에 수집한 뒤 검증 1~5를 수행해
plan/us/u1_data.md에 기록하라.

제약:
- KR 자산 불변: data/, src/oneil_fetch/, config/ 기존 파일 수정 금지.
  .gitignore에 /data_us/ 한 줄 추가만 허용.
- .ps1은 ASCII 전용. 파이썬: C:\Users\mh.han\repos\daytrading\.venv\Scripts\python.exe,
  PYTHONPATH=src, 콘솔은 -X utf8.
- 전 유니버스 수집이 오래 걸리면 분리 프로세스(detached cmd + 로그 + DONE 마커)
  로 돌리고 진행률을 로그로 남겨라. RAM 7.4GB — KR 엔진 런과 동시 실행 금지.
- 소스 호출 제한을 존중(백오프)하고, 수집 실패 심볼은 실패 목록으로 남겨 계속.

완료 기준: DoD 전부 + 유닛테스트 통과 + plan/us/u1_data.md 기록 +
docs/PROGRESS.md 갱신. 커밋은 코드·문서만(데이터는 gitignore).
```
