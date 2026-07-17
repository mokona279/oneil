"""P6 시장필터 지수 스트레스 테스트 (plan/p6_2001_stress.md, P5 §8 권고).

규칙서 §2 시장필터(entry 60MA · defense 120MA · 복귀 3거래일)를 지수 종가 단독으로
장기 구간(2000~)에 돌려 "방어가 제때 켜지고 제때 풀리는가"만 측정한다. 종목 매매
성과는 재현하지 않는다 — 생존편향·체결모델과 무관한 필터 상태머신 타이밍 테스트다.

상태 계산은 엔진과 동일 코드(oneil_bt.rules.market_filter.build_market_states)를
직접 임포트하고, MA는 MovingAverage와 동일 산식(rolling mean, min_periods=창)이다.

에피소드 규약 — 엔진 타이밍(new_entry_allowed(d) = 상태(d-1)==NORMAL)과 일치:
  차단 에피소드 = 상태가 NORMAL을 벗어난 최대 연속 구간 [s..e], 재허용 기준일 r =
  e 다음 첫 NORMAL 세션(진입 재개는 r+1부터). s 당일 진입은 아직 허용된다.
  ① dd_at_block      = close(s)/직전 NORMAL 런 피크 - 1   → 필터가 못 막는 낙폭
  ② lag_peak_to_defense · dd_at_defense (+ 252일 고점 기준 lag252 · dd252)
  ③ whipsaw_cost     = close(r)/close(s) - 1               → 차단 왕복 비용(양수=재진입 프리미엄)
  ④ bottom_to_reentry_sessions · missed_from_bottom       → 회복 재진입 랙

사용:
    PYTHONPATH=src python scripts/market_filter_stress.py \
        --index kospi=out/stress2001/kospi_full.csv \
        --index kosdaq=out/stress2001/kosdaq_full.csv \
        --start 2000-01-01 --out-dir out/stress2001
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from oneil_bt.domain.enums import MarketState  # noqa: E402
from oneil_bt.rules.market_filter import build_market_states  # noqa: E402

ENTRY_WINDOW = 60
DEFENSE_WINDOW = 120
RECOVER_DAYS = 3
HIGH_LOOKBACK = 252  # ② 보조 기준: 트레일링 52주 고점


# --------------------------------------------------------------------------- #
# 상태 계산
# --------------------------------------------------------------------------- #
def load_close(path: Path | str) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["date"])
    close = df.set_index("date")["close"].astype(float)
    assert close.index.is_unique and close.index.is_monotonic_increasing
    assert close.notna().all() and (close > 0).all()
    return close


def compute_states(
    close: pd.Series,
    entry_window: int = ENTRY_WINDOW,
    defense_window: int = DEFENSE_WINDOW,
    recover_days: int = RECOVER_DAYS,
) -> pd.Series:
    """MovingAverage와 동일 산식으로 MA를 만들어 엔진 상태머신에 넣는다."""
    entry_ma = close.rolling(entry_window, min_periods=entry_window).mean()
    defense_ma = close.rolling(defense_window, min_periods=defense_window).mean()
    return build_market_states(close, entry_ma, defense_ma, recover_days)


# --------------------------------------------------------------------------- #
# 차단 에피소드 추출
# --------------------------------------------------------------------------- #
def extract_episodes(close: pd.Series, states: pd.Series) -> pd.DataFrame:
    """NORMAL 이탈 최대 연속 구간별 지표. 워밍업(첫 NORMAL 이전)은 제외한다."""
    assert len(close) == len(states)
    n = states.eq(MarketState.NORMAL).to_numpy()
    is_def = states.eq(MarketState.DEFENSE).to_numpy()
    c = close.to_numpy()
    idx = close.index
    trail_high = close.rolling(HIGH_LOOKBACK, min_periods=1).max().to_numpy()

    rows: list[dict] = []
    # 차단 시작 s: n[s-1] and not n[s]
    starts = np.flatnonzero(n[:-1] & ~n[1:]) + 1
    for s in starts:
        # 직전 NORMAL 런 시작
        run_start = s - 1
        while run_start > 0 and n[run_start - 1]:
            run_start -= 1
        peak_pos = run_start + int(np.argmax(c[run_start : s + 1]))
        peak = c[peak_pos]

        # 차단 런 끝 e, 재허용 r
        after = np.flatnonzero(n[s:])
        r = s + int(after[0]) if len(after) else None
        e = (r - 1) if r is not None else len(c) - 1

        seg = c[s : e + 1]
        bottom_pos = s + int(np.argmin(seg))
        def_seg = np.flatnonzero(is_def[s : e + 1])
        d = s + int(def_seg[0]) if len(def_seg) else None

        row = dict(
            block_date=idx[s],
            peak_date=idx[peak_pos],
            peak=peak,
            dd_at_block=c[s] / peak - 1.0,
            kind="defense" if d is not None else "caution",
            blocked_sessions=(r - s) if r is not None else np.nan,
            reentry_date=idx[r] if r is not None else pd.NaT,
            whipsaw_cost=(c[r] / c[s] - 1.0) if r is not None else np.nan,
            bottom_date=idx[bottom_pos],
            dd_max=c[bottom_pos] / peak - 1.0,
            bottom_to_reentry_sessions=(r - bottom_pos) if r is not None else np.nan,
            missed_from_bottom=(c[r] / c[bottom_pos] - 1.0) if r is not None else np.nan,
            defense_date=idx[d] if d is not None else pd.NaT,
            lag_peak_to_defense=(d - peak_pos) if d is not None else np.nan,
            dd_at_defense=(c[d] / peak - 1.0) if d is not None else np.nan,
            dd_at_defense_vs252=(c[d] / trail_high[d] - 1.0) if d is not None else np.nan,
        )
        assert row["dd_at_block"] <= 1e-12 and row["dd_max"] <= row["dd_at_block"] + 1e-12
        rows.append(row)
    return pd.DataFrame(rows)


def yearly_summary(close: pd.Series, states: pd.Series,
                   episodes: pd.DataFrame) -> pd.DataFrame:
    """연도별 상태 점유율·진입허용률·지수 수익률·에피소드 수."""
    df = pd.DataFrame({"close": close, "state": states})
    df["allowed"] = df["state"].shift(1).eq(MarketState.NORMAL)  # 엔진 D-1 규약
    rows = []
    for year, g in df.groupby(df.index.year):
        ep = episodes[episodes["block_date"].dt.year == year] if len(episodes) else episodes
        rows.append(dict(
            year=year,
            sessions=len(g),
            normal_pct=g["state"].eq(MarketState.NORMAL).mean() * 100,
            caution_pct=g["state"].eq(MarketState.CAUTION).mean() * 100,
            defense_pct=g["state"].eq(MarketState.DEFENSE).mean() * 100,
            allowed_pct=g["allowed"].mean() * 100,
            index_ret_pct=(g["close"].iloc[-1] / g["close"].iloc[0] - 1) * 100,
            n_blocks=len(ep),
            n_defense=int((ep["kind"] == "defense").sum()) if len(ep) else 0,
        ))
    out = pd.DataFrame(rows)
    shares = out[["normal_pct", "caution_pct", "defense_pct"]].sum(axis=1)
    assert np.allclose(shares, 100.0), "상태 점유율 합 != 100%"
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_indexes(specs: list[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for spec in specs:
        name, _, path = spec.partition("=")
        assert name and path, f"--index 형식은 name=path: {spec!r}"
        out[name] = Path(path)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--index", action="append", required=True,
                    metavar="NAME=PATH", help="지수 CSV(date,close). 반복 지정")
    ap.add_argument("--start", default="2000-01-01", help="분석 창 시작(차단 시작일 기준)")
    ap.add_argument("--end", default=None, help="분석 창 끝(기본: 데이터 끝)")
    ap.add_argument("--out-dir", default="out/stress2001")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(args.start)

    for name, path in _parse_indexes(args.index).items():
        close = load_close(path)
        end = pd.Timestamp(args.end) if args.end else close.index[-1]
        states = compute_states(close)
        episodes = extract_episodes(close, states)
        win = episodes[(episodes["block_date"] >= start)
                       & (episodes["block_date"] <= end)].reset_index(drop=True)
        mask = (close.index >= start) & (close.index <= end)
        yearly = yearly_summary(close[mask], states[mask], win)

        win.to_csv(out_dir / f"{name}_episodes.csv", index=False,
                   encoding="utf-8-sig", float_format="%.6f")
        yearly.to_csv(out_dir / f"{name}_yearly.csv", index=False,
                      encoding="utf-8-sig", float_format="%.4f")

        defense = win[win["kind"] == "defense"]
        print(f"\n=== {name} {start.date()}..{end.date()} — "
              f"차단 {len(win)}회 (방어 도달 {len(defense)}회) ===")
        cols = ["block_date", "peak_date", "dd_at_block", "lag_peak_to_defense",
                "dd_at_defense", "dd_max", "blocked_sessions", "whipsaw_cost",
                "bottom_to_reentry_sessions", "missed_from_bottom"]
        with pd.option_context("display.width", 200, "display.max_columns", 30):
            print(defense.sort_values("dd_max")[cols].head(12).to_string(index=False))
            print(yearly.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
