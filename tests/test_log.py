"""T-LOG 日志模块用例（测试文档 §4.11）。"""

from __future__ import annotations

import logging
import re
from logging import StreamHandler
from logging.handlers import RotatingFileHandler

from daily_picks.log import setup_logging


class TestLogging:
    def test_default_console_only(self):  # T-LOG-01
        setup_logging()
        logger = logging.getLogger("daily_picks")
        handler_types = {type(h) for h in logger.handlers}
        assert StreamHandler in handler_types
        assert RotatingFileHandler not in handler_types

    def test_file_handler_writes(self, tmp_path):  # T-LOG-02
        log_file = tmp_path / "app.log"
        setup_logging(log_file=str(log_file))
        logging.getLogger("daily_picks").info("hello")
        for handler in logging.getLogger("daily_picks").handlers:
            handler.flush()
        assert log_file.exists()
        assert "hello" in log_file.read_text(encoding="utf-8")

    def test_rotation_backup_created(self, tmp_path):  # T-LOG-03
        log_file = tmp_path / "rot.log"
        setup_logging(log_file=str(log_file), max_bytes=100, backup_count=3)
        logger = logging.getLogger("daily_picks")
        for i in range(200):
            logger.info("轮转测试第 %d 行：撑大日志文件触发 RotatingFileHandler 轮转备份", i)
        for handler in logger.handlers:
            handler.flush()
        assert (tmp_path / "rot.log.1").exists()

    def test_format_matches_spec(self, tmp_path):  # T-LOG-04
        log_file = tmp_path / "fmt.log"
        setup_logging(log_file=str(log_file))
        logging.getLogger("daily_picks").info("格式测试")
        for handler in logging.getLogger("daily_picks").handlers:
            handler.flush()
        first = log_file.read_text(encoding="utf-8").splitlines()[0]
        pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO \[daily_picks\] 格式测试"
        assert re.match(pattern, first)

    def test_repeated_setup_no_handler_growth(self, tmp_path):  # T-LOG-05
        setup_logging(log_file=str(tmp_path / "a.log"))
        setup_logging(log_file=str(tmp_path / "b.log"))
        assert len(logging.getLogger("daily_picks").handlers) == 2  # 1 console + 1 file，不叠加
