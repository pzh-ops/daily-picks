"""T-SCHED 调度模块用例（测试文档 §4.9；调度不真正常驻——直接触发 job 函数，禁止真等 1 天）。"""

from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from test_e2e import WECOM_URL, load, mock_sources

from daily_picks import scheduler as sched
from daily_picks.config import ConfigError
from daily_picks.storage import Storage


class TestParseScheduleTime:
    def test_parse_valid(self):  # T-SCHED-01（解析部分）
        assert sched.parse_schedule_time("08:00") == (8, 0)
        assert sched.parse_schedule_time("23:59") == (23, 59)

    @pytest.mark.parametrize("bad", ["25:99", "24:00", "08:60", "-1:00", "8", "abc", "08:00:00", "", None])
    def test_parse_invalid_raises(self, bad):  # T-SCHED-02
        with pytest.raises(ConfigError):
            sched.parse_schedule_time(bad)


class TestBuildTrigger:
    def test_trigger_hour_minute_timezone(self, sample_config):  # T-SCHED-01（trigger 构造部分）
        trigger = sched.build_trigger(sample_config)
        fields = {f.name: str(f) for f in trigger.fields}
        assert fields["hour"] == "8"
        assert fields["minute"] == "0"
        assert trigger.timezone == ZoneInfo("Asia/Shanghai")

    def test_trigger_uses_configured_time(self, sample_config):
        sample_config.schedule.time = "21:30"
        trigger = sched.build_trigger(sample_config)
        fields = {f.name: str(f) for f in trigger.fields}
        assert fields["hour"] == "21"
        assert fields["minute"] == "30"


@pytest.fixture
def sched_cfg(sample_config, tmp_path):
    """调度集成用配置：provider=none（推送不触网）、dry_run_file 指向 tmp（不污染仓库 logs/）。"""
    cfg = sample_config
    cfg.push.provider = "none"
    cfg.push.dry_run_file = str(tmp_path / "logs" / "last_digest.md")
    return cfg


class TestSchedulerJob:
    def test_job_executes_run_once_and_records(self, sched_cfg, mock_http):  # T-SCHED-03
        mock_sources(mock_http)
        scheduler = sched._make_scheduler(sched_cfg)
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "daily_run"
        jobs[0].func(*jobs[0].args)  # 到点触发：直接调用 job 入口（不等真实调度器）
        storage = Storage(Path(sched_cfg.storage.db_path))
        rows = storage._conn.execute("SELECT COUNT(*) FROM digest_runs").fetchone()[0]
        assert rows == 1  # run_once 已执行并落库

    def test_same_day_second_run_skips_push(self, sched_cfg, mock_http, monkeypatch):  # T-SCHED-04
        monkeypatch.setenv("WECOM_WEBHOOK_KEY", "test-key")
        sched_cfg.push.provider = "wecom"  # 覆盖 fixture 的 none，走真实推送路径
        mock_sources(mock_http)
        wecom = mock_http.post(WECOM_URL).mock(
            return_value=httpx.Response(200, json=json.loads(load("wecom_ok.json"))))
        job = sched._make_scheduler(sched_cfg).get_jobs()[0]
        job.func(*job.args)
        job.func(*job.args)  # 同日二次触发：幂等跳过推送
        assert wecom.call_count == 1
        storage = Storage(Path(sched_cfg.storage.db_path))
        assert storage._conn.execute("SELECT COUNT(*) FROM digest_runs").fetchone()[0] == 1
        run = storage.get_digest_run(1)
        assert run["pushed"] == 1
        assert run["channel"] == "wecom"


class TestReentrancy:
    def test_job_skips_when_previous_run_in_progress(self, sched_cfg, monkeypatch):
        """防重入：进程内锁被占用（上次运行未结束）→ 本次跳过，不执行 run_once。"""
        calls = []

        async def fake_run_once(cfg, dry_run=False):
            calls.append(cfg)
            return 0

        monkeypatch.setattr("daily_picks.cli.run_once", fake_run_once)
        assert sched._JOB_LOCK.acquire(blocking=False)
        try:
            sched._locked_run_once(sched_cfg)  # 锁被占用 → 跳过
        finally:
            sched._JOB_LOCK.release()
        assert calls == []
        sched._locked_run_once(sched_cfg)  # 锁空闲 → 正常执行
        assert len(calls) == 1

    def test_job_releases_lock_on_failure(self, sched_cfg, monkeypatch):
        """run_once 抛异常时锁必须释放（finally），后续触发不受影响。"""
        async def failing_run_once(cfg, dry_run=False):
            raise RuntimeError("boom")

        monkeypatch.setattr("daily_picks.cli.run_once", failing_run_once)
        with pytest.raises(RuntimeError):
            sched._locked_run_once(sched_cfg)
        assert sched._JOB_LOCK.acquire(blocking=False)  # 锁已释放
        sched._JOB_LOCK.release()

    def test_job_warns_on_partial_failure_exit_code(self, sched_cfg, monkeypatch, caplog):
        """run_once 返回非零（部分失败）→ 记 WARNING 日志，锁照常释放。"""
        async def failing_run_once(cfg, dry_run=False):
            return 1

        monkeypatch.setattr("daily_picks.cli.run_once", failing_run_once)
        with caplog.at_level("WARNING", logger="daily_picks.scheduler"):
            sched._locked_run_once(sched_cfg)
        assert any("退出码 1" in r.message for r in caplog.records)
        assert sched._JOB_LOCK.acquire(blocking=False)
        sched._JOB_LOCK.release()


class TestRunForever:
    def _fake_scheduler(self, monkeypatch, raise_kbi: bool = False):
        """替身调度器：不真正常驻；start() 记录调用（可模拟 Ctrl+C）。"""

        class FakeScheduler:
            def __init__(self, job_func, cfg):
                self._job = FakeJob(job_func, cfg)
                self.started = False

            def get_jobs(self):
                return [self._job]

            def start(self):
                self.started = True
                if raise_kbi:
                    raise KeyboardInterrupt

        class FakeJob:
            def __init__(self, func, cfg):
                self.func = func
                self.args = [cfg]
                self.next_run_time = "2026-08-28 08:00:00+08:00"

        fake = FakeScheduler(sched._locked_run_once, None)
        monkeypatch.setattr(sched, "_make_scheduler", lambda cfg: fake)
        return fake

    def test_prints_next_run_time_and_starts(self, sample_config, monkeypatch, capsys):
        fake = self._fake_scheduler(monkeypatch)
        sched.run_forever(sample_config)
        out = capsys.readouterr().out
        assert "调度已启动" in out
        assert "下次运行时间" in out
        assert "2026-08-28 08:00:00" in out
        assert fake.started

    def test_keyboard_interrupt_graceful_exit(self, sample_config, monkeypatch):
        fake = self._fake_scheduler(monkeypatch, raise_kbi=True)
        sched.run_forever(sample_config)  # Ctrl+C → 不抛 traceback，正常返回
        assert fake.started
