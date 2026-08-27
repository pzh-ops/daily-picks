"""命令行入口：argparse 子命令编排（开发文档 §4.18）。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from daily_picks import __version__
from daily_picks.config import DEFAULT_CONFIG_PATH, ConfigError, RootConfig, load_config, write_default_config
from daily_picks.llm import LLMClient, estimate_cost
from daily_picks.log import setup_logging
from daily_picks.models import Article, ScoredArticle
from daily_picks.ranker import rank_and_pick, rule_score, select_candidates
from daily_picks.sources import SourceAdapter, build_adapters
from daily_picks.storage import Storage

logger = logging.getLogger("daily_picks.cli")

_ENV_EXAMPLE = "DEEPSEEK_API_KEY=\nWECOM_WEBHOOK_KEY=\nSERVERCHAN_SENDKEY=\n"

# 单源采集超时（秒）。模块级常量便于测试注入（T-SRC-ALL-02 monkeypatch 缩短），见开发文档 §4.18 步骤 4
SOURCE_TIMEOUT_S = 30.0


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
    print(f"命令 `{command}` 尚未实现（后续里程碑完成）。")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """init 子命令：生成 config.yaml、.env.example（缺失时）并初始化数据库。"""
    cfg_path = Path(DEFAULT_CONFIG_PATH)
    if cfg_path.exists() and not args.force:
        try:
            answer = input(f"{cfg_path} 已存在，是否覆盖？[y/N] ").strip().lower()
        except EOFError:
            # 无 stdin 场景（如管道/重定向）：视为取消，优雅退出而非"未预期的错误"
            print("未检测到交互输入（stdin 已关闭），视为取消；如需覆盖请加 --force。")
            return 0
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


def cmd_run(args: argparse.Namespace) -> int:
    """run 子命令：立即执行一次完整流程（M2：采集→去重入库→规则打分→LLM 精排/降级→打印精选）。"""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    return asyncio.run(run_once(cfg, dry_run=args.dry_run))


def _parse_row_datetime(value: str | None) -> datetime | None:
    """SQLite DATETIME 文本 → naive datetime；None/非法 → None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _llm_key_missing(cfg: RootConfig) -> bool:
    """DEEPSEEK_API_KEY 是否缺失（缺失 → 走规则分降级，run 不崩溃）。"""
    try:
        _key = cfg.llm.api_key
        return _key == ""
    except ConfigError:
        return True


async def _fetch_one(adapter: SourceAdapter, cfg: RootConfig,
                     client: httpx.AsyncClient) -> tuple[str, list[Article] | None, str | None]:
    """单源采集：asyncio.wait_for 超时 + 全异常隔离；返回 (源名, 文章|None, 错误描述|None)。"""
    section = getattr(cfg.sources, adapter.name)
    try:
        items = await asyncio.wait_for(adapter.fetch(section, client), timeout=SOURCE_TIMEOUT_S)
        return adapter.name, items, None
    except TimeoutError:
        msg = f"超时（>{SOURCE_TIMEOUT_S:g}s）"
        logger.warning("采集失败 source=%s: %s", adapter.name, msg)
        return adapter.name, None, msg
    except Exception as e:  # noqa: BLE001 —— 单源失败隔离（设计文档 §6.7 / R-001）
        msg = f"{type(e).__name__}: {e}"
        logger.warning("采集失败 source=%s: %s", adapter.name, msg)
        return adapter.name, None, msg


async def _collect(adapters: list[SourceAdapter],
                   cfg: RootConfig) -> tuple[list[Article], dict[str, tuple[bool, str]]]:
    """并发采集全部启用源；返回 (全部文章, {源名: (是否成功, 描述)})。"""
    try:
        # trust_env 默认开启：httpx 自动读取 HTTP_PROXY/HTTPS_PROXY（设计文档 §6.5，HN 走代理场景）
        client = httpx.AsyncClient(timeout=SOURCE_TIMEOUT_S)
    except ImportError as e:
        # 环境代理不可用（如 socks5 代理但未装 socksio）：降级为直连，不中断整次运行
        logger.warning("httpx 初始化失败（环境代理配置不可用），改用直连: %s", e)
        client = httpx.AsyncClient(timeout=SOURCE_TIMEOUT_S, trust_env=False)
    async with client:
        results = await asyncio.gather(*(_fetch_one(a, cfg, client) for a in adapters))
    collected: list[Article] = []
    stats: dict[str, tuple[bool, str]] = {}
    errors = {a.name: a.source_errors for a in adapters}
    for name, items, err in results:
        if items:
            collected.extend(items)  # 部分成功的条目仍入库（失败隔离）
        if err is not None:
            stats[name] = (False, err)
        elif errors.get(name, 0):
            # 适配器内部吞掉的失败（§6.7 计数 source_errors）：如实标记
            if items:
                stats[name] = (False, f"获取 {len(items)} 条，{errors[name]} 个请求失败")
            else:
                stats[name] = (False, f"{errors[name]} 个请求全部失败")
        else:
            stats[name] = (True, f"{len(items)} 条")
    return collected, stats


