"""Test the Qichacha connector client (offline mode + helpers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_qcc.config import Settings
from agent_qcc.qcc import (
    QccClient,
    ToolResult,
    extract_target,
    parse_sse_stream,
)
from agent_qcc.skill import load_skill

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


@pytest.fixture()
def skill():
    return load_skill(SKILLS_DIR, "qcc-company")


@pytest.fixture()
def client(skill):
    settings = Settings(offline=True, use_llm=False)
    return QccClient(skill, settings)


class TestExtractTarget:
    def test_name(self):
        assert extract_target({"name": "测试公司"}) == "测试公司"

    def test_keyword(self):
        assert extract_target({"keyword": "搜索词"}) == "搜索词"

    def test_search_key(self):
        assert extract_target({"searchKey": "91XXXX"}) == "91XXXX"

    def test_fallback(self):
        assert extract_target({}) == "<unknown>"


class TestParseSseStream:
    def test_single_event(self):
        body = 'data: {"jsonrpc": "2.0", "result": {"ok": true}}\n\n'
        result = parse_sse_stream(body)
        assert result == {"jsonrpc": "2.0", "result": {"ok": True}}

    def test_multi_line_data(self):
        body = 'data: {"jsonrpc": "2.0",\ndata: "result": {"ok": true}}\n\n'
        result = parse_sse_stream(body)
        assert result["result"] == {"ok": True}

    def test_last_event_wins(self):
        body = (
            'data: {"jsonrpc": "2.0", "result": {"first": true}}\n\n'
            'data: {"jsonrpc": "2.0", "result": {"second": true}}\n\n'
        )
        result = parse_sse_stream(body)
        assert result["result"] == {"second": True}

    def test_empty(self):
        assert parse_sse_stream("") is None
        assert parse_sse_stream("event: message\n") is None


class TestToolResult:
    def test_to_evidence_ok(self):
        r = ToolResult(tool="get_company_profile", target="测试公司", ok=True, data={"x": 1}, duration_ms=50)
        e = r.to_evidence()
        assert e["tool"] == "get_company_profile"
        assert e["ok"] is True
        assert e["data"] == {"x": 1}
        assert e["duration_ms"] == 50

    def test_to_evidence_error(self):
        r = ToolResult(tool="t", target="x", ok=False, error="boom")
        e = r.to_evidence()
        assert e["ok"] is False
        assert e["error"] == "boom"


class TestQccClientOffline:
    @pytest.mark.asyncio
    async def test_call_offline_returns_error(self, client):
        result = await client.call("get_company_profile", {"name": "测试公司"})
        assert result.ok is False
        assert "离线模式" in result.error

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self, client):
        result = await client.call("nonexistent_tool", {"name": "测试"})
        assert result.ok is False
        assert "未知工具" in result.error

    @pytest.mark.asyncio
    async def test_call_missing_args(self, client):
        result = await client.call("get_company_profile", {})
        assert result.ok is False
        assert "缺少查询参数" in result.error

    @pytest.mark.asyncio
    async def test_verify_company_requires_search_key(self, client):
        result = await client.call("verify_company_accuracy", {"name": "测试公司"})
        assert result.ok is False
        assert "缺少必填参数" in result.error

    def test_validate_known_tool(self, client):
        assert client.validate_call({"tool": "get_company_profile", "args": {"name": "x"}}) is None

    def test_validate_unknown_tool(self, client):
        assert client.validate_call({"tool": "nope", "args": {"name": "x"}}) is not None

    def test_auth_headers_not_set_when_no_token(self, client):
        headers = client._headers()
        assert "Authorization" not in headers

    def test_auth_headers_set_with_token(self, skill):
        settings = Settings(offline=False, use_llm=False, qcc_auth_token="my-token")
        c = QccClient(skill, settings)
        headers = c._headers()
        assert headers["Authorization"] == "Bearer my-token"

    def test_custom_auth_header(self, skill):
        settings = Settings(
            offline=False,
            use_llm=False,
            qcc_auth_token="key-123",
            qcc_auth_header="X-API-Key",
            qcc_auth_scheme="",
        )
        c = QccClient(skill, settings)
        headers = c._headers()
        assert headers["X-API-Key"] == "key-123"

    def test_session_id_in_headers(self, client):
        """Session ids are kept per server URL, and sent to that URL only."""
        client._session_ids[client.url] = "sess-abc"
        headers = client._headers()
        assert headers["Mcp-Session-Id"] == "sess-abc"
        other = client._headers(url="https://agent.qcc.com/mcp/risk/stream")
        assert "Mcp-Session-Id" not in other

    def test_url_from_skill(self, client):
        assert client.url == "https://agent.qcc.com/mcp/company/stream"

    def test_transport_default(self, client):
        assert client.transport == "streamableHttp"
