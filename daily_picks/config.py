"""配置模型与加载（pydantic + YAML，设计文档 §11 / 开发文档 §4.1）。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config.yaml"

# 已知适配器名（设计文档 §6）；enabled 中的未知名字只警告不报错（T-CFG-05）
KNOWN_SOURCES = ("rss", "bilibili", "zhihu", "juejin", "hnews", "infoq")


class ConfigError(Exception):
    """配置加载/校验错误（CLI 捕获后打印友好错误并退出码 2）。"""


class AppConfig(BaseModel):
    name: str = "daily-picks"
    timezone: str = "Asia/Shanghai"


class ScheduleConfig(BaseModel):
    time: str = "08:00"  # 每日推送时间 "HH:MM"


class DigestConfig(BaseModel):
    top_n: int = 10
    max_candidates: int = 40
    min_score: float = 0.0


class LLMConfig(BaseSettings):
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    api_key_env: str = "DEEPSEEK_API_KEY"  # 从环境读取，不落盘
    temperature: float = 0.3
    max_tokens: int = 2000
    timeout_s: int = 60
    max_input_chars: int = 12000

    @property
    def api_key(self) -> str:
        """从环境变量读取密钥；缺失抛 ConfigError。"""
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise ConfigError(f"缺少环境变量 {self.api_key_env}，请在 .env 或 shell 环境中配置")
        return key


class PushConfig(BaseSettings):
    provider: str = "wecom"  # wecom | serverchan | none
    wecom_webhook_key_env: str = "WECOM_WEBHOOK_KEY"
    serverchan_sendkey_env: str = "SERVERCHAN_SENDKEY"
    dry_run_file: str = "logs/last_digest.md"


class Keyword(BaseModel):
    keyword: str
    weight: float = 1.0


class SourceSection(BaseModel):
    weight: float = 0.0
    max_items_per_source: int = 30
    urls: list[str] = []  # 仅 rss/infoq 使用
    ps: int = 20  # 仅 bilibili
    limit: int = 50  # 仅 zhihu/juejin
    hits_per_page: int = 30  # 仅 hnews


class InterestsConfig(BaseModel):
    keywords: list[Keyword] = []


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: list[str] = []  # 启用顺序（名字与适配器一致）
    rss: SourceSection = SourceSection()
    bilibili: SourceSection = SourceSection()
    zhihu: SourceSection = SourceSection()
    juejin: SourceSection = SourceSection()
    hnews: SourceSection = SourceSection()
    infoq: SourceSection = SourceSection()


class StorageConfig(BaseModel):
    db_path: str = "data/daily_picks.db"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "logs/daily_picks.log"
    max_bytes: int = 1048576
    backup_count: int = 3


class RootConfig(BaseModel):
    app: AppConfig = AppConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    digest: DigestConfig = DigestConfig()
    llm: LLMConfig = LLMConfig()
    interests: InterestsConfig = InterestsConfig()
    sources: SourcesConfig = SourcesConfig()
    push: PushConfig = PushConfig()
    storage: StorageConfig = StorageConfig()
    logging: LoggingConfig = LoggingConfig()


# 设计文档 §11 的默认配置 YAML（write_default_config 原样写出）
DEFAULT_YAML = """\
app:
  name: daily-picks
  timezone: Asia/Shanghai

schedule:
  time: "08:00"              # 每日推送时间（24h）

digest:
  top_n: 10                  # 精选条数
  max_candidates: 40         # 送 LLM 精排的候选上限
  min_score: 0.0             # 规则分低于此值不参与（保底策略除外）

llm:
  base_url: https://api.deepseek.com
  model: deepseek-v4-pro
  api_key_env: DEEPSEEK_API_KEY
  temperature: 0.3
  max_tokens: 2000
  timeout_s: 60
  max_input_chars: 12000

interests:
  keywords:
    - {keyword: "AI", weight: 2.0}
    - {keyword: "大模型", weight: 2.0}
    - {keyword: "独立开发", weight: 1.5}
    - {keyword: "开源", weight: 1.0}