async def run_once(cfg: RootConfig, dry_run: bool = False) -> int:
    """完整流程（设计文档 §4.2 数据流）。返回 0=成功/推送，1=部分失败，2=致命错误。

    M1 已实现步骤 1-5；M2 实现步骤 6-7（规则打分→LLM 精排/降级→打印精选）与排序记账；
    步骤 8-9（digest 生成与推送）由 M3 填充。
    """
    # 步骤 1：日志 + Storage 初始化（建表/迁移）
    setup_logging(level=cfg.logging.level, log_file=cfg.logging.file,
                  max_bytes=cfg.logging.max_bytes, backup_count=cfg.logging.backup_count)
    db_path = Path(cfg.storage.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)  # data/ 运行时创建（目录结构 §2）
    storage = Storage(db_path)
    storage.init_schema()

    # 步骤 2：当日日期（配置时区，幂等键）
    run_date = datetime.now().astimezone(ZoneInfo(cfg.app.timezone)).strftime("%Y-%m-%d")

    # 步骤 3：当日 digest_runs 幂等锁（同日重复触发返回已有 run id，设计文档 §5）
    run_id = storage.start_digest_run(run_date, candidate_count=0)
    logger.info("开始采集 run_id=%s run_date=%s", run_id, run_date)

    # 步骤 4：并发采集（asyncio.gather + 每源 asyncio.wait_for 30s 超时，单源失败隔离）
    adapters = build_adapters(cfg)
    collected, stats = await _collect(adapters, cfg)

    # 步骤 5：去重入库（返回新入库 id；新文章构 ScoredArticle 打分在 M2 步骤 4/7）
    new_ids = storage.upsert_articles(collected)
    print(f"采集 {len(collected)} 条，去重后 {len(new_ids)} 条")
    print("各源采集情况:")
    for name, (ok, detail) in stats.items():
        print(f"  - {name}: {'成功' if ok else '失败'}（{detail}）")
    logger.info("采集 %d 条，去重后新入库 %d 条 run_id=%s", len(collected), len(new_ids), run_id)
    failed = [name for name, (ok, _) in stats.items() if not ok]

    # 步骤 6：weights = storage.get_interest_weights() 合并 config 关键词
    # （语义：config.interests.keywords 优先，同名词以 config 权重为准；表中独有的词追加）
    weights = storage.get_interest_weights()
    for kw in cfg.interests.keywords:
        weights[kw.keyword] = kw.weight

    # 步骤 7：规则打分 → select_candidates → rank_and_pick（LLM 失败/无 key 降级为规则分）
    now = datetime.now()
    scored: list[ScoredArticle] = []
    for row in storage.get_articles_by_ids(new_ids):
        article = Article(
            source=row["source"], source_key=row["source_key"], title=row["title"], url=row["url"],
            author=row["author"], summary=row["summary"],
            published_at=_parse_row_datetime(row["published_at"]),
        )
        feedback = storage.get_feedback_kinds(row["id"])
        score = rule_score(article, weights, now,
                           source_weight=getattr(cfg.sources, article.source).weight,
                           feedback_kinds=feedback)
        storage.update_score(row["id"], score)
        scored.append(ScoredArticle(article=article, score=score, article_id=row["id"]))

    candidates = select_candidates(scored, cfg.digest.max_candidates)
    llm_client = LLMClient(cfg.llm)
    if _llm_key_missing(cfg):
        print("未配置 DEEPSEEK_API_KEY，使用规则分降级")
        logger.warning("未配置 DEEPSEEK_API_KEY，使用规则分降级")
    picks, fallback_used = await rank_and_pick(
        candidates, llm_client, weights, cfg.digest.top_n, cfg.llm.max_input_chars
    )

    by_id = {sa.article_id: sa for sa in scored}
    print(f"精选 {len(picks)} 条" + (" [fallback]" if fallback_used else ""))
    for pick in picks:
        picked = by_id.get(pick.article_id)
        if picked is None:
            logger.warning("精选条目无对应文章 article_id=%s，跳过", pick.article_id)
            continue
        print(f"{pick.rank}. 【{picked.article.source}】{picked.article.title} —— {pick.reason}")

    # 步骤 10（排序部分，M2）：finish_digest_run 记账（token/cost/fallback；推送字段由 M3 填充）
    tokens_in = llm_client.last_tokens_in
    tokens_out = llm_client.last_tokens_out
    cost_usd = estimate_cost(tokens_in, tokens_out)
    storage.finish_digest_run(run_id, picked_count=len(picks), pushed=0, channel=None,
                              tokens_in=tokens_in, tokens_out=tokens_out,
                              cost_usd=cost_usd, fallback_used=fallback_used)
    logger.info("采集 %d 条，去重后新入库 %d 条，候选 %d，精选 %d，成本 $%.6f run_id=%s",
                len(collected), len(new_ids), len(candidates), len(picks), cost_usd, run_id)
    # 步骤 8-9：digest 生成与推送（M3 填充）

    # 全部源失败视为部分失败（对齐 T-E2E-06）；单源失败不影响整体（R-001）
    return 1 if (stats and failed and len(failed) == len(stats)) else 0


def main(argv: list[str] | None = None) -> int:
    """argparse 入口；异常统一处理，返回退出码（0 成功 / 1 部分失败 / 2 致命错误）。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "init": cmd_init,
        "run": cmd_run,
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
