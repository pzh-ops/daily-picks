"""命令行入口：argparse 子命令编排（开发文档 §4.18）。"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from daily_picks import __version__
from daily_picks.config import DEFAULT_CONFIG_PATH, ConfigError, RootConfig, load_config, write_default_config
from daily_picks.digest import build_digest_text
from daily_picks.feedback import FeedbackError, apply_feedback
from daily_picks.llm import LLMClient, estimate_cost
from daily_picks.log import setup_logging
from daily_picks.models import Article, PushResult, ScoredArticle
from daily_picks.publisher import NoopPublisher, create_publisher
from daily_picks.ranker import rank_and_pick, rule_score, select_candidates
from daily_picks.scheduler import run_forever
from daily_picks.setup import run_setup
from daily_picks.sources import SourceAdapter, build_adapters
from daily_picks.storage import Storage, StorageError
from daily_picks.tracking import TrackingClient, TrackingError, sync_clicks

logger = logging.getLogger("daily_picks.cli")

_ENV_EXAMPLE = "DEEPSEEK_API_KEY=\nWECOM_WEBHOOK_KEY=\nSERVERCHAN_SENDKEY=\nTRACKING_API_TOKEN=\n"

# stats 成本估算汇率（任务要求：USD → CNY 按 1 USD = 7.2 CNY）
USD_TO_CNY = 7.2

# 单源采集超时（秒）。模块级常量便于测试注入（T-SRC-ALL-02 monkeypatch 缩短），见开发文档 §4.18 步骤 4
SOURCE_TIMEOUT_S = 30.0


def build_parser() -> argparse.ArgumentParser:
    """构建 argparse：init/run/serve/setup/feedback/stats/test/track 八个子命令。"""
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

    sub.add_parser("setup", help="v3 启动向导：标签/信息源/每日条数配置")

    p_stats = sub.add_parser("stats", help="统计报表：推送/token/成本")
    p_stats.add_argument("--days", type=int, default=7, help="统计最近 N 天（默认 7）")

    p_test = sub.add_parser("test", help="连通性自检")
    p_test.add_argument("target", choices=["llm", "push", "track"], help="自检目标")

    p_track = sub.add_parser("track", help="点击追踪：同步点击数据并回写偏好权重（v2）")
    p_track_sub = p_track.add_subparsers(dest="track_command", required=True, metavar="子命令")
    p_track_sub.add_parser("sync", help="立即同步一次点击数据")

    return parser


def _open_storage(cfg: RootConfig) -> Storage:
    """按配置打开 Storage 并建表（db 父目录不存在时自动创建，目录结构 §2）。"""
    db_path = Path(cfg.storage.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage = Storage(db_path)
    storage.init_schema()
    return storage


def _make_tracking_client(cfg: RootConfig) -> TrackingClient | None:
    """构造点击追踪客户端（设计文档 §15）。base_url 空 → None（功能关闭）；
    base_url 已配置但缺 API token → WARNING + None（fail-open，不阻塞主流程）。"""
    if not cfg.tracking.enabled:
        return None
    token = os.environ.get(cfg.tracking.api_key_env, "").strip()
    if not token:
        logger.warning("tracking.base_url 已配置但未设置 %s，本次跳过点击同步与短链注册",
                       cfg.tracking.api_key_env)
        return None
    return TrackingClient(cfg.tracking.base_url, token, timeout_s=cfg.tracking.timeout_s)


def cmd_serve(args: argparse.Namespace) -> int:
    """serve 子命令：常驻调度（setup_logging → run_forever 打印下次运行时间并阻塞；Ctrl+C 优雅退出）。"""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    setup_logging(level=cfg.logging.level, log_file=cfg.logging.file,
                  max_bytes=cfg.logging.max_bytes, backup_count=cfg.logging.backup_count)
    run_forever(cfg)
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """setup 子命令（docs/05 §1.3）。无 LLM key → llm=None，降级为纯内置映射推荐。"""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    storage = _open_storage(cfg)
    llm = LLMClient(cfg.llm) if not _llm_key_missing(cfg) else None
    return asyncio.run(run_setup(cfg, storage, llm))


def cmd_feedback(args: argparse.Namespace) -> int:
    """feedback 子命令：like|dislike <article_id> [--keyword]（设计文档 §10）。

    找不到文章 → 退出码 1 + 提示；成功打印更新了哪些关键词权重。
    """
    cfg = load_config(DEFAULT_CONFIG_PATH)
    storage = _open_storage(cfg)
    try:
        result = apply_feedback(storage, args.article_id, args.kind, extra_keyword=args.keyword)
    except FeedbackError as e:
        print(f"反馈失败: {e}", file=sys.stderr)
        return 1
    print(f"反馈已记录（{args.kind}）")
    if result["updated"]:
        print(f"已更新关键词权重: {', '.join(result['updated'])}")
    else:
        print("文章未命中任何关键词，权重未变化（like 可加 --keyword 指定附加关键词）")
    print(f"文章 {args.article_id} 状态: {result['article_state']}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """stats 子命令：近 N 天统计报表（运行/推送/token/成本，设计文档 R-008）。"""
    if args.days < 1:
        print(f"--days 必须 >= 1（实际为 {args.days}）", file=sys.stderr)
        return 1
    cfg = load_config(DEFAULT_CONFIG_PATH)
    storage = _open_storage(cfg)
    stats = storage.get_stats(args.days)
    cost_usd = float(stats["cost_usd"])
    print(f"近 {args.days} 天统计")
    print(f"  运行次数:   {stats['runs']}")
    print(f"  推送次数:   {stats['pushed']}")
    print(f"  Token 输入: {stats['tokens_in']}")
    print(f"  Token 输出: {stats['tokens_out']}")
    print(f"  成本(USD):  ${cost_usd:.6f}")
    print(f"  成本(CNY):  ¥{cost_usd * USD_TO_CNY:.4f}（按 1 USD = {USD_TO_CNY:g} CNY 估算）")
    return 0


def cmd_track(args: argparse.Namespace) -> int:
    """track 子命令：sync 同步点击并回写偏好权重（设计文档 §15.5）。
    未启用（base_url 空/缺 token）→ 退出码 1；同步/存储故障（TrackingError/StorageError）→ 退出码 1。"""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    setup_logging(level=cfg.logging.level, log_file=cfg.logging.file,
                  max_bytes=cfg.logging.max_bytes, backup_count=cfg.logging.backup_count)
    storage = _open_storage(cfg)
    client = _make_tracking_client(cfg)
    if client is None:
        print("点击追踪未启用：请先在 config.yaml 配置 tracking.base_url，并在环境变量设置 "
              f"{cfg.tracking.api_key_env}", file=sys.stderr)
        return 1
    try:
        result = asyncio.run(sync_clicks(storage, client, cfg.tracking.click_delta))
    except (TrackingError, StorageError) as e:
        print(f"点击同步失败: {e}", file=sys.stderr)
        return 1
    print(f"同步完成：拉取 {result['synced']} 条点击，回写权重 {result['applied']} 条")
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
        f"已初始化数据库: {db_path}（articles / digest_runs / digest_items / feedback /"
        " interest_weights / clicks / meta 共 7 张表）"
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


# test push 的固定测试消息（开发文档 §4.18）
TEST_PUSH_MESSAGE = "这是一条 DailyPicks 测试消息：如果你在微信中收到它，说明推送配置正确。"


async def _test_push(cfg: RootConfig) -> int:
    """test push：向配置渠道发送固定测试消息并报告 PushResult；失败（含无 key）退出码 1。"""
    publisher = create_publisher(cfg.push)
    result = await publisher.push("今日精选测试", TEST_PUSH_MESSAGE)
    if result.ok:
        print(f"推送自检通过（channel={result.channel}）: {result.detail}")
        return 0
    print(f"推送自检失败（channel={result.channel}）: {result.detail}", file=sys.stderr)
    return 1


async def _test_llm(cfg: RootConfig) -> int:
    """test llm：发送一条极简 chat 请求（"ping"）验证 DeepSeek 连通与 key 有效性（设计文档 R-010）。

    成功打印模型名与延迟；失败（无 key / HTTP 错误 / 超时 / 非 JSON 响应）退出码 1。
    """
    try:
        api_key = cfg.llm.api_key
    except ConfigError:
        print(f"未配置 {cfg.llm.api_key_env}，无法自检 LLM 连通性", file=sys.stderr)
        return 1
    url = f"{cfg.llm.base_url.rstrip('/')}/chat/completions"
    body = {
        "model": cfg.llm.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    try:
        client = httpx.AsyncClient(timeout=cfg.llm.timeout_s)
    except ImportError as e:
        # 环境代理不可用（如 socks5 代理但未装 socksio）：降级为直连（对齐 _collect 的处理）
        logger.warning("httpx 初始化失败（环境代理配置不可用），改用直连: %s", e)
        client = httpx.AsyncClient(timeout=cfg.llm.timeout_s, trust_env=False)
    started = time.perf_counter()
    try:
        async with client:
            resp = await client.post(url, json=body, headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        print(f"LLM 自检失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    elapsed_ms = (time.perf_counter() - started) * 1000
    model = data.get("model") if isinstance(data, dict) else None
    print(f"LLM OK：model={model or cfg.llm.model}，延迟 {elapsed_ms:.0f} ms")
    return 0


async def _test_track(cfg: RootConfig) -> int:
    """test track：GET /api/clicks?after=0 验证追踪服务连通与鉴权（设计文档 R-012）。"""
    client = _make_tracking_client(cfg)
    if client is None:
        print("点击追踪未启用：tracking.base_url 为空或未设置 API token", file=sys.stderr)
        return 1
    try:
        events, has_more = await client.fetch_clicks(0)
    except TrackingError as e:
        print(f"追踪服务自检失败: {e}", file=sys.stderr)
        return 1
    print(f"追踪服务 OK：已返回 {len(events)} 条点击事件（has_more={has_more}）")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """test 子命令：llm/push/track 连通性自检（设计文档 R-010/R-012）。"""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    if args.target == "llm":
        return asyncio.run(_test_llm(cfg))
    if args.target == "track":
        return asyncio.run(_test_track(cfg))
    return asyncio.run(_test_push(cfg))


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

    步骤 1-5 采集入库（M1）；步骤 6-7 打分与 LLM 精排（M2）；步骤 8-10 简报生成、
    推送（含 dry-run 与同日幂等跳过）与记账（M3）。
    """
    # 步骤 1：日志 + Storage 初始化（建表/迁移）
    setup_logging(level=cfg.logging.level, log_file=cfg.logging.file,
                  max_bytes=cfg.logging.max_bytes, backup_count=cfg.logging.backup_count)
    db_path = Path(cfg.storage.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)  # data/ 运行时创建（目录结构 §2）
    storage = Storage(db_path)
    storage.init_schema()

    # 点击追踪客户端（未配置 → None；设计文档 §15）
    track_client = _make_tracking_client(cfg)

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

    # 步骤 5.5：同步点击并回写偏好权重（设计文档 §15.5）。未配置/失败只记日志，不阻塞主流程。
    if track_client is not None:
        try:
            sync_result = await sync_clicks(storage, track_client, cfg.tracking.click_delta)
            print(f"点击同步：拉取 {sync_result['synced']} 条，回写权重 {sync_result['applied']} 条")
        except (TrackingError, StorageError) as e:
            logger.warning("点击同步失败（不影响主流程）: %s", e)

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

    candidates = select_candidates(scored, cfg.digest.max_candidates, cfg.digest.min_score)
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

    # 步骤 7.5：注册点击追踪短链（设计文档 §15.2）；注册失败的条目保留原始 URL（fail-open）
    url_map: dict[int, str] = {}
    if track_client is not None:
        links = [(p.article_id, by_id[p.article_id].article.url)
                 for p in picks if p.article_id in by_id]
        if links:
            try:
                url_map = await track_client.register_links(links)
            except Exception as e:  # noqa: BLE001 —— 追踪失败不得阻塞主流程
                logger.warning("短链注册失败（使用原始链接）: %s", e)

    # 步骤 8：生成微信 markdown 简报（设计文档 §8；items = (rank, article, reason)）
    digest_items: list[tuple[int, Article, str]] = []
    for pick in picks:
        picked = by_id.get(pick.article_id)
        if picked is None:
            logger.warning("精选条目无对应文章 article_id=%s，跳过", pick.article_id)
            continue
        article = picked.article
        if pick.article_id in url_map:
            article = dataclasses.replace(article, url=url_map[pick.article_id])
        digest_items.append((pick.rank, article, pick.reason))
    digest_text = build_digest_text(digest_items, run_date)

    # 步骤 9：推送。dry-run → NoopPublisher 写 dry_run_file；幂等：当日已推送则跳过 webhook（设计文档 §5）
    prev_run = storage.get_digest_run(run_id)
    already_pushed = bool(prev_run and prev_run["pushed"])
    push_result: PushResult | None = None
    pushed = 0
    channel: str | None = None
    if dry_run:
        push_result = await NoopPublisher(cfg.push.dry_run_file).push("今日精选", digest_text)
        logger.info("dry-run：简报已写入 %s", cfg.push.dry_run_file)
        channel = prev_run["channel"] if already_pushed else "dry-run"
        pushed = 1 if already_pushed else 0
    elif already_pushed:
        logger.info("当日已推送 run_id=%s channel=%s，跳过推送（幂等）", run_id, prev_run["channel"])
        channel = prev_run["channel"]
        pushed = 1
    else:
        push_result = await create_publisher(cfg.push).push("今日精选", digest_text)
        channel = push_result.channel
        if push_result.ok and channel in ("wecom", "serverchan"):
            pushed = 1
            logger.info("推送成功 channel=%s detail=%s", channel, push_result.detail)
            print(f"推送成功（{channel}）: {push_result.detail}")
        elif push_result.ok:
            # provider=none：NoopPublisher 只写本地文件，等价 dry-run（设计文档 §9.3），不计 pushed
            logger.info("推送渠道为 none，简报已写本地文件: %s", push_result.detail)
            print(f"已写本地文件: {push_result.detail}")
        else:
            logger.error("推送失败 channel=%s detail=%s", channel, push_result.detail)
            print(f"推送失败（{channel}）: {push_result.detail}", file=sys.stderr)

    # 精选条目落库（设计文档 §4.2 步骤 7）
    storage.add_digest_items(run_id, picks)

    # 步骤 10：finish_digest_run 记账（picked/pushed/channel/token/cost/fallback）
    tokens_in = llm_client.last_tokens_in
    tokens_out = llm_client.last_tokens_out
    cost_usd = estimate_cost(tokens_in, tokens_out)
    storage.finish_digest_run(run_id, picked_count=len(picks), pushed=pushed, channel=channel,
                              tokens_in=tokens_in, tokens_out=tokens_out,
                              cost_usd=cost_usd, fallback_used=fallback_used)
    logger.info("采集 %d 条，去重后新入库 %d 条，候选 %d，精选 %d，成本 $%.6f run_id=%s",
                len(collected), len(new_ids), len(candidates), len(picks), cost_usd, run_id)

    # 全部源失败视为部分失败（对齐 T-E2E-06）；推送失败亦返回 1（T-E2E-05）；单源失败不影响整体（R-001）
    exit_code = 1 if (stats and failed and len(failed) == len(stats)) else 0
    if push_result is not None and not push_result.ok:
        exit_code = 1
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """argparse 入口；异常统一处理，返回退出码（0 成功 / 1 部分失败 / 2 致命错误）。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "init": cmd_init,
        "run": cmd_run,
        "serve": cmd_serve,
        "setup": cmd_setup,
        "feedback": cmd_feedback,
        "stats": cmd_stats,
        "test": cmd_test,
        "track": cmd_track,
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
