"""단일종목 백테스트 CLI (계획서 §3 cli, Phase 6).

포트폴리오 모드와 같은 엔진을 유니버스 1종목으로 돌린다(날짜별 판정 로그 + 트레이드
산출). 조립·요약은 run_portfolio를 재사용하고, 종목 하나만 지정한다.

사용:
    python -m oneil_bt.cli.run_single --symbol 005930 ... (그 외 인자는 run_portfolio와 동일)
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from ..data.csv_source import CsvDataSource
from ..domain.config import Config
from ..domain.enums import Market
from ..engine.context import BacktestResult
from ..reporting import write_report
from .run_portfolio import build_source, format_summary, run


def run_single(
    source: CsvDataSource,
    cfg: Config,
    symbol: str,
    start: date,
    end: date,
    *,
    initial_cash: float = 1.0e8,
) -> BacktestResult:
    return run(source, cfg, start, end, initial_cash=initial_cash, symbols=[symbol])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="주도주 추세추종 단일종목 백테스트")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--price-dir", required=True)
    parser.add_argument("--meta", required=True)
    parser.add_argument("--kospi", required=True)
    parser.add_argument("--kosdaq", required=False)
    parser.add_argument("--rules", required=True)
    parser.add_argument("--costs", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cash", type=float, default=1.0e8)
    parser.add_argument("--out", default=None, help="리포트 출력 디렉토리(생략 시 요약만)")
    args = parser.parse_args(argv)

    index_paths: dict[Market, Path | str] = {Market.KOSPI: args.kospi}
    if args.kosdaq:
        index_paths[Market.KOSDAQ] = args.kosdaq

    source = build_source(args.price_dir, args.meta, index_paths)
    cfg = Config.load(args.rules, args.costs)
    result = run_single(
        source, cfg, args.symbol,
        date.fromisoformat(args.start), date.fromisoformat(args.end),
        initial_cash=args.cash,
    )
    print(format_summary(result))
    if args.out:
        report = write_report(result, args.out)
        print("\n[성과 지표]")
        print(report.summary())
        print(f"\n리포트 저장: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
