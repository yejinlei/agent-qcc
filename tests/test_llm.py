"""Test the LLM JSON extraction logic and the offline LlmClient."""

from __future__ import annotations

import pytest

from agent_qcc.config import Settings
from agent_qcc.llm import LlmClient, Usage, extract_json, make_llm


class TestExtractJson:
    def test_plain_json(self):
        result = extract_json('{"intent": "company_info", "company": "测试公司"}')
        assert result == {"intent": "company_info", "company": "测试公司"}

    def test_json_wrapped_in_prose(self):
        raw = '好的，这是结果：{"intent": "company_info"} 希望对你有用。'
        result = extract_json(raw)
        assert result == {"intent": "company_info"}

    def test_json_in_code_fence(self):
        raw = '```json\n{"decision": "respond"}\n```'
        result = extract_json(raw)
        assert result == {"decision": "respond"}

    def test_json_in_plain_code_fence(self):
        raw = '```\n{"decision": "revise"}\n```'
        result = extract_json(raw)
        assert result == {"decision": "revise"}

    def test_empty(self):
        assert extract_json("") is None
        assert extract_json("   ") is None

    def test_no_json(self):
        assert extract_json("这里没有 JSON") is None

    def test_nested_json(self):
        raw = '{"outer": {"inner": "value"}, "list": [1, 2, 3]}'
        result = extract_json(raw)
        assert result["outer"] == {"inner": "value"}
        assert result["list"] == [1, 2, 3]

    def test_array_not_returned(self):
        # extract_json only returns dicts, not arrays
        assert extract_json("[1, 2, 3]") is None


class TestUsage:
    def test_add_accumulates(self):
        u = Usage()
        u.add({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        u.add({"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})
        assert u.prompt_tokens == 30
        assert u.completion_tokens == 15
        assert u.total_tokens == 45
        assert u.calls == 2

    def test_add_none_is_noop(self):
        u = Usage()
        u.add(None)
        u.add({})
        assert u.calls == 0

    def test_as_dict(self):
        u = Usage()
        u.add({"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8})
        d = u.as_dict()
        assert d == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8, "calls": 1}


class TestLlmClient:
    def _settings(self, **kw) -> Settings:
        kw.setdefault("use_llm", False)
        return Settings(offline=True, **kw)

    def test_offline_returns_none(self):
        client = make_llm(self._settings())
        assert client is None

    def test_no_api_key_returns_none(self):
        client = make_llm(self._settings(use_llm=True))
        assert client is None

    @pytest.mark.asyncio
    async def test_invoke_json_none_when_offline(self):
        client = LlmClient(self._settings(use_llm=True))
        assert not client.available
        result = await client.invoke_json("system", "human", key="intent")
        assert result is None

    @pytest.mark.asyncio
    async def test_invoke_text_none_when_offline(self):
        client = LlmClient(self._settings(use_llm=True))
        result = await client.invoke_text("system", "human")
        assert result is None

    @pytest.mark.asyncio
    async def test_astream_yields_nothing_when_offline(self):
        client = LlmClient(self._settings(use_llm=True))
        chunks = [chunk async for chunk in client.astream("system", "human")]
        assert chunks == []

    def test_usage_snapshot_default(self):
        client = LlmClient(self._settings(use_llm=True))
        snap = client.usage_snapshot()
        assert snap["calls"] == 0
        assert snap["total_tokens"] == 0
