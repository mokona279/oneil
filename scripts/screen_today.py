"""오늘자 매수 후보 스크리너 + 보유종목 점검 + 매수금액 산정 (holdings 인식형).

백테스트 엔진의 규칙 컴포넌트(베이스·트렌드·RS·시장필터·품질·손절·청산·사이저)를 그대로
재사용해, 한 거래 세션을 전 종목에 대해 1회 평가한다. 증권계좌 자동연동은 없다 —
현금·보유종목을 `state/holdings.csv`에 적어두면 최신 종가로 평가액을 재계산해서:
  1) 보유종목 손절/청산 신호 점검(최신 종가 마크·손절도달·60MA 이탈·시장 방어),
  2) 그 평가액 기준으로 신규 후보의 매수 금액(비중x트랜치) 산정.

규칙 요약:
  매수(§4): 피벗(베이스 고점, 핸들 시 손잡이 고점) 장중 돌파, 피벗 +5% 이내 추격,
            돌파일 거래량 >= 20일평균x1.5.  분할 50/30/20, 2/3차 +2.5%/+5%.
  손절(§6①): 평단 - 2xATR, 손절폭 -10% 캡. 종가 도달 시 익일 시가 전량.
  사이징(§1): 비중 = min(20%, 1% / 손절폭%) — 자본 무관. 매수액 = 평가액 x 비중 x 트랜치.

holdings.csv 스키마 (헤더 필수, utf-8):
  symbol,qty,avg_price,entry_date,stop_price
  CASH,30000000,,,            # 현금: qty 칸에 원화 금액 (나머지 공란)
  005930,50,72000,2026-05-01,66000

사용:
  PYTHONPATH=src python scripts/screen_today.py \
      --price-dir data/prices --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
      --meta data/meta.csv --rules config/rules_v3-3.yaml --costs config/costs.yaml \
      --holdings state/holdings.csv --out-dir out/daily
  # --asof 생략 시 지수 마지막 세션. --holdings 없으면 --equity 로 사이징.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oneil_bt.data.csv_source import CsvDataSource
from oneil_bt.data.metadata import MetaRepository
from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import Market
from oneil_bt.domain.trade import Position
from oneil_bt.engine.context import build_market_context, build_symbol_context
from oneil_bt.portfolio.position_sizer import PositionSizer


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="오늘자 매수 후보 + 보유종목 점검")
    ap.add_argument("--price-dir", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--kospi", required=True)
    ap.add_argument("--kosdaq", required=False)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--costs", required=True)
    ap.add_argument("--asof", default=None, help="기준 세션 YYYY-MM-DD (생략 시 지수 마지막 세션)")
    ap.add_argument("--holdings", default=None, help="state/holdings.csv (현금·보유종목)")
    ap.add_argument("--cash", type=float, default=None, help="현금 override (holdings CASH행 대체)")
    ap.add_argument("--equity", type=float, default=1.0e8, help="holdings 없을 때 사이징 자본")
    ap.add_argument("--out-dir", default="out/daily", help="산출 디렉토리")
    return ap.parse_args()


def load_holdings(path: Path) -> tuple[float | None, dict]:
    """holdings.csv → (현금, {symbol: {qty, avg_price, entry_date, stop_price}})."""
    cash: float | None = None
    pos: dict = {}
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sym = (r.get("symbol") or "").strip()
            if not sym:
                continue
            if sym.upper() == "CASH":
                cash = float(r["qty"])
                continue
            pos[sym] = {
                "qty": int(float(r["qty"])),
                "avg_price": float(r["avg_price"]),
                "entry_date": (r.get("entry_date") or "").strip(),
                "stop_price": float(r["stop_price"]) if r.get("stop_price") else None,
            }
    return cash, pos


HELD_HEADER = (
    "symbol", "name", "market", "qty", "avg_price", "close", "value",
    "unreal_pnl_pct", "R_multiple", "stop_price", "dist_to_stop_pct",
    "signal", "below_60ma", "note",
)


def report_holdings(source, cfg, mkt, holdings, L):
    """보유종목을 최신 종가로 마크하고 손절/청산 신호를 판정. (rows, 주식평가액)."""
    rows = []
    stock_value = 0.0
    for sym, h in sorted(holdings.items()):
        try:
            meta = source.meta(sym)
        except Exception:
            rows.append({k: "" for k in HELD_HEADER} | {"symbol": sym, "note": "meta 없음"})
            continue
        m = meta.market
        prices = source.load_prices(sym)
        sc = build_symbol_context(sym, m, prices, mkt[m].index_prices, cfg)
        bar = prices.row(L)
        close = float(bar["close"]) if bar is not None else h["avg_price"]
        qty, avg, stop = h["qty"], h["avg_price"], h["stop_price"]
        value = close * qty
        stock_value += value
        risk = (avg - stop) if stop else None
        rmult = ((close - avg) / risk) if risk else None
        pos = Position(
            symbol=sym, market=m,
            entry_date=date.fromisoformat(h["entry_date"]) if h["entry_date"] else L,
            entry_price=avg, avg_price=avg, qty=qty,
            stop_price=stop if stop else avg * 0.9,
        )
        signals = []
        if stop and sc.stop.hit(pos, L):
            signals.append("STOP→전량매도")
        ex = sc.trend_exit.evaluate(pos, L)
        if ex and ex.is_sell:
            signals.append("60MA이탈→절반")
        ma60 = sc.ind.asof("ma60", L)
        defense = mkt[m].filter.state_asof(L).name == "DEFENSE"
        rows.append({
            "symbol": sym, "name": meta.name, "market": m.name, "qty": qty,
            "avg_price": round(avg, 2), "close": round(close, 2), "value": round(value),
            "unreal_pnl_pct": round((close / avg - 1) * 100, 2),
            "R_multiple": round(rmult, 2) if rmult is not None else "",
            "stop_price": round(stop, 2) if stop else "",
            "dist_to_stop_pct": round((close / stop - 1) * 100, 2) if stop else "",
            "signal": " · ".join(signals) if signals else "HOLD",
            "below_60ma": int(ma60 is not None and close < ma60),
            "note": "시장DEFENSE→해당시장 절반감축" if defense else "",
        })
    return rows, stock_value


def main() -> int:
    a = parse_args()
    index_paths: dict[Market, str] = {Market.KOSPI: a.kospi}
    if a.kosdaq:
        index_paths[Market.KOSDAQ] = a.kosdaq

    source = CsvDataSource(
        price_dir=a.price_dir, index_paths=index_paths,
        meta=MetaRepository.from_csv(a.meta),
    )
    cfg = Config.load(a.rules, a.costs)
    sizer = PositionSizer(cfg)
    mkt: dict[Market, object] = {
        m: build_market_context(m, source.load_index(m), cfg) for m in index_paths
    }
    kospi_idx = mkt[Market.KOSPI].index_prices.df.index
    L = date.fromisoformat(a.asof) if a.asof else kospi_idx[-1].date()

    # --- 보유종목 + 평가액 ---
    holdings: dict = {}
    cash = a.cash if a.cash is not None else 0.0
    if a.holdings:
        file_cash, holdings = load_holdings(Path(a.holdings))
        if a.cash is None and file_cash is not None:
            cash = file_cash
    held_rows, stock_value = [], 0.0
    if holdings:
        held_rows, stock_value = report_holdings(source, cfg, mkt, holdings, L)
    equity = (cash + stock_value) if (a.holdings or a.cash is not None) else a.equity

    print(f"[기준 세션] {L}")
    for m in mkt:
        st = mkt[m].filter.state_asof(L).name
        ok = mkt[m].filter.new_entry_allowed(L)
        print(f"[시장필터] {m.name}: 상태={st}  신규진입허용={ok}")
    if a.holdings:
        print(f"[계좌] 현금 {cash:,.0f} + 주식 {stock_value:,.0f} = 평가액 {equity:,.0f}  "
              f"(보유 {len(holdings)}종목)")
    else:
        print(f"[계좌] 평가액 {equity:,.0f} (holdings 미지정 — --equity 사용)")

    max_pos = cfg.portfolio.max_positions
    remaining_slots = max(0, max_pos - len(holdings))

    # --- 전 종목 스크리닝 ---
    chase = cfg.entry.chase_limit_pct / 100.0
    vol_mult = cfg.entry.breakout_volume_mult
    ratios = cfg.entry.tranche_ratios
    pyr = cfg.entry.pyramid_triggers_pct
    rows = []
    n_scanned = n_base = 0
    for sym in source.symbols():
        meta = source.meta(sym)
        m = meta.market
        if m not in mkt:
            continue
        prices = source.load_prices(sym)
        sc = build_symbol_context(sym, m, prices, mkt[m].index_prices, cfg)
        n_scanned += 1
        try:
            base = sc.detector.base_asof(L)
        except Exception:
            continue
        if base is None:
            continue
        bar = prices.row(L)
        if bar is None:
            continue
        n_base += 1
        close = float(bar["close"])
        pivot = base.pivot
        trend_ok = sc.trend.passes(L)
        rs_ok = sc.rs.passes(L)
        market_ok = mkt[m].filter.new_entry_allowed(L)
        q = sc.quality.passes(L, base)
        broke = sc.detector.is_breakout(L, base)
        all_gate = trend_ok and rs_ok and market_ok and q.passed

        atr = sc.ind.asof("atr14", L)
        vol_ma20 = sc.ind.asof("vol_ma20", L)
        stop = sc.stop.stop_price(pivot, atr) if atr else None
        stop_pct = (1.0 - stop / pivot) * 100.0 if stop else None
        gap = (pivot / close - 1.0) * 100.0
        weight = sizer.target_weight(pivot, atr) if atr else None
        target_amt = equity * weight if weight else None
        t1_amt = target_amt * ratios[0] if target_amt else None
        t1_shares = int(t1_amt // pivot) if t1_amt else None

        if broke and close <= pivot * (1 + chase):
            bucket = "1_BROKE_OUT"
        elif -2.0 <= gap <= chase * 100:
            bucket = "2_AT_PIVOT"
        elif chase * 100 < gap <= 8.0:
            bucket = "3_NEAR"
        else:
            bucket = "4_FORMING"

        rows.append({
            "symbol": sym, "name": meta.name, "market": m.name,
            "held": int(sym in holdings), "bucket": bucket, "all_gate": int(all_gate),
            "trend": int(trend_ok), "rs": int(rs_ok), "market_ok": int(market_ok),
            "overheat_ok": int(q.not_overheated), "atr_ok": int(q.atr_ok),
            "contraction_ok": int(q.contraction_ok), "dryup_ok": int(q.dryup_ok),
            "close": round(close, 2), "pivot": round(pivot, 2),
            "gap_to_pivot_pct": round(gap, 2),
            "buy_zone_low": round(pivot, 2), "buy_zone_high": round(pivot * (1 + chase), 2),
            "weight_pct": round(weight * 100, 2) if weight else "",
            "target_amount": round(target_amt) if target_amt else "",
            "t1_amount": round(t1_amt) if t1_amt else "",
            "t1_shares": t1_shares if t1_shares else "",
            "t2_trigger": round(pivot * (1 + pyr[0] / 100), 2),
            "t3_trigger": round(pivot * (1 + pyr[1] / 100), 2),
            "stop_price": round(stop, 2) if stop else "",
            "stop_pct": round(stop_pct, 2) if stop_pct else "",
            "risk_per_share": round(pivot - stop, 2) if stop else "",
            "vol_needed": round(vol_ma20 * vol_mult) if vol_ma20 else "",
            "stage": base.stage, "depth_pct": round(base.depth_pct, 1),
            "tier": base.tier, "weeks": round(base.weeks_elapsed, 1),
            "handle": int(base.handle),
        })

    rows.sort(key=lambda r: (-r["all_gate"], r["bucket"], abs(r["gap_to_pivot_pct"])))

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "buy_candidates.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    if held_rows:
        with (out_dir / "holdings_report.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(HELD_HEADER))
            w.writeheader()
            w.writerows(held_rows)

    # --- 콘솔 요약 ---
    if held_rows:
        print(f"\n=== 보유종목 점검 ({len(held_rows)}종목, 잔여 슬롯 {remaining_slots}/{max_pos}) ===")
        print(f"{'코드':>6} {'종목명':<11} {'현재가':>9} {'평단':>9} {'손익%':>7} "
              f"{'R':>5} {'손절가':>9} {'스탑까지%':>8} {'신호':<18}")
        for r in held_rows:
            print(f"{r['symbol']:>6} {str(r['name'])[:10]:<11} {(r['close'] or 0):>9,.0f} "
                  f"{(r['avg_price'] or 0):>9,.0f} {(r['unreal_pnl_pct'] or 0):>7.1f} "
                  f"{str(r['R_multiple']):>5} {(r['stop_price'] or 0):>9,.0f} "
                  f"{(r['dist_to_stop_pct'] or 0):>8.1f} {str(r['signal']):<18}")

    new = [r for r in rows if not r["held"]]
    actionable = [r for r in new if r["all_gate"] and r["bucket"] in ("1_BROKE_OUT", "2_AT_PIVOT")]
    watch = [r for r in new if r["trend"] and r["rs"] and r["bucket"] in ("2_AT_PIVOT", "3_NEAR")]
    print(f"\n스캔 {n_scanned} · 유효베이스 {n_base} · 즉시매수 {len(actionable)} · "
          f"관심(트렌드+RS·근접) {len(watch)}  -> {out_dir}")

    def show(title: str, lst: list, k: int = 25) -> None:
        print(f"\n=== {title} ===")
        if not lst:
            print("  (없음)")
            return
        print(f"{'코드':>6} {'종목명':<11} {'시장':<6} {'현재가':>9} {'피벗':>9} {'갭%':>6} "
              f"{'비중%':>5} {'1차매수액':>11} {'수량':>6} {'손절가':>9} {'단계':>3}")
        for r in lst[:k]:
            print(f"{r['symbol']:>6} {r['name'][:10]:<11} {r['market']:<6} "
                  f"{r['close']:>9,.0f} {r['pivot']:>9,.0f} {r['gap_to_pivot_pct']:>6.1f} "
                  f"{str(r['weight_pct']):>5} {(r['t1_amount'] or 0):>11,.0f} "
                  f"{str(r['t1_shares']):>6} {(r['stop_price'] or 0):>9,.0f} {r['stage']:>3}")

    if remaining_slots == 0 and holdings:
        print("\n[알림] 잔여 슬롯 0 — 신규 매수 불가(8종목 만기). 아래는 참고용.")
    show("① 즉시 매수 (전 게이트 통과, 매수액 포함)", actionable)
    if not actionable:
        show("① 대체표 — 트렌드+RS 통과·피벗근접 (시장필터만 회복되면 후보)", watch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
