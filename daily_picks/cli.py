"""命令行入口：argparse 子命令编排（开发文档 §4.18）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from daily_picks import __version__
from daily_picks.config import DEFAULT_CONFIG_PATH, ConfigError, load_config, write_default_config
from daily_picks.storage import Storage

_ENV_EXAMPLE = "DEEPSEEK_API_KEY=\nWECOM_WEBHOOK_KEY=\nSERVERCHAN_SENDKEY=\n"


def build_parser() -> argparse.ArgumentParser:
    """构建 argparse：init/run/serve/feedback/stats/test 六个子命令。"""
    parser = argparse.ArgumentParser(
        prog="daily-picks",
        description="每日精选（DailyPicks）：聚合 → 理解 → 决策 → 推送的个人内容 Agent",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="命令")

    p_init = sub.add_parser("init", help="初始化 config.yaml 与数据库")
    p_init.add_argument("--force", action="store_true", help="config.yaml 已存在时直接覆盖，不询问")

    p_run = sub.add_parser("run", help="立即执行一次完整流程")
    p_run.add_argument("--dry-run", action="store_true", help="只写 logs/last_digest.md，不推送")

    sub.add_parser("serve", help="常驻调度（默认每天 08:00 执行）")

    p_feedback = sub.add_parser("feedback", help="偏好反馈：like/dislike 调整关键词权重")
    p_feedback.add_argument("kind", choices=["like", "dislike"], help="反馈类型")
    p_feedback.add_argument("article_id", type=int, help="文章 id")
    p_feedback.add_argument("--keyword", help="附加关键词（文章未命中时用于调整权重）")

    p_stats = sub.add_parser("stats", help="统计报表：推送/token/成本")
    p_stats.add_argument("--days", type=int, default=7, help="统计最近 N 天（默认 7）")

    p_test = sub.add_parser("test", help="连通性自检")
    p_test.add_argument("target", choices=["llm", "push"], help="自检目标")

    return parser


def _not_implemented(command: str) -> int:
    """M0 阶段未实现命令的统一占位：提示并返回 0。"""
    print(f"命令 `{command}` 尚未实现（M0 脚手架阶段，后续里程碑完成）。")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """init 子命令：生成 config.yaml、.env.example（缺失时）并初始化数据库。"""
    cfg_path = Path(DEFAULT_CONFIG_PATH)
    if cfg_path.exists() and not args.force:
        answer = input(f"{cfg_path} 已存在，是否覆盖？[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("已取消，未做任何修改。")
            return 0

    write_default_config(DEFAULT_CONFIG_PATH)
    print(f"已生成配置文件: {cfg_path}")

    env_example = Path(".env.example")
    if env_example.exists():
        print(f"{env_example} 已存在，跳过。")
    else:
        env_example.write_text(_ENV_EXAMPLE, encoding="utf-8")
        print(f"已生成密钥模板: {env_example}")

    cfg = load_config(DEFAULT_CONFIG_PATH)
    db_path = Path(cfg.storage.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    Storage(db_path).init_schema()
    print(
        f"已初始化数据库: {db_path}（articles / digest_runs / digest_items /"
        " feedback / interest_weights 共 5 张表）"
    )
    print("\n下一步：")
    print("  1. 编辑 .env 填入密钥（DEEPSEEK_API_KEY；推送选配 WECOM_WEBHOOK_KEY 或 SERVERCHAN_SENDKEY）")
    print("  2. uv run daily-picks run --dry-run   # 预览简报（不推送）")
    print("  3. uv run daily-picks run             # 真实推送")
    return 0


def main(argv: list[str] | None = None) -> int:
    """argparse 入口；异常统一处理，返回退出码（0 成功 / 1 部分失败 / 2 致命错误）。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "init": cmd_init,
        "run": lambda _: _not_implemented("run"),
        "serve": lambda _: _not_implemented("serve"),
        "feedback": lambda _: _not_implemented("feedback"),
        "stats": lambda _: _not_implemented("stats"),
        "test": lambda _: _not_implemented("test"),
    }
    try:
        return handlers[args.command](args)
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 —— CLI 顶层兜底，避免 traceback 刷屏
        print(f"未预期的错误: {e}", file=sys.stderr)
        return 2
