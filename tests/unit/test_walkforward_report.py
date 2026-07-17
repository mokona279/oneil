"""P5 워크포워드 분석기(scripts/walkforward_report.py) 단위 테스트.

합성 미니 데이터(DataFrame 직접 구성 또는 tmp_path에 CSV 기록)로 ret/mdd 산식,
트레이드 연도 귀속(행 단위 — trade_id 합산 아님), 앵커드 picked/rewarded 밴드 판정,
불변식 assert를 각각 검증한다. 실제 백테스트 산출물(out/q13/candidate_B)에 대한
end-to-end 스모크는 CLI로 별도 수행(레포 규칙상 out/ 아래는 테스트에서 쓰지 않는다).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import walkforward_report as wf  # noqa: E402


def _equity_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """(date, equity) 목록 → yearly_returns가 요구하는 최소 스키마 DataFrame."""
    df = pd.DataFrame(rows, columns=["date", "equity"])
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    return df


def _trades_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["year"] = df["exit_date"].dt.year
    return df


def _acts_df(rows: list[tuple[str, str]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "rule"])
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    return df


# --------------------------------------------------------------------------
# 1. ret_pct / mdd_pct — base 전환·연초 피크 리셋
# --------------------------------------------------------------------------

def test_yearly_returns_base_and_mdd_reset_per_year() -> None:
    equity = _equity_df([
        ("2017-01-02", 100.0),
        ("2017-06-01", 150.0),   # 연중 고점
        ("2017-12-28", 120.0),   # 연말 낙폭 -> 2017 mdd = (150-120)/150 = 20%
        ("2018-01-02", 200.0),   # 2018년은 전년 마지막(120)보다 높게 시작 -> 피크 리셋
        ("2018-06-01", 180.0),   # 2018 mdd = (200-180)/200 = 10% (2017 고점 150 무시)
        ("2018-12-28", 240.0),
    ])
    out = wf.yearly_returns(equity, initial_cash=100.0)
    y17 = out[out["year"] == 2017].iloc[0]
    y18 = out[out["year"] == 2018].iloc[0]

    # 2017 ret: base=initial_cash(100) -> 120/100-1 = 20%
    assert y17["ret_pct"] == pytest.approx(20.0)
    assert y17["mdd_pct"] == pytest.approx(20.0)

    # 2018 ret: base=전년 마지막(120) -> 240/120-1 = 100%
    assert y18["ret_pct"] == pytest.approx(100.0)
    # 피크가 연초에 리셋됐으므로 2017 고점(150)이 아니라 2018 고점(200) 기준
    assert y18["mdd_pct"] == pytest.approx(10.0)


# --------------------------------------------------------------------------
# 2. 트레이드 연도 귀속 — 행(TradeRecord) 단위, trade_id 합산 아님
# --------------------------------------------------------------------------

def test_yearly_trades_row_level_not_trade_id_collapsed() -> None:
    # 동일 trade_id(=1)의 부분청산 2행이 서로 다른 연도(2020/2021)에 걸쳐 있다.
    # metrics.json의 n_trades는 항상 trades.csv 행 수와 같으므로(모듈 docstring 근거),
    # 각 행을 자신의 exit_date 연도에 각각 귀속해야 한다 — trade_id의 "마지막
    # exit_date" 하나로 묶어 전부 한 해로 합치면 안 된다.
    trades = _trades_df([
        {"trade_id": 1, "exit_date": "2020-12-20", "pnl": 100.0, "pnl_r": 1.0},
        {"trade_id": 1, "exit_date": "2021-01-05", "pnl": -50.0, "pnl_r": -0.5},
        {"trade_id": 2, "exit_date": "2021-03-01", "pnl": 30.0, "pnl_r": 0.3},
    ])
    out = wf.yearly_trades(trades).set_index("year")

    assert out.loc[2020, "n_trades"] == 1          # trade_id=1의 첫 행만 2020 귀속
    assert out.loc[2020, "n_wins"] == 1
    assert out.loc[2020, "r_sum"] == pytest.approx(1.0)

    assert out.loc[2021, "n_trades"] == 2           # trade_id=1의 둘째 행 + trade_id=2
    assert out.loc[2021, "n_wins"] == 1              # -50짜리는 패, 30짜리는 승
    assert out.loc[2021, "r_sum"] == pytest.approx(-0.5 + 0.3)

    # 전체 합은 항상 원본 행 수·pnl_r 합과 일치(불변식의 근거이기도 함).
    assert out["n_trades"].sum() == len(trades)
    assert out["r_sum"].sum() == pytest.approx(trades["pnl_r"].sum())


# --------------------------------------------------------------------------
# 3. rule_activations 동적 컬럼화(act_<rule>) + 0 채움
# --------------------------------------------------------------------------

def test_yearly_activations_dynamic_columns() -> None:
    acts = _acts_df([
        ("2020-05-01", "q11_stop_clamp"),
        ("2020-06-01", "q11_stop_clamp"),
        ("2021-01-10", "r4b_reentry_entry"),
    ])
    out = wf.yearly_activations(acts).set_index("year")
    assert "act_q11_stop_clamp" in out.columns
    assert "act_r4b_reentry_entry" in out.columns
    assert out.loc[2020, "act_q11_stop_clamp"] == 2
    assert out.loc[2020, "act_r4b_reentry_entry"] == 0   # 그 해엔 발동 없음 -> 0
    assert out.loc[2021, "act_q11_stop_clamp"] == 0
    assert out.loc[2021, "act_r4b_reentry_entry"] == 1


def test_yearly_activations_empty_returns_year_only_frame() -> None:
    empty = pd.DataFrame(columns=["date", "symbol", "rule", "detail", "year"])
    out = wf.yearly_activations(empty)
    assert list(out.columns) == ["year"]
    assert len(out) == 0


# --------------------------------------------------------------------------
# 4. 앵커드 picked/rewarded 밴드 판정 (±2pp 중립 밴드)
# --------------------------------------------------------------------------

def test_build_anchored_picked_and_rewarded_bands() -> None:
    # axis_delta를 직접 구성 — cum_delta_pp(train 재료)와 ret_delta_pp(test 재료)를
    # 밴드 경계 안팎으로 배치해 picked/rewarded 조합을 전부 실측한다.
    axis_delta = pd.DataFrame([
        # axis A: 2020(train) adopted(>2pp), 2021(test) adopted 방향 -> rewarded yes
        {"axis": "A", "year": 2020, "ret_delta_pp": 0.0, "r_delta": 0.0,
         "cum_full_pct": 5.0, "cum_arm_pct": 0.0, "cum_delta_pp": 5.0},
        {"axis": "A", "year": 2021, "ret_delta_pp": 3.0, "r_delta": 0.0,
         "cum_full_pct": 8.0, "cum_arm_pct": 0.0, "cum_delta_pp": 8.0},
        # axis B: 2020(train) adopted(>2pp), 2021(test) legacy 방향(< -2pp) -> rewarded no
        {"axis": "B", "year": 2020, "ret_delta_pp": 0.0, "r_delta": 0.0,
         "cum_full_pct": 5.0, "cum_arm_pct": 0.0, "cum_delta_pp": 5.0},
        {"axis": "B", "year": 2021, "ret_delta_pp": -4.0, "r_delta": 0.0,
         "cum_full_pct": 0.0, "cum_arm_pct": 4.0, "cum_delta_pp": -4.0},
        # axis C: 2020(train) tie(중립 밴드 안, 정확히 경계값 2.0도 tie) -> rewarded neutral
        {"axis": "C", "year": 2020, "ret_delta_pp": 0.0, "r_delta": 0.0,
         "cum_full_pct": 2.0, "cum_arm_pct": 0.0, "cum_delta_pp": 2.0},
        {"axis": "C", "year": 2021, "ret_delta_pp": 10.0, "r_delta": 0.0,
         "cum_full_pct": 10.0, "cum_arm_pct": 0.0, "cum_delta_pp": 10.0},
    ])
    out = wf.build_anchored(axis_delta).set_index(["axis", "decision_year"])

    a = out.loc[("A", 2021)]
    assert a["picked"] == "adopted"
    assert a["rewarded"] == "yes"

    b = out.loc[("B", 2021)]
    assert b["picked"] == "adopted"
    assert b["rewarded"] == "no"

    c = out.loc[("C", 2021)]
    assert c["picked"] == "tie"       # train=2.0은 경계값(> 아님) -> tie
    assert c["rewarded"] == "neutral"


def test_band_boundaries() -> None:
    assert wf.band(2.0) == "tie"          # 경계값은 tie(strict > 필요)
    assert wf.band(2.0001) == "adopted"
    assert wf.band(-2.0) == "tie"
    assert wf.band(-2.0001) == "legacy"
    assert wf.band(0.0) == "tie"


# --------------------------------------------------------------------------
# 5. 불변식 assert — 통과/실패 각각
# --------------------------------------------------------------------------

def _make_arm(name: str, metrics: dict) -> wf.ArmData:
    equity = _equity_df([("2020-01-02", 100.0), ("2020-12-30", 110.0)])
    trades = _trades_df([
        {"trade_id": 1, "exit_date": "2020-03-01", "pnl": 10.0, "pnl_r": 1.0},
        {"trade_id": 2, "exit_date": "2020-06-01", "pnl": -5.0, "pnl_r": -0.5},
    ])
    acts = _acts_df([("2020-02-01", "q11_stop_clamp")])
    return wf.ArmData(name=name, path=Path("."), equity=equity, trades=trades,
                       acts=acts, metrics=metrics)


def test_assert_invariants_passes_when_consistent() -> None:
    # 2020 ret: 110/100-1=10% -> 누적수익도 10%와 일치해야 한다.
    metrics = {"total_return_pct": 10.0, "n_trades": 2, "n_wins": 1}
    arm = _make_arm("ok", metrics)
    wf_yearly = wf.build_wf_yearly({"ok": arm}, initial_cash=100.0)
    wf.assert_invariants("ok", arm, wf_yearly)  # 예외 없이 통과해야 함


def test_assert_invariants_raises_on_n_trades_mismatch() -> None:
    metrics = {"total_return_pct": 10.0, "n_trades": 999, "n_wins": 1}
    arm = _make_arm("bad", metrics)
    wf_yearly = wf.build_wf_yearly({"bad": arm}, initial_cash=100.0)
    with pytest.raises(AssertionError, match="n_trades"):
        wf.assert_invariants("bad", arm, wf_yearly)


# --------------------------------------------------------------------------
# 6. load_arm — 경로 부재 시 경고 후 스킵(None)
# --------------------------------------------------------------------------

def test_load_arm_missing_path_returns_none(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "does_not_exist"
    result = wf.load_arm("ghost", str(missing))
    assert result is None
    captured = capsys.readouterr()
    assert "경고" in captured.err


# --------------------------------------------------------------------------
# 7. load_arm 통합 — 실 CSV 파일(utf-8-sig, BOM) 기록 → 읽기 → 불변식 통과
# --------------------------------------------------------------------------

def test_load_arm_reads_real_csv_files_end_to_end(tmp_path: Path) -> None:
    arm_dir = tmp_path / "arm"
    arm_dir.mkdir()

    equity_csv = (
        "date,cash,holdings_value,equity,n_positions,exposure_pct,market_state\n"
        "2020-01-02,100.0,0.0,100.0,0,0.0,KOSPI=NORMAL\n"
        "2020-12-30,10.0,100.0,110.0,1,90.9,KOSPI=NORMAL\n"
    )
    trades_csv = (
        "symbol,market,trade_id,tranche_no,entry_reason,entry_date,entry_price,"
        "entry_qty,entry_cost,exit_reason,exit_date,exit_price,exit_qty,exit_cost,"
        "pnl,pnl_r,hold_days,base_stage,pivot\n"
        "000001,KOSPI,1,1,BREAKOUT_T1,2020-01-05,10.0,10,1.0,STOP,2020-03-01,9.0,10,"
        "1.0,10.0,1.0,55,1,10.0\n"
        "000002,KOSPI,2,1,BREAKOUT_T1,2020-05-01,20.0,5,1.0,STOP,2020-06-01,19.0,5,"
        "1.0,-5.0,-0.5,31,1,20.0\n"
    )
    acts_csv = "date,symbol,rule,detail\n2020-02-01,000001,q11_stop_clamp,{}\n"
    metrics_json = '{"total_return_pct": 10.0, "n_trades": 2, "n_wins": 1}'

    (arm_dir / "equity_curve.csv").write_text(equity_csv, encoding="utf-8-sig")
    (arm_dir / "trades.csv").write_text(trades_csv, encoding="utf-8-sig")
    (arm_dir / "rule_activations.csv").write_text(acts_csv, encoding="utf-8-sig")
    (arm_dir / "metrics.json").write_text(metrics_json, encoding="utf-8-sig")

    arm = wf.load_arm("full", str(arm_dir))
    assert arm is not None
    wf_yearly = wf.build_wf_yearly({"full": arm}, initial_cash=100.0)
    wf.assert_invariants("full", arm, wf_yearly)  # 통과해야 함

    row = wf_yearly[wf_yearly["year"] == 2020].iloc[0]
    assert row["n_trades"] == 2
    assert row["n_wins"] == 1
    assert row["act_q11_stop_clamp"] == 1
