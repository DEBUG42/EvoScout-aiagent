"""M4 测试：存储去重 + bot 匹配 + 管线（mock DeepSeek）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.defs import parse_agent_md
from app.ai.pipeline import PipelineSummary, fetch_and_ingest, process_bot
from app.config.settings import Settings
from app.sources.base import RawItem
from app.sources.registry import item_matches_bot
from app.storage.db import DB
from app.storage.repo import Repo
from app.storage.schema import init_db


@pytest.fixture
def db(tmp_path: Path) -> DB:
    database = DB(tmp_path / "hub.db")
    init_db(database)
    return database


@pytest.fixture
def settings() -> Settings:
    return Settings()


class MockDeepSeek:
    """按脚本返回 JSON 结果。"""

    def __init__(self, results: list[dict]):
        self.results = list(results)
        self.calls = 0

    def can_call(self, bot: str) -> bool:
        return True

    def chat_json(self, system: str, user: str, bot: str = "") -> dict:
        self.calls += 1
        return self.results.pop(0) if self.results else {"results": []}


class TestRepoDedup:
    def test_insert_dedup(self, db):
        repo = Repo(db)
        items = [
            RawItem(source="arxiv", external_id="2401.00001", title="A", kind="paper").to_db_dict(),
            RawItem(source="arxiv", external_id="2401.00002", title="B", kind="paper").to_db_dict(),
        ]
        ids1 = repo.insert_items(items)
        assert len(ids1) == 2
        ids2 = repo.insert_items(items)          # 重复运行 0 新增
        assert ids2 == []

    def test_bot_items_unique(self, db):
        repo = Repo(db)
        iid = repo.insert_items([
            RawItem(source="hn", external_id="1", title="x", kind="news").to_db_dict()
        ])[0]
        assert repo.create_bot_items("b1", [iid]) == 1
        assert repo.create_bot_items("b1", [iid]) == 0
        assert repo.create_bot_items("b2", [iid]) == 1   # 不同 bot 各自独立


class TestItemMatching:
    def _bot(self, subs: dict):
        return parse_agent_md(f"---\nname: b\nsubscriptions: {subs}\n---\n")

    def test_arxiv_category(self):
        bot = self._bot({"arxiv": ["cs.AI"]})
        item = {"source": "arxiv", "kind": "paper", "title": "Agent Benchmark",
                "summary": "", "extra_json": '{"category": "cs.AI"}'}
        assert item_matches_bot(bot, item)
        item["extra_json"] = '{"category": "cs.CV"}'
        assert not item_matches_bot(bot, item)

    def test_keyword_matches_any_source(self):
        bot = self._bot({"keywords": ["LLM"]})
        item = {"source": "s2", "kind": "paper", "title": "A New LLM Agent System",
                "summary": "", "extra_json": "{}"}
        assert item_matches_bot(bot, item)

    def test_hn_reddit_rss(self):
        bot = self._bot({"hackernews": True, "reddit": ["MachineLearning"], "rss": ["机器之心"]})
        assert item_matches_bot(bot, {"source": "hn", "kind": "news", "title": "t", "summary": "", "extra_json": "{}"})
        assert item_matches_bot(bot, {"source": "reddit:MachineLearning", "kind": "news", "title": "t", "summary": "", "extra_json": "{}"})
        assert not item_matches_bot(bot, {"source": "reddit:other", "kind": "news", "title": "t", "summary": "", "extra_json": "{}"})
        assert item_matches_bot(bot, {"source": "rss:机器之心", "kind": "news", "title": "t", "summary": "", "extra_json": "{}"})


class TestPipeline:
    def test_process_bot_scoring_and_push(self, db, settings):
        repo = Repo(db)
        bot = parse_agent_md("---\nname: b1\nsubscriptions: {}\n---\n")
        item_ids = repo.insert_items([
            RawItem(source="hn", external_id="1", title="新闻A", kind="news", summary="s").to_db_dict(),
            RawItem(source="hn", external_id="2", title="新闻B", kind="news", summary="s").to_db_dict(),
        ])
        repo.create_bot_items("b1", item_ids)
        client = MockDeepSeek([{"results": [
            {"id": 0, "score": 9.0, "digest": "重要新闻", "tags": ["AI"]},
            {"id": 1, "score": 2.0, "digest": "无关内容", "tags": []},
        ]}])
        pushed: list = []

        def push(bot_name, item, bot_item):
            pushed.append(item["title"])
            return "msg_1"

        summary = process_bot("b1", bot, repo, client, settings, Path("."), push=push)
        assert summary.ready == 1 and summary.skipped == 1 and summary.pushed == 1
        assert pushed == ["新闻A"]
        assert repo.is_pushed("b1", item_ids[0])
        assert not repo.is_pushed("b1", item_ids[1])

    def test_pipeline_json_failure_fallback(self, db, settings):
        repo = Repo(db)
        bot = parse_agent_md("---\nname: b1\n---\n")
        item_ids = repo.insert_items([
            RawItem(source="hn", external_id="1", title="新闻", kind="news", summary="原文摘要").to_db_dict()
        ])
        repo.create_bot_items("b1", item_ids)

        class FailingClient(MockDeepSeek):
            def chat_json(self, system, user, bot=""):
                raise __import__("app.ai.client", fromlist=["AiError"]).AiError("API 挂了")

        summary = process_bot("b1", bot, repo, FailingClient([]), settings, Path("."))
        assert summary.failed == 1
        row = repo.get_bot_items("b1")[0]
        assert row["status"] == "failed"

    def test_fetch_and_ingest(self, db, settings, tmp_path: Path):
        repo = Repo(db)

        class FakeSource:
            name = "fake"
            def fetch(self):
                return [RawItem(source="fake", external_id="e1", title="t1", kind="news")]
            def healthy(self):
                return True

        class FakeRegistry:
            def bots(self):
                return [parse_agent_md("---\nname: b1\nsubscriptions: {keywords: [t1]}\n---\n")]

        counts = fetch_and_ingest({"fake": FakeSource()}, FakeRegistry(), repo)
        assert counts["fake"] == 1
        assert repo.get_bot_items("b1")  # 匹配上 keyword t1

    def test_kv_and_confirmations(self, db):
        repo = Repo(db)
        repo.kv_set("k", "v1")
        assert repo.kv_get("k") == "v1"
        repo.kv_set("k", "v2")
        assert repo.kv_get("k") == "v2"
        token = repo.create_confirmation("bot", "user1", "rm x", "args")
        assert repo.consume_confirmation(token, "user2") is None   # 他人不可消费
        rec = repo.consume_confirmation(token, "user1")
        assert rec and rec["command"] == "rm x"
        assert repo.consume_confirmation(token, "user1") is None   # 一次性

    def test_event_dedup(self, db):
        repo = Repo(db)
        assert repo.claim_event("ev1")
        assert not repo.claim_event("ev1")
        assert repo.claim_event("ev2")
