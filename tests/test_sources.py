"""T-SRC 采集适配器用例（测试文档 §4.3，全部 respx mock，禁止真实网络请求）。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from daily_picks.config import SourceSection
from daily_picks.sources import UA, build_adapters
from daily_picks.sources.base import SUMMARY_MAX_CHARS, SourceError, to_local_datetime
from daily_picks.sources.bilibili import BilibiliAdapter
from daily_picks.sources.hnews import HNewsAdapter, parse_iso_z
from daily_picks.sources.infoq import InfoQAdapter
from daily_picks.sources.juejin import JuejinAdapter
from daily_picks.sources.rss import RssAdapter
from daily_picks.sources.zhihu import ZhihuAdapter

FIXTURES = Path(__file__).parent / "fixtures"

RSS_URL = "https://sspai.com/feed"
RSS_URL2 = "https://www.ruanyifeng.com/blog/atom.xml"
BILI_URL = "https://api.bilibili.com/x/web-interface/popular"
ZHIHU_URL = "https://api.zhihu.com/topstory/hot-lists/total"
JUEJIN_URL = "https://api.juejin.cn/recommend_api/v1/article/recommend_all_feed"
HN_URL = "https://hn.algolia.com/api/v1/search"
INFOQ_URL = "https://www.infoq.cn/feed"


def load(name: str) -> str:
    """读 fixtures/ 下测试数据（测试文档 §3）。"""
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def client() -> httpx.AsyncClient:
    """测试用 AsyncClient（respx 拦截 transport，不会发真实请求）。

    trust_env=False：不读取环境代理（本机 all_proxy=socks5 且未装 socksio，构造即抛 ImportError）。
    """
    return httpx.AsyncClient(trust_env=False)


def mock_rss(mock_http, url: str = RSS_URL, content: str | None = None, status: int = 200):
    """注册 RSS 路由（默认返回 rss_sample.xml）。"""
    return mock_http.get(url).mock(
        return_value=httpx.Response(status, content=content if content is not None else load("rss_sample.xml"))
    )


class TestRss:
    """T-SRC-RSS-01~06（测试文档 §4.3）。"""

    async def test_parse_basic(self, mock_http, client):  # T-SRC-RSS-01
        mock_rss(mock_http)
        articles = await RssAdapter().fetch(SourceSection(urls=[RSS_URL]), client)
        assert len(articles) == 2
        a = articles[0]
        assert a.source == "rss"
        assert a.title == "AI 编程工具实战"
        assert a.url == "https://example.com/post/1"
        assert a.author == "张三"
        assert a.source_key == "https://example.com/post/1"
        assert a.published_at is not None
        assert a.published_at.year == 2026
        assert articles[1].title == "前端性能优化指南"

    async def test_summary_strips_html(self, mock_http, client):  # T-SRC-RSS-02
        mock_rss(mock_http)
        articles = await RssAdapter().fetch(SourceSection(urls=[RSS_URL]), client)
        summary = articles[0].summary
        assert summary is not None
        assert "<p>" not in summary and "</p>" not in summary
        assert summary == "用 AI 写代码的十个技巧，亲测有效。"
        assert len(summary) <= 200

    async def test_missing_guid_uses_link(self, mock_http, client):  # T-SRC-RSS-03
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><title>t</title>
          <item><title>无 guid 条目</title><link>https://example.com/noguid</link>
                <description>desc</description></item>
        </channel></rss>"""
        mock_rss(mock_http, content=xml)
        articles = await RssAdapter().fetch(SourceSection(urls=[RSS_URL]), client)
        assert len(articles) == 1
        assert articles[0].source_key == "https://example.com/noguid"

    async def test_single_url_failure_isolated(self, mock_http, client):  # T-SRC-RSS-04
        mock_rss(mock_http, url=RSS_URL)
        mock_http.get(RSS_URL2).mock(return_value=httpx.Response(500))
        cfg = SourceSection(urls=[RSS_URL, RSS_URL2])
        adapter = RssAdapter()
        articles = await adapter.fetch(cfg, client)  # 异常被吞，不抛出
        assert len(articles) == 2  # 仅来自正常 URL 的条目
        assert adapter.source_errors == 1  # 失败计数（设计文档 §6.7）

    async def test_empty_feed(self, mock_http, client):  # T-SRC-RSS-05
        xml = '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title></channel></rss>'
        mock_rss(mock_http, content=xml)
        assert await RssAdapter().fetch(SourceSection(urls=[RSS_URL]), client) == []

    async def test_empty_title_dropped(self, mock_http, client):  # T-SRC-RSS-06
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><title>t</title>
          <item><title>  </title><link>https://example.com/empty</link><description>d</description></item>
          <item><title>正常条目</title><link>https://example.com/ok</link><description>d</description></item>
          <item><title>无链接条目</title><description>d</description></item>
        </channel></rss>"""
        mock_rss(mock_http, content=xml)
        articles = await RssAdapter().fetch(SourceSection(urls=[RSS_URL]), client)
        assert len(articles) == 1  # 空标题与缺 id/link 的条目均被跳过
        assert articles[0].title == "正常条目"
        assert articles[0].published_at is not None  # 无 pubDate → 用当前时间（§6.1）


class TestRssHelpers:
    """RssAdapter 私有解析函数的补充用例（提升分支覆盖）。"""

    def test_parse_published_none(self):
        assert RssAdapter._parse_published(None) is not None  # → datetime.now()

    def test_parse_published_invalid_struct(self):
        bad = time.struct_time((2026, 13, 40, 99, 99, 99, 0, 0, -1))  # timegm 抛 ValueError
        assert RssAdapter._parse_published(bad) is not None  # → datetime.now()

    def test_clean_bad_url(self):
        with pytest.raises(SourceError):
            RssAdapter()._clean("标题", None, "ftp://example.com/x")

    def test_clean_empty_summary_text(self):
        title, summary, url = RssAdapter()._clean(" t ", "<p>  </p>", "https://example.com")
        assert (title, summary, url) == ("t", None, "https://example.com")

    def test_clean_truncates_long_summary(self):
        long_summary = "<p>" + "长" * 250 + "</p>"  # 超 200 字 → 截断
        _, summary, _ = RssAdapter()._clean("标题", long_summary, "https://example.com")
        assert summary is not None
        assert len(summary) == SUMMARY_MAX_CHARS

    def test_to_local_datetime_tolerant(self):
        assert to_local_datetime(1787702400) == datetime.fromtimestamp(1787702400)
        assert to_local_datetime("1787702400") == datetime.fromtimestamp(1787702400)  # 数字字符串
        assert to_local_datetime(None) is None
        assert to_local_datetime("not-a-number") is None  # 非法值 → None


class TestBilibili:
    """T-SRC-BILI-01~03（测试文档 §4.3）。"""

    async def test_parse_basic(self, mock_http, client):  # T-SRC-BILI-01
        mock_http.get(BILI_URL, params={"ps": 20, "pn": 1}).mock(
            return_value=httpx.Response(200, json=json.loads(load("bilibili_sample.json")))
        )
        articles = await BilibiliAdapter().fetch(SourceSection(ps=20, max_items_per_source=20), client)
        assert len(articles) == 1
        a = articles[0]
        assert a.source == "bilibili"
        assert a.source_key == "117155953776818"
        assert a.title == "世界伊始——《伊莫》全球上线定档"
        assert a.url == "https://www.bilibili.com/video/BV1wwhG6JEnc"
        assert a.author == "伊莫"
        assert a.summary == "PC端9月16日，移动端9月23日！"
        assert a.published_at == datetime.fromtimestamp(1787702400)

    async def test_nonzero_code_returns_empty(self, mock_http, client):  # T-SRC-BILI-02
        mock_http.get(BILI_URL).mock(return_value=httpx.Response(200, json={"code": -400}))
        adapter = BilibiliAdapter()
        assert await adapter.fetch(SourceSection(ps=20), client) == []
        assert adapter.source_errors == 1  # 失败计数（设计文档 §6.7）

    async def test_missing_bvid_skipped(self, mock_http, client):  # T-SRC-BILI-03
        payload = {"code": 0, "data": {"list": [
            {"aid": 1, "title": "缺 bvid", "pubdate": 1787702400},
            {"aid": 2, "bvid": "BV2", "title": "   ", "pubdate": 1787702400},  # 清洗后空标题
            {"aid": 3, "bvid": "BV3", "title": "正常视频", "owner": {"name": "up3"}, "pubdate": 1787702400},
        ]}}
        mock_http.get(BILI_URL).mock(return_value=httpx.Response(200, json=payload))
        articles = await BilibiliAdapter().fetch(SourceSection(ps=20), client)
        assert [a.source_key for a in articles] == ["3"]

    async def test_missing_data_tolerated(self, mock_http, client):
        mock_http.get(BILI_URL).mock(return_value=httpx.Response(200, json={"code": 0}))
        assert await BilibiliAdapter().fetch(SourceSection(ps=20), client) == []


class TestZhihu:
    """T-SRC-ZHIHU-01~03（测试文档 §4.3）。"""

    async def test_parse_basic_url_replaced(self, mock_http, client):  # T-SRC-ZHIHU-01
        mock_http.get(ZHIHU_URL).mock(
            return_value=httpx.Response(200, json=json.loads(load("zhihu_sample.json")))
        )
        articles = await ZhihuAdapter().fetch(SourceSection(limit=50), client)
        assert len(articles) == 1
        a = articles[0]
        assert a.source == "zhihu"
        assert a.source_key == "2075969425702741132"
        assert "api.zhihu.com" not in a.url
        assert a.url == "https://www.zhihu.com/question/2075969425702741132"
        assert a.title.startswith("西藏日喀则")
        assert a.summary is not None and "泥石流" in a.summary
        assert a.published_at == datetime.fromtimestamp(1787729680)
        assert a.raw is not None and "answer_count" in a.raw  # 不进库字段保留在 raw

    async def test_empty_data_returns_empty(self, mock_http, client):  # T-SRC-ZHIHU-02
        mock_http.get(ZHIHU_URL).mock(return_value=httpx.Response(200, json={"data": []}))
        assert await ZhihuAdapter().fetch(SourceSection(limit=50), client) == []

    async def test_missing_excerpt_tolerated(self, mock_http, client):  # T-SRC-ZHIHU-03
        payload = {"data": [
            {"target": {"id": 1, "title": "无 excerpt 条目", "url": "https://api.zhihu.com/q/1",
                        "created": 1787729680}},
            {"target": {"id": 2, "url": "https://api.zhihu.com/q/2"}},  # 缺 title → 跳过
            {"target": {"id": 3, "title": "   ", "url": "https://api.zhihu.com/q/3"}},  # 清洗后空标题 → 跳过
        ]}
        mock_http.get(ZHIHU_URL).mock(return_value=httpx.Response(200, json=payload))
        articles = await ZhihuAdapter().fetch(SourceSection(limit=50), client)
        assert len(articles) == 1
        assert articles[0].summary is None  # 缺 excerpt 容错，不崩溃
        assert articles[0].source_key == "1"


class TestJuejin:
    """T-SRC-JUEJIN-01~03（测试文档 §4.3）。"""

    async def test_parse_basic(self, mock_http, client):  # T-SRC-JUEJIN-01
        mock_http.post(JUEJIN_URL).mock(
            return_value=httpx.Response(200, json=json.loads(load("juejin_sample.json")))
        )
        articles = await JuejinAdapter().fetch(SourceSection(limit=20), client)
        assert len(articles) == 1
        a = articles[0]
        assert a.source == "juejin"
        assert a.source_key == "7637856870833635343"
        assert a.url == "https://juejin.cn/post/7637856870833635343"
        assert a.author == "深小乐"
        assert a.title == "Cursor 转 Codex 大半个月，聊聊我的真实感受"
        assert a.published_at == datetime.fromtimestamp(1778379468)

    async def test_nonzero_err_no_returns_empty(self, mock_http, client):  # T-SRC-JUEJIN-02
        mock_http.post(JUEJIN_URL).mock(return_value=httpx.Response(200, json={"err_no": 1}))
        assert await JuejinAdapter().fetch(SourceSection(limit=20), client) == []

    async def test_ctime_string_and_garbage_tolerated(self, mock_http, client):
        # 实测掘金 ctime 为字符串（见开发文档 §4.4 修订说明），必须容错
        payload = {"err_no": 0, "data": [
            {"item_info": {"article_info": {"article_id": "9", "title": "字符串时间戳",
                                            "ctime": "1778379468"}}},
            {"item_info": {"article_info": {"article_id": "10", "title": "非法时间戳",
                                            "ctime": "not-a-number"}}},
        ]}
        mock_http.post(JUEJIN_URL).mock(return_value=httpx.Response(200, json=payload))
        articles = await JuejinAdapter().fetch(SourceSection(limit=20), client)
        assert len(articles) == 2
        assert articles[0].published_at == datetime.fromtimestamp(1778379468)
        assert articles[1].published_at is None  # 非法值 → None，不崩溃

    async def test_request_headers_and_skip_missing_fields(self, mock_http, client):  # T-SRC-JUEJIN-03
        payload = {"err_no": 0, "data": [
            {"item_type": 2},  # 无 item_info → 跳过
            {"item_info": {"article_info": {"article_id": "42", "title": "无 brief 与作者"}}},
            {"item_info": {"article_info": {"article_id": "43", "title": "   "}}},  # 清洗后空标题 → 跳过
        ]}
        route = mock_http.post(JUEJIN_URL).mock(return_value=httpx.Response(200, json=payload))
        articles = await JuejinAdapter().fetch(SourceSection(limit=20), client)
        assert [a.source_key for a in articles] == ["42"]
        assert articles[0].summary is None and articles[0].author is None
        request = route.calls.last.request
        assert request.headers["Origin"] == "https://juejin.cn"
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["User-Agent"] == UA
        assert json.loads(request.content) == {"id_type": 2, "sort_type": 200, "cursor": "0", "limit": 20}


class TestHNews:
    """T-SRC-HN-01~03（测试文档 §4.3）。"""

    async def test_parse_basic(self, mock_http, client):  # T-SRC-HN-01
        mock_http.get(HN_URL).mock(
            return_value=httpx.Response(200, json=json.loads(load("hnews_sample.json")))
        )
        articles = await HNewsAdapter().fetch(SourceSection(hits_per_page=30), client)
        assert len(articles) == 2
        a = articles[0]
        assert a.source == "hnews"
        assert a.source_key == "49448321"
        assert a.title == "AWS Acquires DuckLabs"
        # 2026-09-01 修订：url 一律 HN 讨论页（Launch HN 类原 url 是官网主页，语义不符）
        assert a.url == "https://news.ycombinator.com/item?id=49448321"
        assert a.author == "onderkalaci"
        assert a.published_at == datetime.fromisoformat("2026-08-26T12:59:26+00:00").astimezone().replace(
            tzinfo=None
        )
        assert a.raw is not None and "points" in a.raw  # 不进库字段保留在 raw

    async def test_null_url_falls_back_to_item_page(self, mock_http, client):  # T-SRC-HN-02
        mock_http.get(HN_URL).mock(
            return_value=httpx.Response(200, json=json.loads(load("hnews_sample.json")))
        )
        articles = await HNewsAdapter().fetch(SourceSection(hits_per_page=30), client)
        assert articles[1].url == "https://news.ycombinator.com/item?id=49400000"

    async def test_params_and_failure_isolated(self, mock_http, client):  # T-SRC-HN-03
        captured: dict = {}
        state = {"n": 0}

        def handler(request):
            state["n"] += 1
            captured["url"] = request.url
            if state["n"] > 1:
                return httpx.Response(500)
            return httpx.Response(200, json={"hits": [
                {"objectID": "1"},  # 缺 title → 跳过
                {"objectID": "2", "title": "无 created_at 条目", "url": "https://example.com/h2"},
                # 2026-09-01 修订：url 恒为 HN 讨论页（Algolia url 不再使用），原"非 http(s)
                # 链接清洗失败"场景不复存在；条目 3 正常入列
                {"objectID": "3", "title": "第三条", "url": "ftp://bad.example.com"},
            ]})

        mock_http.get(HN_URL).mock(side_effect=handler)
        articles = await HNewsAdapter().fetch(SourceSection(hits_per_page=25), client)
        assert captured["url"].params["hitsPerPage"] == "25"
        assert captured["url"].params["tags"] == "front_page"
        assert [a.source_key for a in articles] == ["2", "3"]
        assert articles[0].published_at is None  # 缺 created_at → parse_iso_z(None)
        assert await HNewsAdapter().fetch(SourceSection(hits_per_page=25), client) == []  # 500 隔离

    def test_parse_iso_z(self):
        assert parse_iso_z("not-a-date") is None  # 解析失败
        assert parse_iso_z(None) is None
        dt = parse_iso_z("2026-08-26T12:59:26Z")
        assert dt == datetime.fromisoformat("2026-08-26T12:59:26+00:00").astimezone().replace(tzinfo=None)


class TestInfoQ:
    """T-SRC-INFOQ-01~03（测试文档 §4.3）。"""

    async def test_parse_basic_default_url(self, mock_http, client):  # T-SRC-INFOQ-01
        mock_http.get(INFOQ_URL).mock(
            return_value=httpx.Response(200, content=load("infoq_sample.xml"))
        )
        articles = await InfoQAdapter().fetch(SourceSection(), client)  # urls 空 → default_urls
        assert len(articles) == 1
        a = articles[0]
        assert a.source == "infoq"
        assert a.title == "Aspire 13.5 正式发布"
        assert a.url == "https://www.infoq.cn/article/1"

    async def test_empty_feed(self, mock_http, client):  # T-SRC-INFOQ-02
        xml = '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title></channel></rss>'
        custom = "https://custom.example.com/feed"  # 覆盖 cfg.urls 分支
        mock_http.get(custom).mock(return_value=httpx.Response(200, content=xml))
        assert await InfoQAdapter().fetch(SourceSection(urls=[custom]), client) == []

    async def test_inherits_rss_cleaning(self, mock_http, client):  # T-SRC-INFOQ-03
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><title>InfoQ</title>
          <item><title>带标签条目</title><link>https://www.infoq.cn/article/2</link>
                <description>&lt;p&gt;段落一&lt;/p&gt;&lt;b&gt;段落二&lt;/b&gt;</description></item>
        </channel></rss>"""
        mock_http.get(INFOQ_URL).mock(return_value=httpx.Response(200, content=xml))
        articles = await InfoQAdapter().fetch(SourceSection(), client)
        assert len(articles) == 1
        summary = articles[0].summary
        assert summary is not None
        assert "<p>" not in summary and "<b>" not in summary
        assert len(summary) <= 200


