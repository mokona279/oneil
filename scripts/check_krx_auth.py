"""KRX 자격증명으로 깨졌던 pykrx 엔드포인트가 살아나는지 점검하는 일회성 스크립트.

사용:
    PYTHONPATH=src python scripts/check_krx_auth.py --env-file <.env 경로>

.env(KRX_ID/KRX_PW)를 pykrx import 전에 로드한 뒤, 로그인이 필요했던 엔드포인트
(지수·티커목록·시가총액·미수정 OHLCV=거래대금)와 대조군(수정 OHLCV)을 각각 1회 호출해
결과를 UTF-8 JSON으로 남긴다. 자격증명 값은 절대 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oneil_fetch.env_loader import load_env_file  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", required=True)
    ap.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "krx_auth_result.json"),
    )
    args = ap.parse_args()

    keys = load_env_file(args.env_file)
    print(f"[env] 로드된 키: {', '.join(keys)}")  # 값이 아니라 키 이름만

    import os

    print(f"[env] KRX_ID 설정됨: {bool(os.getenv('KRX_ID'))}, "
          f"KRX_PW 설정됨: {bool(os.getenv('KRX_PW'))}")

    from pykrx import stock  # env 설정 후 import

    out: dict = {}

    def probe(label, fn, delay=6):
        time.sleep(delay)
        try:
            r = fn()
            if r is None or (hasattr(r, "__len__") and len(r) == 0):
                out[label] = "EMPTY"
            elif hasattr(r, "columns"):
                out[label] = {"ok": True, "cols": list(r.columns), "n": len(r)}
            else:
                out[label] = {"ok": True, "n": len(r), "sample": list(r)[:3]}
        except Exception as e:  # noqa: BLE001
            out[label] = f"ERR {type(e).__name__}: {e}"
        Path(args.out).write_text(
            json.dumps(out, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    probe("index_1001", lambda: stock.get_index_ohlcv("20240102", "20240116", "1001"))
    probe("ticker_list_kospi",
          lambda: stock.get_market_ticker_list("20240102", market="KOSPI"))
    probe("market_cap_kospi",
          lambda: stock.get_market_cap("20240102", market="KOSPI"))
    probe("ohlcv_unadj_value_005930",
          lambda: stock.get_market_ohlcv("20240102", "20240116", "005930", adjusted=False))
    probe("ohlcv_adj_control_005930",
          lambda: stock.get_market_ohlcv("20240102", "20240116", "005930", adjusted=True))

    print(f"[done] 결과 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
