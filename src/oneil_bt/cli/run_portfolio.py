"""포트폴리오 백테스트 CLI (계획서 §3 cli, Phase 6).

CSV 데이터 소스와 config를 읽어 전체(또는 지정) 유니버스로 엔진을 돌리고, 성과 요약을
출력한다. 상세 리포트(트레이드/자본곡선 CSV·지표)는 Phase 7 리포팅이 담당한다.

사용:
    python -m oneil_bt.cli.run_portfolio \
        --price-dir data/prices --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
        --meta data/meta.csv --rules config/rules_v3-3.yaml --costs config/costs.yaml \
        --start 2015-01-01 --end 2020-12-31 --cash 1e8 [--symbols A,B,C]
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from ..data.csv_source import CsvDataSource
from ..data.metadata import MetaRepository
from ..domain.config import Config
from ..domain.enums import Market
from ..engine.context import BacktestResult
from ..engine.engine import BacktestEngine
from ..reporting import write_report


def build_source(
    price_dir: Path | str,
    meta_path: Path | str,
    index_paths: dict[Market, Path | str],
) -> CsvDataSource:
    return CsvDataSource(
        price_dir=price_dir,
        index_paths=index_paths,
        meta=MetaRepository.from_csv(meta_path),
    )


def run(
    source: CsvDataSource,
    cfg: Config,
    start: date,
    end: date,
    *,
    initial_cash: float = 1.0e8,
    symbols: list[str] | None = None,
) -> BacktestResult:
    engine = BacktestEngine(source, cfg, initial_cash=initial_cash)
    return engine.run(start, end, symbols=symbols)


def format_summary(result: BacktestResult) -> str:
    n_stop = sum(1 for t in result.trades if t.closed.is_stop)
    lines = [
        f"기간           : {result.start} ~ {result.end}",
        f"초기자본       : {result.initial_cash:,.0f}",
        f"최종자본       : {result.final_equity:,.0f}",
        f"총수익률       : {result.total_return_pct:+.2f}%",
        f"트레이드 수    : {len(result.trades)} (손절 {n_stop})",
        f"이벤트 수      : {len(result.events)}",
        f"자본곡선 일수  : {len(result.equity_curve)}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="주도주 추세추종 포트폴리오 백테스트")
    parser.add_argument("--price-dir", required=True)
    parser.add_argument("--meta", required=True)
    parser.add_argument("--kospi", required=True)
    parser.add_argument("--kosdaq", required=False)
    parser.add_argument("--rules", required=True)
    parser.add_argument("--costs", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cash", type=float, default=1.0e8)
    parser.add_argument("--symbols", default=None, help="쉼표 구분 종목코드(생략 시 전체)")
    parser.add_argument("--out", default=None, help="리포트 출력 디렉토리(생략 시 요약만)")
    args = parser.parse_args(argv)

    index_paths: dict[Market, Path | str] = {Market.KOSPI: args.kospi}
    if args.kosdaq:
        index_paths[Market.KOSDAQ] = args.kosdaq

    source = build_source(args.price_dir, args.meta, index_paths)
    cfg = Config.load(args.rules, args.costs)
    symbols = args.symbols.split(",") if args.symbols else None
    result = run(
        source, cfg,
        date.fromisoformat(args.start), date.fromisoformat(args.end),
        initial_cash=args.cash, symbols=symbols,
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