class TestBuildAdapters:
    """T-SRC-ALL-01~02（测试文档 §4.3）。"""

    def test_build_order_follows_enabled(self, sample_config):  # T-SRC-ALL-01
        adapters = build_adapters(sample_config)
        assert [a.name for a in adapters] == ["rss", "bilibili", "zhihu", "juejin", "hnews", "infoq"]
        assert isinstance(adapters[0], RssAdapter)
        assert isinstance(adapters[1], BilibiliAdapter)
        assert isinstance(adapters[2], ZhihuAdapter)
        assert isinstance(adapters[3], JuejinAdapter)
        assert isinstance(adapters[4], HNewsAdapter)
        assert isinstance(adapters[5], InfoQAdapter)

    def test_unknown_name_skipped_with_warning(self, sample_config, caplog):
        sample_config.sources.enabled = ["rss", "foo"]
        with caplog.at_level(logging.WARNING, logger="daily_picks.sources"):
            adapters = build_adapters(sample_config)
        assert [a.name for a in adapters] == ["rss"]
        assert "foo" in caplog.text

    async def test_source_timeout_isolation(self, sample_config, tmp_path,
                                            monkeypatch, caplog):  # T-SRC-ALL-02
        import respx

        import daily_picks.cli as cli_mod
        from daily_picks.storage import Storage

        monkeypatch.setattr(cli_mod, "SOURCE_TIMEOUT_S", 0.1)  # 测试注入，避免真实等待 30s
        cfg = sample_config
        cfg.sources.enabled = ["rss", "bilibili"]
        cfg.sources.rss.urls = [RSS_URL]
        cfg.logging.file = str(tmp_path / "run.log")

        # 挂起请求被 wait_for 取消，respx 视该路由为"未调用"，故此处关掉 assert_all_called
        with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock_http:
            mock_http.get(RSS_URL).mock(return_value=httpx.Response(200, content=load("rss_sample.xml")))

            async def _hang(request):  # 模拟挂起源（> 超时）
                await asyncio.sleep(30)
                return httpx.Response(200, json={"code": 0, "data": {"list": []}})

            mock_http.get(BILI_URL).mock(side_effect=_hang)

            with caplog.at_level(logging.WARNING):
                rc = await cli_mod.run_once(cfg, dry_run=True)

        assert rc == 0  # 单源失败不影响整体
        assert "采集失败 source=bilibili" in caplog.text  # 该源被超时掐断
        rows = Storage(Path(cfg.storage.db_path)).get_articles_by_ids([1, 2])
        assert len(rows) == 2  # 其余源（rss）正常入库

    async def test_run_once_partial_source_failure(self, sample_config, mock_http, tmp_path):
        # 补充：适配器内部吞掉的失败（source_errors>0）也要如实标记，且部分成功条目仍入库
        import daily_picks.cli as cli_mod
        from daily_picks.storage import Storage

        cfg = sample_config
        cfg.sources.enabled = ["rss"]
        cfg.sources.rss.urls = [RSS_URL, RSS_URL2]
        cfg.logging.file = str(tmp_path / "run.log")
        mock_http.get(RSS_URL).mock(return_value=httpx.Response(200, content=load("rss_sample.xml")))
        mock_http.get(RSS_URL2).mock(return_value=httpx.Response(500))

        rc = await cli_mod.run_once(cfg, dry_run=True)
        assert rc == 1  # 唯一启用的源存在失败请求 → 部分失败
        rows = Storage(Path(cfg.storage.db_path)).get_articles_by_ids([1, 2])
        assert len(rows) == 2  # 部分成功的条目仍入库
