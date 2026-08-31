"""M12 用例：反馈通道/意图解析/演化落库（测试文档 docs/06 §4；LLM 全部 mock）。"""

from __future__ import annotations

import argparse
import json as json_mod

import pytest

from daily_picks import cli as cli_mod
from daily_picks.config import write_default_config
from daily_picks.feedback import FEEDBACK_INTENTS, ParsedFeedback, apply_feedback, parse_feedback
from daily_picks.feedback_channels import FeedbackChannel, HermesChannel, RawFeedback
from daily_picks.llm import LLMError
from daily_picks.models import Article


class TestFeedbackChannel:
    def test_raw_feedback_defaults(self):
        fb = RawFeedback(text="多推点AI硬件")
        assert fb.text == "多推点AI硬件"
        assert fb.article_id is None
        assert fb.channel == "hermes"

    def test_abstract_class_not_instantiable(self):
        with pytest.raises(TypeError):
            FeedbackChannel()  # 含抽象方法，禁止实例化

    async def test_hermes_receive_returns_empty(self):
        channel = HermesChannel()
        assert channel.name == "hermes"
        assert await channel.receive() == []

    async def test_hermes_acknowledge_noop(self):
        await HermesChannel().acknowledge("fb-1")  # 不抛错即通过


class FakeFeedbackLLM:
    """mock LLMClient.chat：返回预置 JSON 文本，或按配置抛 LLMError。"""

    def __init__(self, reply: str | None = None, raise_error: bool = False):
        self.reply = reply or ""
        self.raise_error = raise_error
        self.calls = 0

    async def chat(self, system: str, user: str, json_mode: bool = True) -> str:
        self.calls += 1
        if self.raise_error:
            raise LLMError("boom")
        return self.reply


def _llm_json(intent: str, **overrides) -> str:
    data = {"intent": intent, "article_id": None, "tags": [], "keywords": [], "top_n": None}
    data.update(overrides)
    return json_mod.dumps(data, ensure_ascii=False)


