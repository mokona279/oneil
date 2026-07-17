"""P5 워크포워드 연도 슬라이스 분석기 (plan/p5_walkforward.md §4).

전체창 정밀 런 출력 디렉토리(암, arm)들을 읽어 연도별 지표(수익률·MDD·트레이드·발동)
·축별 delta·앵커드 선택 안정성을 산출한다. 엔진이 인과적이므로(t시점 상태 = t에서
멈춘 런과 동일) 전체창 1회 정밀 런의 연도 슬라이스 = 그 해까지 운용한 계좌의 그 해
성과와 동치라는 전제(plan §2 P5-1)로 계산한다.

**의존성 제약**: oneil_bt 패키지를 import하지 않는다(pandas+stdlib만). config/src/out
아래 파일은 읽기만 하고 절대 쓰지 않는다.

n_trades·n_wins 판정 근거 (중요 — 반드시 읽을 것):
    trades.csv의 `trade_id`는 표시용 그룹핑일 뿐이다(`reporting/trade_log.py`
    `_trade_ids`: 동일 심볼+진입일이면 같은 id를 등장 순서대로 부여 — 부분청산/트란셰가
    여러 행으로 이어진다). 반면 백테스트 엔진이 실제로 세는 "트레이드" 단위는
    `reporting/metrics.py::compute_metrics`가 `n = len(result.trades)`로 계산하는
    TradeRecord 개수 = trades.csv **행 수** 그대로이고, 승패도 행 단위 pnl>0으로
    센다(트란셰별 개별 판정 — trade_id로 합산하지 않는다).
    실측(out/q13/candidate_B): trade_id 고유값은 152개인데 metrics.json의 n_trades는
    367(= trades.csv 행 수)이다. 즉 trade_id 단위로 묶어 "그 트레이드의 마지막
    exit_date 연도"에 귀속시키는 방식은 총합이 152가 되어 metrics.json과 **절대
    일치할 수 없다**. 따라서 본 스크립트는 트레이드 연도 귀속·승패 판정을 모두
    **행(TradeRecord) 단위**로 하며(= exit_date 그 행의 연도, pnl>0이면 승), 이것이
    metrics.json의 n_trades·n_wins와 정확히 일치하는 유일한 방법이다. r_sum(연도별
    pnl_r 합)도 애초에 "행 단위 그대로 합산"이 명시 규칙이라(§P5-3, 기존 포렌식 관례)
    동일 기준으로 일관된다. 참고로 행 단위에서는 pnl>0과 pnl_r>0이 항상 같은 부호를
    갖는다(리스크 금액이 항상 양수이므로) — 실측으로도 219승으로 동일해 판정 기준
    선택(pnl 합 vs pnl_r 합)은 무의미해진다.

사용:
    python scripts/walkforward_report.py \
        --arm full=out/p5/full --arm noR3b=out/p5/noR3b --arm legacy=out/p5/legacy \
        [--initial-cash 1e8] [--out-dir out/p5]
    --arm 은 반복 지정(NAME=PATH). 존재하지 않거나 필수 파일이 없는 경로는 경고 후
    스킵한다. 산출: <out-dir>/wf_yearly.csv, wf_axis_delta.csv, wf_anchored.csv + 콘솔 표.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REQUIRED_FILES = ("equity_curve.csv", "trades.csv", "metrics.json")

# 축 정의 (plan §2 P5-4, P5-2 §3 표): {축 이름: 대안(되돌림) 암 이름}.
# delta = full − 대안암 ("채택값 − 대안값"). full 암은 상수 이름 "full"로 고정.
FULL_ARM = "full"
AXES: dict[str, str] = {
    "R3b": "noR3b",
    "Q11": "noQ11",
    "R4b": "noR4b",
    "confirm5": "c3",
    "W1": "w3",
    "slots12": "slots8",
    "reserve_off": "reserve",
    "package": "legacy",
}

NEUTRAL_BAND_PP = 2.0  # 앵커드 선택 판정 중립 밴드(±2pp, plan §2 P5-5(a)).
DECISION_YEARS = range(2021, 2027)  # 앵커드 결정 시점 2021~2026.


@dataclass
class ArmData:
    name: str
    path: Path
    equity: pd.DataFrame   # date, equity, ... (+year)
    trades: pd.DataFrame   # ... exit_date, pnl, pnl_r, ... (+year)
    acts: pd.DataFrame     # date, symbol, rule, detail (+year) — 없으면 빈 DF
    metrics: dict


# --------------------------------------------------------------------------
# 로딩
# --------------------------------------------------------------------------

def parse_arm_arg(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise SystemExit(f"--arm 형식 오류(NAME=PATH 필요): {raw!r}")
    name, _, path_str = raw.partition("=")
    name, path_str = name.strip(), path_str.strip()
    if not name or not path_str:
        raise SystemExit(f"--arm 형식 오류(NAME=PATH 필요): {raw!r}")
    return name, path_str


def _read_acts(path: Path) -> pd.DataFrame:
    """rule_activations.csv — 없거나 헤더뿐이어도 관대하게 빈 DF로 처리."""
    empty = pd.DataFrame(columns=["date", "symbol", "rule", "detail", "year"])
    if not path.exists():
        return empty
    try:
        acts = pd.read_csv(path, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return empty
    if len(acts) == 0 or "date" not in acts.columns:
        return empty
    acts = acts.copy()
    acts["date"] = pd.to_datetime(acts["date"])
    if acts["date"].isna().any():
        raise ValueError(f"{path}: date 파싱 실패 행 존재")
    acts["year"] = acts["date"].dt.year
    return acts


def load_arm(name: str, path_str: str) -> ArmData | None:
    """암 디렉토리 로드. 경로/필수 파일 부재 시 경고 후 None(스킵)."""
    path = Path(path_str)
    if not path.exists():
        print(f"[경고] 암 '{name}' 스킵 — 경로 없음: {path}", file=sys.stderr)
        return None
    missing = [f for f in REQUIRED_FILES if not (path / f).exists()]
    if missing:
        print(f"[경고] 암 '{name}' 스킵 — 필수 파일 누락 {missing}: {path}", file=sys.stderr)
        return None

    equity = pd.read_csv(path / "equity_curve.csv", encoding="utf-8-sig")
    equity["date"] = pd.to_datetime(equity["date"])
    if equity["date"].isna().any():
        raise ValueError(f"[{name}] equity_curve.csv: date 파싱 실패 행 존재")
    equity["year"] = equity["date"].dt.year

    trades = pd.read_csv(path / "trades.csv", encoding="utf-8-sig")
    if len(trades):
        trades["exit_date"] = pd.to_datetime(trades["exit_date"])
        if trades["exit_date"].isna().any():
            raise ValueError(f"[{name}] trades.csv: exit_date 파싱 실패 행 존재")
        trades["year"] = trades["exit_date"].dt.year
    else:
        trades["year"] = pd.Series(dtype="int64")

    acts = _read_acts(path / "rule_activations.csv")

    with (path / "metrics.json").open(encoding="utf-8-sig") as f:
        metrics = json.load(f)

    return ArmData(name=name, path=path, equity=equity, trades=trades, acts=acts, metrics=metrics)


# --------------------------------------------------------------------------
# 연도별 집계
# --------------------------------------------------------------------------

def yearly_returns(equity: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    """연도별 (last_equity, ret_pct, mdd_pct).

    ret_pct base: 전년 마지막 equity, 최초 연도는 initial_cash.
    mdd_pct: 연내 러닝 피크 대비 최대 낙폭(%, 피크는 연초 리셋), 0 이상.
    """
    eq_sorted = equity.sort_values("date")
    years = sorted(eq_sorted["year"].unique())
    rows = []
    base = initial_cash
    for y in years:
        yr_eq = eq_sorted.loc[eq_sorted["year"] == y, "equity"].to_numpy()
        last_eq = float(yr_eq[-1])
        ret_pct = (last_eq / base - 1.0) * 100.0
        peak = float("-inf")
        mdd = 0.0
        for eq in yr_eq:
            peak = max(peak, float(eq))
            if peak > 0:
                mdd = max(mdd, (peak - float(eq)) / peak)
        rows.append({"year": int(y), "last_equity": last_eq, "ret_pct": ret_pct, "mdd_pct": mdd * 100.0})
        base = last_eq
    return pd.DataFrame(rows, columns=["year", "last_equity", "ret_pct", "mdd_pct"])


def yearly_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """행(TradeRecord) 단위 exit_date 연도 귀속. 승패는 pnl>0(모듈 docstring 근거)."""
    if len(trades) == 0:
        return pd.DataFrame(columns=["year", "n_trades", "n_wins", "r_sum"])
    g = trades.groupby("year")
    out = g.agg(
        n_trades=("pnl", "size"),
        n_wins=("pnl", lambda s: int((s > 0).sum())),
        r_sum=("pnl_r", "sum"),
    ).reset_index()
    out["year"] = out["year"].astype(int)
    return out


def yearly_activations(acts: pd.DataFrame) -> pd.DataFrame:
    """rule별 연도 카운트 → act_<rule> 와이드 컬럼(rule 이름은 파일에서 동적으로)."""
    if len(acts) == 0:
        return pd.DataFrame(columns=["year"])
    pivot = acts.pivot_table(index="year", columns="rule", values="date", aggfunc="count", fill_value=0)
    pivot.columns = [f"act_{c}" for c in pivot.columns]
    pivot = pivot.reset_index()
    pivot["year"] = pivot["year"].astype(int)
    return pivot


def build_wf_yearly(arms: dict[str, ArmData], initial_cash: float) -> pd.DataFrame:
    """암별 연도 지표를 하나의 표로 합친다. act_* 컬럼은 전 암 합집합, 결측은 0."""
    frames = []
    for name, arm in arms.items():
        ret_df = yearly_returns(arm.equity, initial_cash)
        trade_df = yearly_trades(arm.trades)
        act_df = yearly_activations(arm.acts)

        merged = ret_df.merge(trade_df, on="year", how="left")
        merged = merged.merge(act_df, on="year", how="left")

        for col in ("n_trades", "n_wins"):
            if col not in merged.columns:
                merged[col] = 0
        merged["n_trades"] = merged["n_trades"].fillna(0).astype(int)
        merged["n_wins"] = merged["n_wins"].fillna(0).astype(int)
        merged["r_sum"] = merged["r_sum"].fillna(0.0) if "r_sum" in merged.columns else 0.0

        act_cols = [c for c in merged.columns if c.startswith("act_")]
        if act_cols:
            merged[act_cols] = merged[act_cols].fillna(0).astype(int)

        merged.insert(0, "arm", name)
        frames.append(merged.drop(columns=["last_equity"]))

    out = pd.concat(frames, ignore_index=True, sort=False)
    act_cols = sorted(c for c in out.columns if c.startswith("act_"))
    if act_cols:
        out[act_cols] = out[act_cols].fillna(0).astype(int)
    base_cols = ["arm", "year", "ret_pct", "mdd_pct", "r_sum", "n_trades", "n_wins"]
    return out[base_cols + act_cols]


# --------------------------------------------------------------------------
# 불변식 (plan §4-3)
# --------------------------------------------------------------------------

def assert_invariants(name: str, arm: ArmData, wf_yearly: pd.DataFrame) -> None:
    m = arm.metrics
    sub = wf_yearly[wf_yearly["arm"] == name].sort_values("year")

    if "total_return_pct" in m and len(sub):
        cum = 1.0
        for r in sub["ret_pct"]:
            cum *= (1.0 + r / 100.0)
        final_cum = (cum - 1.0) * 100.0
        expected = float(m["total_return_pct"])
        diff = abs(final_cum - expected)
        assert diff <= 0.05, (
            f"[불변식 위반: {name}] 연도별 복리 누적수익 계산={final_cum:.6f}% != "
            f"metrics.json total_return_pct={expected:.6f}% (diff={diff:.6f}pp > 0.05pp 허용치)"
        )

    if "n_trades" in m:
        total_trades = int(sub["n_trades"].sum())
        assert total_trades == int(m["n_trades"]), (
            f"[불변식 위반: {name}] 연도별 n_trades 합={total_trades} != "
            f"metrics.json n_trades={m['n_trades']}"
        )
    if "n_wins" in m:
        total_wins = int(sub["n_wins"].sum())
        assert total_wins == int(m["n_wins"]), (
            f"[불변식 위반: {name}] 연도별 n_wins 합={total_wins} != "
            f"metrics.json n_wins={m['n_wins']}"
        )

    total_r = float(sub["r_sum"].sum())
    raw_r = float(arm.trades["pnl_r"].sum()) if len(arm.trades) else 0.0
    assert abs(total_r - raw_r) <= 1e-6, (
        f"[불변식 위반: {name}] 연도별 r_sum 합={total_r} != trades.csv pnl_r 총합={raw_r}"
    )

    act_cols = [c for c in sub.columns if c.startswith("act_")]
    total_act = int(sub[act_cols].to_numpy().sum()) if act_cols else 0
    assert total_act == len(arm.acts), (
        f"[불변식 위반: {name}] 연도별 발동 합={total_act} != "
        f"rule_activations.csv 행수={len(arm.acts)}"
    )


# --------------------------------------------------------------------------
# 축별 delta · 앵커드 선택 안정성 (plan §2 P5-4)
# --------------------------------------------------------------------------

def _cum_ret_series(wf_yearly: pd.DataFrame, arm_name: str) -> pd.Series:
    """cum_ret(arm, ≤Y) = (Π(1+ret_y/100)−1)×100, 연도 인덱스 Series."""
    sub = wf_yearly[wf_yearly["arm"] == arm_name].sort_values("year")
    cum = 1.0
    idx, vals = [], []
    for _, row in sub.iterrows():
        cum *= (1.0 + row["ret_pct"] / 100.0)
        idx.append(int(row["year"]))
        vals.append((cum - 1.0) * 100.0)
    return pd.Series(vals, index=idx, dtype=float)


def build_axis_delta(wf_yearly: pd.DataFrame) -> pd.DataFrame:
    """두 암이 모두 있을 때만 계산(full 고정 vs 축별 대안암)."""
    cols = ["axis", "year", "ret_delta_pp", "r_delta", "cum_full_pct", "cum_arm_pct", "cum_delta_pp"]
    arms_present = set(wf_yearly["arm"].unique())
    if FULL_ARM not in arms_present:
        return pd.DataFrame(columns=cols)

    full_df = wf_yearly[wf_yearly["arm"] == FULL_ARM].set_index("year")
    cum_full = _cum_ret_series(wf_yearly, FULL_ARM)

    rows = []
    for axis, alt in AXES.items():
        if alt not in arms_present:
            continue
        alt_df = wf_yearly[wf_yearly["arm"] == alt].set_index("year")
        cum_alt = _cum_ret_series(wf_yearly, alt)
        years = sorted(set(full_df.index) & set(alt_df.index))
        for y in years:
            ret_delta = float(full_df.loc[y, "ret_pct"] - alt_df.loc[y, "ret_pct"])
            r_delta = float(full_df.loc[y, "r_sum"] - alt_df.loc[y, "r_sum"])
            cf, ca = float(cum_full[y]), float(cum_alt[y])
            rows.append({
                "axis": axis, "year": y,
                "ret_delta_pp": ret_delta, "r_delta": r_delta,
                "cum_full_pct": cf, "cum_arm_pct": ca, "cum_delta_pp": cf - ca,
            })
    return pd.DataFrame(rows, columns=cols)


def band(delta_pp: float) -> str:
    """±NEUTRAL_BAND_PP 중립 밴드로 3분류: adopted(채택값 우세)/legacy(대안 우세)/tie."""
    if delta_pp > NEUTRAL_BAND_PP:
        return "adopted"
    if delta_pp < -NEUTRAL_BAND_PP:
        return "legacy"
    return "tie"


def build_anchored(axis_delta: pd.DataFrame) -> pd.DataFrame:
    """결정 시점 Y: train=누적(≤Y-1) 부호로 picked, test=Y연도 delta 부호와 일치(rewarded)."""
    cols = ["axis", "decision_year", "train_cum_delta_pp", "picked", "test_year_delta_pp", "rewarded"]
    if len(axis_delta) == 0:
        return pd.DataFrame(columns=cols)

    rows = []
    for axis, g in axis_delta.groupby("axis"):
        g = g.set_index("year")
        for Y in DECISION_YEARS:
            if (Y - 1) not in g.index or Y not in g.index:
                continue
            train = float(g.loc[Y - 1, "cum_delta_pp"])
            test = float(g.loc[Y, "ret_delta_pp"])
            picked = band(train)
            test_band = band(test)
            if picked == "tie" or test_band == "tie":
                rewarded = "neutral"
            else:
                rewarded = "yes" if picked == test_band else "no"
            rows.append({
                "axis": axis, "decision_year": Y,
                "train_cum_delta_pp": train, "picked": picked,
                "test_year_delta_pp": test, "rewarded": rewarded,
            })
    return pd.DataFrame(rows, columns=cols)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="P5 워크포워드 연도 슬라이스 분석기")
    ap.add_argument("--arm", action="append", default=[], metavar="NAME=PATH",
                     help="정밀 런 출력 디렉토리(반복 지정 가능)")
    ap.add_argument("--initial-cash", type=float, default=1.0e8, help="2017년 base 초기자본")
    ap.add_argument("--out-dir", default="out/p5", help="CSV 산출 디렉토리")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.arm:
        print("[오류] --arm 을 최소 1개 지정해야 합니다.", file=sys.stderr)
        return 1

    arms: dict[str, ArmData] = {}
    for raw in args.arm:
        name, path_str = parse_arm_arg(raw)
        arm = load_arm(name, path_str)
        if arm is not None:
            arms[name] = arm

    if not arms:
        print("[오류] 유효한 암이 하나도 없습니다.", file=sys.stderr)
        return 1

    wf_yearly = build_wf_yearly(arms, args.initial_cash)
    for name, arm in arms.items():
        assert_invariants(name, arm, wf_yearly)

    axis_delta = build_axis_delta(wf_yearly)
    anchored = build_anchored(axis_delta)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wf_yearly.to_csv(out_dir / "wf_yearly.csv", index=False, encoding="utf-8-sig")
    axis_delta.to_csv(out_dir / "wf_axis_delta.csv", index=False, encoding="utf-8-sig")
    anchored.to_csv(out_dir / "wf_anchored.csv", index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", None)
    pd.set_option("display.float_format", lambda v: f"{v:,.4f}")

    print(f"\n로드된 암 {len(arms)}개: {', '.join(arms)}")
    print(f"불변식 4종(누적수익·n_trades·n_wins·r_sum·발동합) 전 암 통과.")

    print("\n=== wf_yearly ===")
    print(wf_yearly.to_string(index=False))

    print("\n=== wf_axis_delta ===")
    if len(axis_delta):
        print(axis_delta.to_string(index=False))
    else:
        print("(계산 가능한 축 없음 — full 암 + 대안 암 쌍이 모두 로드돼야 함)")

    print("\n=== wf_anchored ===")
    if len(anchored):
        print(anchored.to_string(index=False))
    else:
        print("(계산 가능한 축 없음)")

    print(f"\n산출: {out_dir}/wf_yearly.csv, wf_axis_delta.csv, wf_anchored.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