sources:
  enabled: [rss, bilibili, zhihu, juejin, hnews, infoq]
  rss:
    urls:
      - https://sspai.com/feed
      - https://www.ruanyifeng.com/blog/atom.xml
    max_items_per_source: 30
    weight: 0.0
  bilibili:
    ps: 20
    max_items_per_source: 20
    weight: 0.0
  zhihu:
    limit: 50
    max_items_per_source: 30
    weight: 0.0
  juejin:
    limit: 20
    max_items_per_source: 20
    weight: 0.0
  hnews:
    hits_per_page: 30
    max_items_per_source: 30
    weight: 0.0
  infoq:
    urls:
      - https://www.infoq.cn/feed
    max_items_per_source: 20
    weight: 0.0

push:
  provider: wecom            # wecom | serverchan | none
  wecom:
    webhook_key_env: WECOM_WEBHOOK_KEY
  serverchan:
    sendkey_env: SERVERCHAN_SENDKEY
  dry_run_file: logs/last_digest.md

storage:
  db_path: data/daily_picks.db

logging:
  level: INFO
  file: logs/daily_picks.log
  max_bytes: 1048576         # 1MB 轮转
  backup_count: 3
"""


def _flatten_push(data: dict) -> None:
    """把 §11 嵌套写法（push.wecom.webhook_key_env）拍平为 PushConfig 扁平字段。"""
    push = data.get("push")
    if not isinstance(push, dict):
        return
    wecom = push.get("wecom")
    if isinstance(wecom, dict):
        push.setdefault("wecom_webhook_key_env", wecom.get("webhook_key_env", "WECOM_WEBHOOK_KEY"))
        push.pop("wecom")
    serverchan = push.get("serverchan")
    if isinstance(serverchan, dict):
        push.setdefault("serverchan_sendkey_env", serverchan.get("sendkey_env", "SERVERCHAN_SENDKEY"))
        push.pop("serverchan")


def _load_dotenv(path: str = ".env") -> None:
    """极简 .env 加载（KEY=VALUE 每行）；已存在的环境变量优先，不覆盖。"""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _validate(cfg: RootConfig) -> None:
    """开发文档 §4.1 全部校验：非法配置抛 ConfigError；未知源 key 只警告。"""
    if cfg.digest.top_n > cfg.digest.max_candidates:
        raise ConfigError(
            f"digest.top_n({cfg.digest.top_n}) 不能大于 digest.max_candidates({cfg.digest.max_candidates})"
        )
    if cfg.push.provider not in {"wecom", "serverchan", "none"}:
        raise ConfigError(f"push.provider 非法: {cfg.push.provider!r}（可选 wecom | serverchan | none）")
    for kw in cfg.interests.keywords:
        if not 0 < kw.weight <= 10:
            raise ConfigError(f"关键词 {kw.keyword!r} 权重 {kw.weight} 越界（要求 0 < weight <= 10）")
    for name in cfg.sources.enabled:
        if name not in KNOWN_SOURCES:
            logger.warning("未知内容源 %r，build_adapters 将跳过", name)
    extra = getattr(cfg.sources, "__pydantic_extra__", None) or {}
    for name in extra:
        logger.warning("未知内容源配置段 %r，已忽略", name)


def load_config(path: str = DEFAULT_CONFIG_PATH) -> RootConfig:
    """加载 YAML 配置；缺省字段取默认值；校验失败抛 ConfigError。"""
    _load_dotenv()
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"配置文件不存在: {p}（请先运行 `daily-picks init`）")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件解析失败: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是映射（dict），实际为 {type(data).__name__}")
    _flatten_push(data)
    try:
        cfg = RootConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"配置校验失败:\n{e}") from e
    _validate(cfg)
    return cfg


def write_default_config(path: str) -> None:
    """写设计文档 §11 中的默认 YAML（幂等覆盖，自动创建父目录）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(DEFAULT_YAML, encoding="utf-8")