class TestParseFeedback:
    # T-FB-01 意图 like
    async def test_intent_like(self):
        llm = FakeFeedbackLLM(_llm_json("like"))
        fb = await parse_feedback("这条不错", llm)
        assert fb.intent == "like"
        assert fb.raw == "这条不错"

    # T-FB-02 意图 expand（含标签提取）
    async def test_intent_expand_with_tags(self):
        llm = FakeFeedbackLLM(_llm_json("expand", tags=["AI硬件"], keywords=["AI硬件"]))
        fb = await parse_feedback("多推点AI硬件", llm)
        assert fb.intent == "expand"
        assert fb.tags == ["AI硬件"]

    # T-FB-03 意图 adjust
    async def test_intent_adjust(self):
        llm = FakeFeedbackLLM(_llm_json("adjust", top_n=3))
        fb = await parse_feedback("每天推3条就行", llm)
        assert fb.intent == "adjust"
        assert fb.top_n == 3

    # T-FB-04 无 LLM 启发式兜底
    async def test_heuristic_dislike_without_llm(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("不要推游戏了", llm)
        assert fb.intent == "dislike"

    async def test_heuristic_adjust(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("每天推3条就行", llm)
        assert fb.intent == "adjust"
        assert fb.top_n == 3

    async def test_heuristic_expand(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("多推点AI硬件", llm)
        assert fb.intent == "expand"

    async def test_heuristic_none(self):
        llm = FakeFeedbackLLM(raise_error=True)
        fb = await parse_feedback("今天天气不错", llm)
        assert fb.intent == "none"

    # T-FB-12 解析失败不抛
    async def test_invalid_json_falls_back_to_heuristic(self):
        llm = FakeFeedbackLLM("这不是JSON{{{")
        fb = await parse_feedback("随便说点啥", llm)
        assert fb.intent == "none"

    def test_intents_constant(self):
        assert FEEDBACK_INTENTS == ("like", "dislike", "expand", "adjust", "none")


def seed_article(storage, title: str = "AI 编程工具实战",
                 summary: str = "用 AI 写代码的十个技巧") -> int:
    ids = storage.upsert_articles([
        Article(source="rss", source_key="k1", title=title,
                url="https://example.com/post/1", summary=summary)
    ])
    return ids[0]


def make_fb(intent: str, raw: str = "多推点AI硬件", article_id: int | None = None,
            tags: list[str] | None = None, keywords: list[str] | None = None,
            top_n: int | None = None) -> ParsedFeedback:
    return ParsedFeedback(raw=raw, intent=intent, article_id=article_id,
                          tags=tags or [], keywords=keywords or [], top_n=top_n)


class TestApplyTextFeedback:
    def _profile(self, tmp_db):
        tmp_db.save_profile(["AI大模型"], ["hnews"], 5)

    def _fb_rows(self, tmp_db):
        return tmp_db._conn.execute("SELECT * FROM feedback_text").fetchall()

    # T-FB-05 落库
    def test_feedback_text_recorded(self, tmp_db):
        apply_feedback(make_fb("none", raw="今天天气不错"), tmp_db)
        rows = self._fb_rows(tmp_db)
        assert len(rows) == 1
        assert rows[0]["intent"] == "none"
        assert rows[0]["channel"] == "hermes"
        assert rows[0]["raw_text"] == "今天天气不错"

    # T-FB-06 expand 写标签
    def test_expand_writes_tag_weight(self, tmp_db):
        apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=["AI硬件"]), tmp_db)
        assert dict(tmp_db.list_tags())["AI硬件"] == 1.5

    def test_expand_existing_tag_keeps_weight(self, tmp_db):
        tmp_db.save_tag_weight("AI硬件", 2.0, "click")
        apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=[]), tmp_db)
        assert dict(tmp_db.list_tags())["AI硬件"] == 2.0  # 已有标签不动（保留演化值）

    # T-FB-07 expand 合并进 profile
    def test_expand_merges_into_profile(self, tmp_db):
        self._profile(tmp_db)
        apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=["AI硬件"]), tmp_db)
        assert tmp_db.load_profile()["tags"] == ["AI大模型", "AI硬件"]

    # T-FB-08 adjust 更新 top_n（tags/sources 不变）
    def test_adjust_updates_top_n_only(self, tmp_db):
        self._profile(tmp_db)
        apply_feedback(make_fb("adjust", top_n=3), tmp_db)
        profile = tmp_db.load_profile()
        assert profile["top_n"] == 3
        assert profile["tags"] == ["AI大模型"]
        assert profile["sources"] == ["hnews"]

    def test_adjust_without_profile_is_noop(self, tmp_db):
        apply_feedback(make_fb("adjust", top_n=3), tmp_db)
        assert tmp_db.load_profile() is None  # 无画像不凭空造

    # T-FB-09 关键词写权重
    def test_keywords_bump_interest_weights(self, tmp_db):
        tmp_db.bump_keyword_weight("AI硬件", 0)  # 预置 1.0
        apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=["AI硬件"]), tmp_db)
        assert tmp_db.get_interest_weights()["AI硬件"] == pytest.approx(1.1)

    def test_keywords_clamped_at_2(self, tmp_db):
        tmp_db.bump_keyword_weight("AI硬件", 0)
        for _ in range(20):
            apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=["AI硬件"]), tmp_db)
        assert tmp_db.get_interest_weights()["AI硬件"] == 2.0

    def test_extract_keywords_disabled(self, tmp_db):
        tmp_db.bump_keyword_weight("AI硬件", 0)
        apply_feedback(make_fb("expand", tags=["AI硬件"], keywords=["AI硬件"]),
                       tmp_db, extract_keywords=False)
        assert tmp_db.get_interest_weights()["AI硬件"] == 1.0  # 不写关键词

    # like/dislike 文字路径（含 article_id）复用 v1 + tag 联动
    def test_text_like_reuses_v1_and_bumps_tags(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        tmp_db.save_tag_weight("AI", 1.0, "manual")
        aid = seed_article(tmp_db, title="AI 编程工具实战")
        apply_feedback(make_fb("like", raw="这篇不错", article_id=aid), tmp_db)
        assert tmp_db.get_feedback_kinds(aid) == ["like"]
        assert tmp_db.get_interest_weights()["AI"] == pytest.approx(1.1)   # v1 like +0.1
        assert dict(tmp_db.list_tags())["AI"] == pytest.approx(1.1)        # tag +0.1

    def test_text_dislike_missing_article_no_crash(self, tmp_db):
        apply_feedback(make_fb("dislike", raw="这篇不行", article_id=999), tmp_db)  # 不抛
        rows = self._fb_rows(tmp_db)
        assert len(rows) == 1  # 反馈文字仍落库


class TestApplyFeedbackDispatch:
    """分派器 v1 路径回归（对齐 tests/test_feedback.py 的既有断言）。"""

    def test_v1_signature_still_works(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        aid = seed_article(tmp_db)
        result = apply_feedback(tmp_db, aid, "like")
        assert result["updated"] == ["AI"]
        assert tmp_db.get_interest_weights()["AI"] == pytest.approx(1.1)

    def test_v1_extra_keyword_still_works(self, tmp_db):
        tmp_db.bump_keyword_weight("AI", 0)
        # 注：与 test_feedback.py T-FBK-04 对齐，title+summary 均避开 "AI"（默认 summary 含 "AI" 会命中）
        aid = seed_article(tmp_db, title="今天天气不错", summary="晴转多云")
        result = apply_feedback(tmp_db, aid, "like", extra_keyword="开源")
        assert result["updated"] == ["开源"]


class TestFeedbackCliRouting:
    """T-FB-10/11 CLI 文字反馈路由：feedback "<文字>" 与 like/dislike <id> 共存（docs/05 §3.3）。"""

    def _chdir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_default_config("config.yaml")

    def _seed(self, tmp_path):
        from daily_picks.storage import Storage
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        storage = Storage(tmp_path / "data" / "daily_picks.db")
        storage.init_schema()
        aid = storage.upsert_articles([
            Article(source="rss", source_key="k1", title="AI 编程工具实战",
                    url="https://example.com/a")
        ])[0]
        storage.bump_keyword_weight("AI", 0)
        return storage, aid

    # T-FB-10 CLI 文字路由
    def test_text_feedback_routes_to_text_path(self, tmp_path, monkeypatch, capsys):
        self._chdir(tmp_path, monkeypatch)

        async def fake_parse(raw, llm):
            return ParsedFeedback(raw=raw, intent="expand", article_id=None,
                                  tags=["AI硬件"], keywords=["AI硬件"], top_n=None)

        monkeypatch.setattr(cli_mod, "parse_feedback", fake_parse)
        args = argparse.Namespace(feedback_value=["多推点AI"], kind=None, keyword=None)
        assert cli_mod.cmd_feedback(args) == 0
        out = capsys.readouterr().out
        assert "意图: expand" in out
        from daily_picks.storage import Storage
        storage = Storage(tmp_path / "data" / "daily_picks.db")
        assert storage._conn.execute("SELECT COUNT(*) FROM feedback_text").fetchone()[0] == 1

    # T-FB-11 CLI 原用法兼容
    def test_like_42_keeps_v1_behavior(self, tmp_path, monkeypatch, capsys):
        self._chdir(tmp_path, monkeypatch)
        storage, aid = self._seed(tmp_path)
        args = argparse.Namespace(feedback_value=["like", str(aid)], kind=None, keyword=None)
        assert cli_mod.cmd_feedback(args) == 0
        assert "已更新关键词权重: AI" in capsys.readouterr().out
        assert storage.get_interest_weights()["AI"] == pytest.approx(1.1)

    def test_kind_flag_with_id(self, tmp_path, monkeypatch, capsys):
        self._chdir(tmp_path, monkeypatch)
        storage, aid = self._seed(tmp_path)
        args = argparse.Namespace(feedback_value=[str(aid)], kind="dislike", keyword=None)
        assert cli_mod.cmd_feedback(args) == 0
        assert storage.get_feedback_kinds(aid) == ["dislike"]

    def test_no_args_prints_usage(self, tmp_path, monkeypatch, capsys):
        self._chdir(tmp_path, monkeypatch)
        args = argparse.Namespace(feedback_value=[], kind=None, keyword=None)
        assert cli_mod.cmd_feedback(args) == 1
        assert "用法" in capsys.readouterr().err
