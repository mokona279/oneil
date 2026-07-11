"""네트워크 스모크 (계획서 §7). 실 pykrx로 005930 소량 수집 → 로더 통과.

기본 실행에서 자동 skip: pykrx 미설치 또는 KRX 자격증명 부재면 네트워크를 건드리기 전에
즉시 skip한다(패치본 pykrx는 KRX 데이터 접근에 KRX_ID/KRX_PW 로그인이 필요).

실제로 돌리려면 자격증명 .env 경로를 KRX_ENV_FILE로 주면 된다:
    KRX_ENV_FILE=C:/path/to/.env pytest tests/unit/fetch/test_network_smoke.py -m network
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("pykrx", reason="pykrx 미설치 — 네트워크 스모크 skip")

from oneil_bt.data.loader import CsvBarLoader  # noqa: E402
from oneil_fetch.env_loader import load_env_file  # noqa: E402
from oneil_fetch.krx_client import PykrxClient  # noqa: E402
from oneil_fetch.transform import clean_bars, normalize_ohlcv  # noqa: E402
from oneil_fetch.writer import write_prices  # noqa: E402


def _ensure_credentials() -> None:
    env_file = os.getenv("KRX_ENV_FILE")
    if env_file and Path(env_file).exists():
        load_env_file(env_file)
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        pytest.skip("KRX 자격증명 없음(KRX_ID/KRX_PW 또는 KRX_ENV_FILE) — 스모크 skip")


@pytest.mark.network
def test_fetch_005930_10days_passes_loader(tmp_path: Path) -> None:
    _ensure_credentials()
    client = PykrxClient(sleep_sec=0.3)
    try:
        raw = client.ohlcv("20240102", "20240116", "005930")
    except Exception as exc:  # KRX 응답 실패는 네트워크 이슈 → skip
        pytest.skip(f"KRX 응답 실패: {exc}")
    df, _ = clean_bars(normalize_ohlcv(raw))
    assert len(df) > 0
    # 실측 거래대금(value)이 병합돼 있어야 한다 (§9 Q4)
    assert "value" in df.columns
    assert (df["value"] > 0).all()
    path = tmp_path / "005930.csv"
    write_prices(df, path)
    pf = CsvBarLoader().load(path)
    assert "value" in pf.df.columns
    assert (pf.df["high"] >= pf.df["low"]).all()
