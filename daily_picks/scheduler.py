"""常驻调度：APScheduler BlockingScheduler + CronTrigger（设计文档 §12 / 开发文档 §4.17）。"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from daily_picks.config import ConfigError, RootConfig

logger = logging.getLogger("daily_picks.scheduler")

# 进程内防重入锁：job 执行期间非阻塞获取，拿不到说明上次运行超时未结束，跳过本次
_JOB_LOCK = threading.Lock()


def parse_schedule_time(t: str) -> tuple[int, int]:
    """解析 "HH:MM" → (hour, minute)；非法格式/越界抛 ConfigError。"""
    if not isinstance(t, str) or ":" not in t:
        raise ConfigError(f"schedule.time 非法: {t!r}（应为 HH:MM，如 08:00）")
    hour_s, _, minute_s = t.partition(":")
    try:
        hour = int(hour_s.strip())
        minute = int(minute_s.strip())
    except ValueError:
        raise ConfigError(f"schedule.time 非法: {t!r}（应为 HH:MM，如 08:00）") from None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ConfigError(f"schedule.time 越界: {t!r}（hour 0-23，minute 0-59）")
    return hour, minute


def build_trigger(cfg: RootConfig) -> CronTrigger:
    """按 cfg.schedule.time 构造 CronTrigger（时区取 cfg.app.timezone，默认 Asia/Shanghai）。"""
    hour, minute = parse_schedule_time(cfg.schedule.time)
    return CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(cfg.app.timezone))


def _locked_run_once(cfg: RootConfig) -> None:
    """调度 job：非阻塞获取进程内锁 → run_once(cfg)；拿不到锁则跳过本次（防重入）。"""
    if not _JOB_LOCK.acquire(blocking=False):
        logger.warning("上次运行尚未结束（进程内锁被占用），跳过本次调度触发")
        return
    try:
        from daily_picks.cli import run_once  # 延迟导入：避免 cli ↔ scheduler 循环依赖

        exit_code = asyncio.run(run_once(cfg))
        if exit_code != 0:
            logger.warning("run_once 返回退出码 %d（部分失败）", exit_code)
    finally:
        _JOB_LOCK.release()


def _make_scheduler(cfg: RootConfig) -> BlockingScheduler:
    """构建 BlockingScheduler 并注册每日 job（独立成函数，便于测试直接触发 job）。"""
    scheduler = BlockingScheduler(timezone=ZoneInfo(cfg.app.timezone))
    scheduler.add_job(_locked_run_once, build_trigger(cfg), args=[cfg],
                      id="daily_run", replace_existing=True)
    return scheduler


def run_forever(cfg: RootConfig) -> None:
    """BlockingScheduler 常驻：每天 cfg.schedule.time（cfg.app.timezone）执行 run_once(cfg)。

    启动时打印下次运行时间；Ctrl+C 优雅退出（不抛 traceback）。
    """
    scheduler = _make_scheduler(cfg)
    job = scheduler.get_jobs()[0]
    now = datetime.now(ZoneInfo(cfg.app.timezone))
    next_run = job.trigger.get_next_fire_time(None, now)  # APScheduler 3.11 Job.next_run_time 访问会抛 AttributeError
    print(f"调度已启动：每天 {cfg.schedule.time}（{cfg.app.timezone}）执行一次")
    print(f"下次运行时间: {next_run}")
    logger.info("调度启动 schedule.time=%s next_run=%s", cfg.schedule.time, next_run)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到退出信号，调度已停止")
