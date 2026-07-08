"""통합 테스트 공용 픽스처 (Phase 8).

`data_example/`의 소형 실데이터를 CsvDataSource로 로드해, 스모크·골든 회귀 테스트가
동일 소스·설정을 공유하도록 한다. 데이터셋 자체는 `data_example/generate.py`가
결정론적으로 생성한다(재현: `python data_example/generate.py`).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from oneil_bt.data.csv_source import CsvDataSource
from oneil_bt.data.metadata import MetaRepository
from oneil_bt.domain.config import Config
from oneil_bt.domain.enums import Market

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data_example"
RULES = REPO_ROOT / "config" / "rules_v3-3.yaml"
COSTS = REPO_ROOT / "config" / "costs.yaml"

# data_example/generate.py 의 세션 범위와 일치.
START = date(2019, 1, 2)
END = date(2020, 3, 24)


@pytest.fixture(scope="session")
def cfg() -> Config:
    return Config.load(RULES, COSTS)


@pytest.fixture(scope="session")
def source() -> CsvDataSource:
    return CsvDataSource(
        price_dir=DATA / "prices",
        index_paths={Market.KOSPI: DATA / "kospi.csv", Market.KOSDAQ: DATA / "kosdaq.csv"},
        meta=MetaRepository.from_csv(DATA / "meta.csv"),
    )
