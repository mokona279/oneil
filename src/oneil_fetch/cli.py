"""수집 오케스트레이션 + CLI (계획서 §3.2, §4, §5.5).

동작 순서(§4): (1) 지수 2개 → (2) 유니버스 확정 → (3) 종목 루프(증분→수집→정제→
자기검증→쓰기→체크포인트) → (4) meta.csv → (5) 최종 리포트.

종목 하나의 실패는 전체를 중단시키지 않는다. 실패 목록에 기록하고 계속, 마지막에 요약 +
exit code 1(전부 성공 시 0).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd

from . import KOSDAQ_INDEX_CODE, KOSPI_INDEX_CODE
from .env_loader import load_env_file
from .incremental import decide_fetch, merge_incremental
from .krx_client import KrxClient, PykrxClient
from .meta_builder import build_meta_rows, load_meta_fallback, normalize_listing_dates
from .transform import clean_bars, normalize_index, normalize_ohlcv
from .universe import is_common_stock, select_universe
from .writer import write_index, write_meta, write_prices
from .state import FetchState

_MARKETS = ("KOSPI", "KOSDAQ")
_INDEX_CODES = {"KOSPI": KOSPI_INDEX_CODE, "KOSDAQ": KOSDAQ_INDEX_CODE}
_CHECKPOINT_EVERY = 20


# --------------------------------------------------------------------------- #
# 유틸
# --------------------------------------------------------------------------- #
def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_symbols(spec: str) -> list[str]:
    """--symbols 값을 심볼 리스트로. '@파일'이면 파일에서 줄 단위로 읽는다."""
    if spec.startswith("@"):
        text = Path(spec[1:]).read_text(encoding="utf-8")
        raw = text.replace(",", "\n").split("\n")
    else:
        raw = spec.split(",")
    return [s.strip().zfill(6) for s in raw if s.strip()]


def _prices_dir(out: Path) -> Path:
    return out / "prices"


def _price_path(out: Path, symbol: str) -> Path:
    return _prices_dir(out) / f"{symbol}.csv"


def _load_existing(path: Path) -> pd.DataFrame | None:
    """이미 저장된 종목 CSV를 normalize_ohlcv 스키마(date 문자열)로 읽는다. 없으면 None."""
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"date": str})
    return df


# --------------------------------------------------------------------------- #
# 시장 맵 · 유니버스
# --------------------------------------------------------------------------- #
def build_market_maps(
    client: KrxClient, on_date: date
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """KOSPI·KOSDAQ 티커 목록 → (티커→시장, 시장→티커목록). 2호출."""
    ticker_to_market: dict[str, str] = {}
    by_market: dict[str, list[str]] = {}
    for market in _MARKETS:
        tickers = client.tickers(_yyyymmdd(on_date), market)
        by_market[market] = list(tickers)
        for t in tickers:
            ticker_to_market[t] = market
    return ticker_to_market, by_market


def resolve_universe(
    client: KrxClient,
    args: argparse.Namespace,
    ticker_to_market: dict[str, str],
    by_market: dict[str, list[str]],
) -> tuple[list[str], dict[str, str]]:
    """대상 심볼과 그 이름 맵을 확정한다.

    --symbols 지정 시 유니버스 산출을 생략하고 그 종목만. 아니면 --market 범위의 티커를
    보통주/스팩 필터로 거른다(§5.4). 이름은 필터 이후 후보에 대해서만 조회(호출 절약).
    """
    if args.symbols:
        symbols = parse_symbols(args.symbols)
        names = {s: _safe_name(client, s) for s in symbols}
        return symbols, names

    scope = _MARKETS if args.market == "all" else (args.market.upper(),)
    candidates: list[str] = []
    for market in scope:
        for t in by_market.get(market, []):
            if args.include_non_common or is_common_stock(t):
                candidates.append(t)

    names = {t: _safe_name(client, t) for t in candidates}
    symbols = select_universe(
        candidates, names, include_non_common=args.include_non_common
    )
    return symbols, names


def _safe_name(client: KrxClient, ticker: str) -> str:
    try:
        return client.ticker_name(ticker)
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# 지수
# --------------------------------------------------------------------------- #
def fetch_indices(
    client: KrxClient, out: Path, start: date, end: date
) -> dict[str, str]:
    """kospi.csv / kosdaq.csv 수집·정제·검증·저장. 반환: 시장→마지막 거래일(ISO)."""
    last_dates: dict[str, str] = {}
    for market, code in _INDEX_CODES.items():
        raw = client.index_ohlcv(_yyyymmdd(start), _yyyymmdd(end), code)
        norm = normalize_index(raw)
        filename = f"{market.lower()}.csv"
        write_index(norm, out / filename)
        if len(norm):
            last_dates[market] = norm["date"].iloc[-1]
    return last_dates


# --------------------------------------------------------------------------- #
# 종목 1개 수집
# --------------------------------------------------------------------------- #
def fetch_symbol(
    client: KrxClient,
    symbol: str,
    existing: pd.DataFrame | None,
    start: date,
    end: date,
    *,
    full_refresh: bool,
) -> tuple[pd.DataFrame, dict]:
    """종목 1개를 증분 판단→수집→정제→(오버랩 검증)해 최종 프레임을 만든다.

    반환: (최종 df, 리포트 조각). 정제 후 0행이면 ValueError.
    """
    decision = (
        decide_fetch(None, start, end)
        if full_refresh
        else decide_fetch(existing, start, end)
    )
    raw = client.ohlcv(_yyyymmdd(decision.fromdate), _yyyymmdd(decision.todate), symbol)
    clean, stats = clean_bars(normalize_ohlcv(raw))

    mode = decision.mode
    appended = len(clean)
    if decision.mode == "incremental" and existing is not None:
        merge = merge_incremental(existing, clean)
        if merge.action == "refetch_full":
            mode = "refetch_full"
            raw = client.ohlcv(_yyyymmdd(start), _yyyymmdd(end), symbol)
            clean, stats = clean_bars(normalize_ohlcv(raw))
            final = clean
            appended = len(clean)
        else:
            final = merge.df
            appended = merge.appended
    else:
        final = clean

    if final is None or len(final) == 0:
        raise ValueError("정제 후 유효 행 0개")

    piece = {
        "rows": len(final),
        "date_range": [final["date"].iloc[0], final["date"].iloc[-1]],
        "mode": mode,
        "appended": appended,
        "clean": {
            "halt_fixed": stats.halt_fixed,
            "clamped": stats.clamped,
            "dropped_nonpositive": stats.dropped_nonpositive,
            "dropped_integrity": stats.dropped_integrity,
            "dropped_nan": stats.dropped_nan,
        },
    }
    return final, piece


# --------------------------------------------------------------------------- #
# 메타
# --------------------------------------------------------------------------- #
def build_and_write_meta(
    client: KrxClient,
    out: Path,
    symbols: list[str],
    names: dict[str, str],
    ticker_to_market: dict[str, str],
    end: date,
) -> dict:
    """prices/에 실제로 저장된 심볼 전수를 덮는 meta.csv를 만든다 (§1.3 불변식)."""
    on_disk = sorted(p.stem for p in _prices_dir(out).glob("*.csv"))
    listing = normalize_listing_dates(client.listing_dates())
    shares: dict[str, int] = {}
    for market in _MARKETS:
        cap = client.market_cap(_yyyymmdd(end), market)
        for ticker, n in zip(cap.index, cap["상장주식수"]):
            shares[str(ticker).zfill(6)] = int(n)

    name_map = dict(names)
    for sym in on_disk:
        name_map.setdefault(sym, _safe_name(client, sym))

    # 거래정지·상폐 절차로 당일 상장 목록에서 빠진 on-disk 심볼은 이전 meta에서 복원
    # (2026-07-17 012510 실측 — 없으면 빈 market이 파일 전체 검증을 죽인다).
    fallback = load_meta_fallback(out / "meta.csv")
    result = build_meta_rows(
        on_disk, name_map, ticker_to_market, listing, shares, fallback=fallback
    )
    if result.market_missing:
        raise ValueError(
            "market 미해결 심볼(상장 목록·이전 meta 모두 없음): "
            + ", ".join(result.market_missing[:20])
        )
    write_meta(result.rows, out / "meta.csv")
    return {
        "symbols": len(result.rows),
        "missing_listing_date": result.missing_listing_date,
        "market_fallback": result.market_fallback,
    }


# --------------------------------------------------------------------------- #
# 오케스트레이션
# --------------------------------------------------------------------------- #
def run_fetch(client: KrxClient, args: argparse.Namespace) -> tuple[dict, int]:
    """전체 수집을 실행하고 (리포트, exit_code)를 반환한다."""
    started = time.time()
    out = Path(args.out)
    state_dir = out / "_state"
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    ticker_to_market, by_market = build_market_maps(client, end)
    symbols, names = resolve_universe(client, args, ticker_to_market, by_market)

    if args.dry_run:
        report = {
            "dry_run": True,
            "markets": args.market,
            "range": [args.start, args.end],
            "universe_size": len(symbols),
            "sample": symbols[:20],
            "note": "dry-run은 티커목록만 조회한다. 지수·종목·메타 네트워크 수집은 건너뜀.",
        }
        print(_format_report(report))
        return report, 0

    state = FetchState.load(state_dir)
    if args.full_refresh:
        state.reset()
    state.last_end = args.end

    index_last = {} if args.skip_index else fetch_indices(client, out, start, end)
    target_last = max(index_last.values()) if index_last else args.end

    per_symbol: dict[str, dict] = {}
    skipped: list[str] = []
    for i, sym in enumerate(symbols, start=1):
        if sym not in ticker_to_market:
            state.mark_failed(sym, "시장 불명(KOSPI/KOSDAQ 목록에 없음 — 상폐 가능)")
            continue

        path = _price_path(out, sym)
        existing = _load_existing(path)
        if (
            not args.full_refresh
            and existing is not None
            and len(existing)
            and str(existing["date"].iloc[-1]) >= target_last
        ):
            skipped.append(sym)
            state.mark_completed(sym)
            continue

        try:
            final, piece = fetch_symbol(
                client, sym, existing, start, end, full_refresh=args.full_refresh
            )
            write_prices(final, path)
            per_symbol[sym] = piece
            state.mark_completed(sym)
        except Exception as exc:  # 개별 실패는 계속 진행 (§4)
            state.mark_failed(sym, f"{type(exc).__name__}: {exc}")

        if i % _CHECKPOINT_EVERY == 0:
            state.save(state_dir)

    meta_info: dict = {"skipped": True}
    if not args.skip_meta:
        try:
            meta_info = build_and_write_meta(
                client, out, symbols, names, ticker_to_market, end
            )
        except Exception as exc:
            meta_info = {"error": f"{type(exc).__name__}: {exc}"}

    state.save(state_dir)

    report = {
        "range": [args.start, args.end],
        "universe_size": len(symbols),
        "succeeded": len(per_symbol),
        "skipped_up_to_date": len(skipped),
        "failed": state.failed,
        "meta": meta_info,
        "elapsed_sec": round(time.time() - started, 1),
        "per_symbol": per_symbol,
    }
    (state_dir).mkdir(parents=True, exist_ok=True)
    (state_dir / "fetch_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(_format_report(report))
    exit_code = 1 if state.failed else 0
    return report, exit_code


def _format_report(report: dict) -> str:
    if report.get("dry_run"):
        lines = [
            "── DRY-RUN 계획 ──",
            f"시장 범위     : {report['markets']}",
            f"기간          : {report['range'][0]} ~ {report['range'][1]}",
            f"유니버스 크기 : {report['universe_size']}",
            f"샘플          : {', '.join(report['sample'])}",
            report["note"],
        ]
        return "\n".join(lines)

    lines = [
        "── 수집 리포트 ──",
        f"기간              : {report['range'][0]} ~ {report['range'][1]}",
        f"유니버스          : {report['universe_size']}",
        f"성공              : {report['succeeded']}",
        f"스킵(최신)        : {report['skipped_up_to_date']}",
        f"실패              : {len(report['failed'])}",
        f"소요(초)          : {report['elapsed_sec']}",
    ]
    meta = report["meta"]
    if "symbols" in meta:
        missing = meta["missing_listing_date"]
        lines.append(f"메타 종목         : {meta['symbols']}")
        lines.append(f"상장일 결측       : {len(missing)}")
        if missing:
            lines.append(f"  예: {', '.join(missing[:10])}")
        fb = meta.get("market_fallback") or []
        if fb:
            lines.append(
                f"시장 결측 대체    : {len(fb)} (이전 meta 재사용: {', '.join(fb[:10])})"
            )
    elif "error" in meta:
        lines.append(f"메타 생성 실패    : {meta['error']}")
    if report["failed"]:
        lines.append("실패 종목:")
        for sym, reason in list(report["failed"].items())[:20]:
            lines.append(f"  {sym}: {reason}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m oneil_fetch",
        description="한국 주식 실데이터 수집 (pykrx/FDR → 엔진 CSV 레이아웃)",
    )
    p.add_argument(
        "--start",
        required=True,
        help="수집 시작일 YYYY-MM-DD. 워밍업 위해 백테스트 시작보다 최소 15개월 앞으로(§1.4)",
    )
    p.add_argument("--end", default=date.today().isoformat(), help="수집 종료일(기본 오늘)")
    p.add_argument("--out", default="data", help="출력 디렉토리(기본 data)")
    p.add_argument(
        "--symbols",
        default=None,
        help="지정 시 유니버스 산출 생략, 이 종목만(쉼표 또는 @파일)",
    )
    p.add_argument(
        "--market", choices=["kospi", "kosdaq", "all"], default="all",
        help="유니버스 산출 범위(기본 all)",
    )
    p.add_argument(
        "--include-non-common", action="store_true",
        help="우선주 등 비보통주 포함(기본: 보통주만, §5.4)",
    )
    p.add_argument(
        "--env-file", default=".env",
        help="KRX_ID/KRX_PW 등 자격증명 .env 경로(기본 .env). pykrx가 KRX 데이터 "
        "엔드포인트 접근에 로그인 세션을 요구한다. 파일이 없으면 조용히 건너뜀.",
    )
    p.add_argument("--sleep", type=float, default=0.5, help="요청 간 sleep 초(기본 0.5)")
    p.add_argument(
        "--full-refresh", action="store_true",
        help="증분 무시, 전 종목 전체 재수집",
    )
    p.add_argument("--skip-meta", action="store_true", help="meta.csv 생성 생략")
    p.add_argument("--skip-index", action="store_true", help="지수 수집 생략")
    p.add_argument(
        "--dry-run", action="store_true",
        help="유니버스·계획만 출력(티커목록 외 네트워크 호출 없음)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # pykrx import 전에 자격증명을 환경에 주입한다(로그인 세션 필요).
    if args.env_file and Path(args.env_file).exists():
        keys = load_env_file(args.env_file)
        print(f"[env] {args.env_file}에서 로드: {', '.join(keys)}")
    client = PykrxClient(sleep_sec=args.sleep)
    _, exit_code = run_fetch(client, args)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
