"""포워드 섀도 원장 — 데일리 스크린 결과를 append-only로 봉인 (P8-1).

목적: 백테스트가 아닌 **진짜 OOS 증거** 축적. 매 세션의 매수 후보(전 게이트
통과·피벗권)와 시장필터 상태를, 결과를 알기 전에 기록해 둔다. 나중에 이 원장의
신호 vs 실제 주가 경과를 대조하면 전략의 포워드 성과가 문서로 남는다
(plan/p7_adversarial.md §9 — 유일한 진짜 OOS 수단).

설계 원칙:
- 스크리너(screen_today.py)는 수정하지 않는다 — 산출물(buy_candidates.csv)을
  후처리로 읽는다. 시장필터 상태만 동일 모듈(build_market_context)로 재계산.
- append-only: 같은 세션(asof)이 이미 기록돼 있으면 추가하지 않는다(선기록 우선).
  기록 파일은 forward/ (gitignore 밖) — 커밋하면 git 이력이 봉인 역할을 한다.
- 보유종목·현금(state/)은 개인정보라 원장에 넣지 않는다. 전략 신호만 기록.

산출:
- forward/sessions.csv — 세션당 1행: 시장필터 상태, 후보 수, 규칙 파일 해시.
- forward/signals.csv  — 후보당 1행: actionable(즉시매수) / watch(관심) 구분.
- forward/status.md    — 진행 현황 요약(매 실행 재생성).

사용 (daily.ps1이 스크리닝 직후 호출):
    python scripts/forward_ledger.py --candidates out/daily/<date>/buy_candidates.csv \
        --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
        --rules config/rules_v3-3.yaml --costs config/costs.yaml --out forward
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oneil_bt.data.loader import CsvBarLoader
from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import Market
from oneil_bt.engine.context import build_market_context

SESSIONS_HEADER = (
    "asof", "kospi_state", "kospi_entry_allowed", "kosdaq_state",
    "kosdaq_entry_allowed", "n_actionable", "n_watch",
    "rules_sha8", "costs_sha8", "recorded_at",
)
SIGNALS_HEADER = (
    "asof", "kind", "symbol", "name", "market", "bucket", "close", "pivot",
    "gap_to_pivot_pct", "buy_zone_high", "weight_pct", "t1_amount",
    "stop_price", "stop_pct", "stage", "tier", "weeks", "handle", "recorded_at",
)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="포워드 섀도 원장 기록")
    ap.add_argument("--candidates", required=True, help="buy_candidates.csv 경로")
    ap.add_argument("--kospi", required=True)
    ap.add_argument("--kosdaq", default=None)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--costs", required=True)
    ap.add_argument("--out", default="forward", help="원장 디렉토리")
    return ap.parse_args()


def sha8(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:8]


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def append_rows(path: Path, header: tuple[str, ...], rows: list[dict]) -> None:
    new_file = not path.exists()
    mode = "w" if new_file else "a"
    with path.open(mode, newline="", encoding="utf-8-sig" if new_file else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(header))
        if new_file:
            w.writeheader()
        w.writerows(rows)


def write_status(out_dir: Path) -> None:
    sessions = read_rows(out_dir / "sessions.csv") if (out_dir / "sessions.csv").exists() else []
    signals = read_rows(out_dir / "signals.csv") if (out_dir / "signals.csv").exists() else []
    n_act = sum(1 for s in signals if s["kind"] == "actionable")
    n_watch = len(signals) - n_act
    defense = sum(1 for s in sessions
                  if s["kospi_state"] == "DEFENSE" or s["kosdaq_state"] == "DEFENSE")
    lines = [
        "# 포워드 검증 진행 현황",
        "",
        "매 데일리 런이 그날의 신호를 결과를 알기 전에 봉인한 원장의 요약이다.",
        "이 파일은 매 실행 재생성된다 — 원본은 sessions.csv / signals.csv (append-only).",
        "",
        f"- 기록 세션: **{len(sessions)}개**"
        + (f" ({sessions[0]['asof']} ~ {sessions[-1]['asof']})" if sessions else ""),
        f"- 누적 신호: actionable {n_act} · watch {n_watch}",
        f"- 방어(DEFENSE) 세션: {defense}",
        "",
        "| asof | KOSPI | KOSDAQ | 즉시매수 | 관심 | 기록 시각 |",
        "|---|---|---|---|---|---|",
    ]
    for s in sessions[-10:]:
        lines.append(
            f"| {s['asof']} | {s['kospi_state']} | {s['kosdaq_state']} "
            f"| {s['n_actionable']} | {s['n_watch']} | {s['recorded_at']} |"
        )
    lines += [
        "",
        "검증 방법(데이터가 쌓인 뒤): signals.csv의 actionable 신호별로 이후 실제",
        "주가(피벗 대비 경과·손절 도달 여부)를 대조한다. 원장 봉인을 위해 매 런 후",
        "`git add forward && git commit`을 권장한다.",
        "",
    ]
    (out_dir / "status.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    a = parse_args()
    cand_path = Path(a.candidates)
    if not cand_path.exists():
        print(f"[오류] 후보 파일 없음: {cand_path}", file=sys.stderr)
        return 2
    # 신선도 가드 — 지수 데이터가 후보 파일보다 새로우면 asof가 어긋난다
    if cand_path.stat().st_mtime < Path(a.kospi).stat().st_mtime:
        print("[오류] buy_candidates.csv가 지수 데이터보다 오래됨 — 스크리너를 먼저"
              " 다시 돌려야 원장의 세션 스탬프가 맞다", file=sys.stderr)
        return 2

    cfg = Config.load(a.rules, a.costs)
    loader = CsvBarLoader()
    index_paths = {Market.KOSPI: a.kospi}
    if a.kosdaq:
        index_paths[Market.KOSDAQ] = a.kosdaq
    mkt = {
        m: build_market_context(m, loader.load_index(p, symbol=f"INDEX_{m}"), cfg)
        for m, p in index_paths.items()
    }
    asof = mkt[Market.KOSPI].index_prices.df.index[-1].date()

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sessions_path = out_dir / "sessions.csv"
    if sessions_path.exists():
        if any(s["asof"] == asof.isoformat() for s in read_rows(sessions_path)):
            print(f"[포워드 원장] {asof} 이미 기록됨 — 추가 없음(선기록 우선)")
            write_status(out_dir)
            return 0

    rows = read_rows(cand_path)
    new = [r for r in rows if r["held"] == "0"]
    actionable = [r for r in new if r["all_gate"] == "1"
                  and r["bucket"] in ("1_BROKE_OUT", "2_AT_PIVOT")]
    watch = [r for r in new if r["trend"] == "1" and r["rs"] == "1"
             and r["bucket"] in ("2_AT_PIVOT", "3_NEAR")]

    now = datetime.now().isoformat(timespec="seconds")

    def signal_row(r: dict, kind: str) -> dict:
        return {"asof": asof.isoformat(), "kind": kind, "recorded_at": now} | {
            k: r.get(k, "") for k in SIGNALS_HEADER
            if k not in ("asof", "kind", "recorded_at")
        }

    sig_rows = ([signal_row(r, "actionable") for r in actionable]
                + [signal_row(r, "watch") for r in watch])
    append_rows(out_dir / "signals.csv", SIGNALS_HEADER, sig_rows)

    state = {m: mkt[m].filter.state_asof(asof).name for m in mkt}
    allowed = {m: mkt[m].filter.new_entry_allowed(asof) for m in mkt}
    append_rows(sessions_path, SESSIONS_HEADER, [{
        "asof": asof.isoformat(),
        "kospi_state": state.get(Market.KOSPI, ""),
        "kospi_entry_allowed": int(allowed.get(Market.KOSPI, False)),
        "kosdaq_state": state.get(Market.KOSDAQ, ""),
        "kosdaq_entry_allowed": int(allowed.get(Market.KOSDAQ, False)),
        "n_actionable": len(actionable), "n_watch": len(watch),
        "rules_sha8": sha8(a.rules), "costs_sha8": sha8(a.costs),
        "recorded_at": now,
    }])
    write_status(out_dir)
    print(f"[포워드 원장] {asof} 기록 — actionable {len(actionable)} · watch {len(watch)}"
          f" -> {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
