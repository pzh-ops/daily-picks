"""pytest 全局 fixture（测试文档 §2）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import respx
from freezegun import freeze_time

from daily_picks.config import RootConfig, load_config, write_default_config
from daily_picks.storage import Storage


@pytest.fixture
def tmp_db(tmp_path: Path) -> Storage:
    """临时目录下的空库（test.db），已 init_schema。"""
    storage = Storage(tmp_path / "test.db")
    storage.init_schema()
    return storage


@pytest.fixture
def sample_config(tmp_path: Path) -> RootConfig:
    """全默认 RootConfig（write_default_config 生成后 load，storage.db_path 指向临时目录）。"""
    path = tmp_path / "config.yaml"
    write_default_config(path)
    cfg = load_config(str(path))
    cfg.storage.db_path = str(tmp_path / "data" / "test.db")
    return cfg


@pytest.fixture
def mock_http():
    """respx mock 上下文（assert_all_mocked=True），供各用例注册路由。"""
    with respx.mock(assert_all_mocked=True) as m:
        yield m


@pytest.fixture
def frozen_now():
    """冻结本地时间为 2026-08-27 08:00（UTC+8），并返回该时刻 naive datetime。"""
    with freeze_time("2026-08-27 08:00:00", tz_offset=8):
        yield datetime(2026, 8, 27, 8, 0, 0)
