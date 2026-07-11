"""파라미터 민감도 스윕 CLI (계획서 §11 후속과제, analysis 하니스).

base config를 두고 규칙 수치 축(점 경로)별 값 목록의 데카르트 곱으로 백테스트를 반복
실행해, 조합별 성과지표를 랭킹 표로 출력하고 CSV로 저장한다.

축 지정 방법(둘 다 허용, 합쳐진다):
- `--param <점경로>=<v1,v2,...>`  (반복 가능)  예: `--param sizing.max_weight_pct=5,10,20`
- `--grid <grid.yaml>`  — `{점경로: [값,...]}` 매핑 파일.

사용:
    python -m oneil_bt.cli.run_sweep \
        --price-dir data/prices --kospi data/kospi.csv --kosdaq data/kosdaq.csv \
        --meta data/meta.csv --rules config/rules_v3-3.yaml --costs config/costs.yaml \
        --start 2015-01-01 --end 2020-12-31 --cash 1e8 \
        --param sizing.max_weight_pct=5,10,20 --param stop.atr_mult=1.5,2,2.5 \
        --sort total_return_pct --out out/sweep.csv
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ..analysis import (
    ParameterGrid,
    format_sweep,
    run_sweep,
    write_sweep_csv,
)
from ..domain.config import Config
from ..domain.enums import Market
from .run_portfolio import build_source


def _parse_scalar(token: str) -> Any:
    """CLI 토큰을 int→float→bool→str 순으로 가장 좁은 타입으로 파싱한다."""
    t = token.strip()
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return t


def parse_param_specs(specs: list[str] | None) -> dict[str, list[Any]]:
    """`["path=v1,v2", ...]` → `{path: [값,...]}`. 값은 스칼라로 파싱."""
    axes: dict[str, list[Any]] = {}
    for spec in specs or []:
        if "=" not in spec:
            raise SystemExit(f"--param 형식 오류(‘path=v1,v2’ 필요): {spec!r}")
        path, _, values = spec.partition("=")
        path = path.strip()
        if not path:
            raise SystemExit(f"--param 경로가 비었다: {spec!r}")
        axes[path] = [_parse_scalar(v) for v in values.split(",") if v.strip() != ""]
    return axes


def load_grid_file(path: Path | str) -> dict[str, list[Any]]:
    """`{점경로: [값,...]}` YAML을 읽는다."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"--grid 파일 루트는 매핑이어야 한다: {path}")
    axes: dict[str, list[Any]] = {}
    for key, values in data.items():
        if not isinstance(values, (list, tuple)):
            raise SystemExit(f"--grid 축 '{key}' 값은 리스트여야 한다")
        axes[str(key)] = list(values)
    return axes


def build_grid(
    grid_file: str | None, param_specs: list[str] | None
) -> ParameterGrid:
    """--grid(먼저) + --param(뒤, 같은 축이면 덮어씀)을 합쳐 ParameterGrid로."""
    axes: dict[str, list[Any]] = {}
    if grid_file:
        axes.update(load_grid_file(grid_file))
    axes.update(parse_param_specs(param_specs))
    if not axes:
        raise SystemExit("스윕할 축이 없다. --param 또는 --grid 로 지정하라.")
    return ParameterGrid.from_mapping(axes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="주도주 추세추종 파라미터 민감도 스윕")
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
    parser.add_argument(
        "--param", action="append", default=None,
        help="스윕 축 ‘점경로=v1,v2,...’ (반복 가능)",
    )
    parser.add_argument("--grid", default=None, help="{점경로: [값,...]} YAML 파일")
    parser.add_argument(
        "--sort", default="total_return_pct", help="랭킹 정렬 지표(기본 총수익률)",
    )
    parser.add_argument(
        "--asc", action="store_true", help="오름차순 정렬(기본은 내림차순)",
    )
    parser.add_argument("--out", default=None, help="스윕 결과 CSV 경로")
    args = parser.parse_args(argv)

    grid = build_grid(args.grid, args.param)

    index_paths: dict[Market, Path | str] = {Market.KOSPI: args.kospi}
    if args.kosdaq:
        index_paths[Market.KOSDAQ] = args.kosdaq
    source = build_source(args.price_dir, args.meta, index_paths)
    cfg = Config.load(args.rules, args.costs)
    symbols = args.symbols.split(",") if args.symbols else None

    n_combos = len(grid)
    print(f"스윕 축 {len(grid.names)}개 → 조합 {n_combos}개 실행 중...\n")
    result = run_sweep(
        source, cfg, grid,
        date.fromisoformat(args.start), date.fromisoformat(args.end),
        initial_cash=args.cash, symbols=symbols,
    )

    print(format_sweep(result, sort_key=args.sort, reverse=not args.asc))
    if args.out:
        write_sweep_csv(result, args.out)
        print(f"\n스윕 CSV 저장: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
