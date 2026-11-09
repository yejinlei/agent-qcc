"""Client for the Qichacha (企查查) connector.

Transport is read from ``skills/qcc-company/mcp.json``.  The client speaks the
streamable-HTTP MCP protocol:

1. **Initialize handshake** — a ``POST`` with ``method: initialize`` to
   negotiate protocol version and server capabilities.  The session id from
   the ``Mcp-Session-Id`` header is reused for subsequent calls, and is kept
   per URL because a skill may declare several MCP servers (``qcc-super``
   routes each tool to its own server via ``ToolSpec.server``).
2. **Tool call** — ``POST`` with ``method: tools/call``.  The response may be
   a single JSON object or an SSE stream (``text/event-stream``); both are
   handled.
3. **Auth** — an optional bearer/header token injected from
   ``AGENT_QCC_QCC_AUTH_TOKEN``.
4. **Retry** — transient errors (5xx, connect, read, timeout) are retried with
   exponential backoff up to ``qcc_retries`` times.

In offline mode the client validates the call against the skill spec but never
hits the network, so ``pytest`` and first-run demos work without credentials.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .skill import Skill, ToolSpec


@dataclass(frozen=True)
class ToolResult:
    """Normalised connector result."""

    tool: str
    target: str
    ok: bool
    data: Any = None
    error: str = ""
    duration_ms: int = 0

    def to_evidence(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "target": self.target,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


def extract_target(args: dict[str, Any]) -> str:
    """Best-effort query subject for logging and audit trails."""
    for key in ("name", "keyword", "creditCode", "searchKey"):
        value = args.get(key)
        if value:
            return str(value)
    return "<unknown>"


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences from a model response."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines)
    return t.strip()


def parse_sse_stream(text: str) -> dict[str, Any] | None:
    """Parse an SSE ``text/event-stream`` body into the last JSON payload.

    Each ``data:`` line may be a JSON string or JSON-fragment; the parser
    accumulates multi-line ``data:`` blocks and returns the parsed JSON object
    from the last valid event.
    """
    payload: str = ""
    last_obj: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.strip():
            if payload:
                obj = _try_json(payload)
                if isinstance(obj, dict):
                    last_obj = obj
            payload = ""
            continue
        if line.startswith("data:"):
            payload += line[5:].strip() + "\n"
    if payload:
        obj = _try_json(payload)
        if isinstance(obj, dict):
            last_obj = obj
    return last_obj


def _try_json(text: str) -> Any:
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return None


_RETRYABLE = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.TimeoutException,
    httpx.PoolTimeout,
)


class QccClient:
    """Callable adapter over a loaded connector skill."""

    def __init__(self, skill: Skill, settings: Settings) -> None:
        self.skill = skill
        self.settings = settings
        self.tools: dict[str, ToolSpec] = {tool.name: tool for tool in skill.tools}
        #: session id 按 URL 保存 —— 一个 skill 可能声明多个 MCP server
        self._session_ids: dict[str, str] = {}
        self._client: httpx.AsyncClient | None = None

    # -- properties --------------------------------------------------------

    @property
    def url(self) -> str | None:
        return str(self.skill.transport.get("url") or "") or None

    @property
    def servers(self) -> dict[str, str]:
        """skill 声明的全部 MCP server（name → url）；单 server skill 只有一项。"""
        raw = self.skill.transport.get("servers") or {}
        default = self.url
        if not raw:
            name = str(self.skill.transport.get("server") or "")
            return {name: default} if name and default else {}
        return {str(name): str(entry.get("url") or "") for name, entry in raw.items()}

    @property
    def transport(self) -> str:
        return str(self.skill.transport.get("type") or "streamableHttp")

    def url_for(self, tool_name: str) -> str | None:
        """工具所属 server 的 URL；server 未声明或未知时回退到默认 URL。"""
        default = self.url
        spec = self.tools.get(tool_name)
        if spec is None or not spec.server:
            return default
        return self.servers.get(spec.server) or default

    def _headers(self, *, url: str | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        token = self.settings.qcc_auth_token
        if token:
            scheme = self.settings.qcc_auth_scheme
            if scheme:
                headers[self.settings.qcc_auth_header] = f"{scheme} {token}"
            else:
                headers[self.settings.qcc_auth_header] = token
        session_id = self._session_ids.get(url or self.url or "")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        if extra:
            headers.update(extra)
        return headers

    # -- validation --------------------------------------------------------

    def validate_call(self, call: dict[str, Any]) -> str | None:
        """Return a human readable reason when a planned call cannot run."""
        name = str(call.get("tool") or "")
        spec = self.tools.get(name)
        if spec is None:
            known = ", ".join(sorted(self.tools)) or "<none>"
            return f"未知工具 {name!r}，可用工具: {known}"
        args = call.get("args") or {}
        if not isinstance(args, dict) or not args:
            return f"工具 {name} 缺少查询参数，请提供企业全称或统一社会信用代码"
        missing = [arg for arg in spec.requires if not args.get(arg)]
        if missing:
            return f"工具 {name} 缺少必填参数: {', '.join(missing)}"
        return None

    # -- public call -------------------------------------------------------

    async def call(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        target = extract_target(args)
        if self.settings.offline:
            reason = self.validate_call({"tool": tool_name, "args": args})
            if reason:
                return ToolResult(
                    tool=tool_name, target=target, ok=False, error=reason,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            return ToolResult(
                tool=tool_name, target=target, ok=False,
                error="离线模式（AGENT_QCC_OFFLINE=true）：未连接企查查，仅演示编排流程",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        return await self._remote(tool_name, args, target, started)

    # -- transport ---------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.settings.qcc_timeout)
        return self._client

    async def _initialize(self, url: str | None = None) -> str | None:
        """MCP initialize handshake; returns the session id for ``url``."""
        target = url or self.url
        if not target or target in self._session_ids:
            return self._session_ids.get(target or "")
        payload = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agent-qcc", "version": "0.1.0"},
            },
        }
        client = await self._get_client()
        resp = await client.post(target, json=payload, headers=self._headers(url=target))
        session_id = resp.headers.get("Mcp-Session-Id")
        # Acknowledge with an empty notifications/initialized message.
        if session_id:
            self._session_ids[target] = session_id
            ack = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
            await client.post(target, json=ack, headers=self._headers(url=target))
        return session_id

    async def _remote(
        self, tool_name: str, args: dict[str, Any], target: str, started: float
    ) -> ToolResult:
        """Remote call with retry and SSE handling."""
        endpoint = self.url_for(tool_name)
        if not endpoint:
            return ToolResult(
                tool=tool_name, target=target, ok=False,
                error=f"connector 未配置 URL（skills/{self.skill.name}/mcp.json）",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        last_error = ""
        for attempt in range(self.settings.qcc_retries + 1):
            try:
                result = await self._call_once(tool_name, args, target, started, endpoint)
                if result.ok or not self._is_retryable_error(result.error):
                    return result
                last_error = result.error
            except _RETRYABLE as exc:
                last_error = f"{type(exc).__name__}: {exc}"[:500]
            except Exception as exc:  # noqa: BLE001
                return ToolResult(
                    tool=tool_name, target=target, ok=False,
                    error=f"{type(exc).__name__}: {exc}"[:500],
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            if attempt < self.settings.qcc_retries:
                await asyncio.sleep(self.settings.qcc_backoff * (2 ** attempt))
        return ToolResult(
            tool=tool_name, target=target, ok=False, error=last_error,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    @staticmethod
    def _is_retryable_error(error: str) -> bool:
        lower = error.lower()
        return any(t in lower for t in ("timeout", "connecterror", "readerror", "5", "502", "503", "504"))

    async def _call_once(
        self, tool_name: str, args: dict[str, Any], target: str, started: float, endpoint: str
    ) -> ToolResult:
        await self._initialize(endpoint)
        client = await self._get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
        }
        response = await client.post(endpoint, json=payload, headers=self._headers(url=endpoint))
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            data = parse_sse_stream(response.text) or {}
        else:
            data = response.json()

        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return ToolResult(
                tool=tool_name, target=target, ok=False, error=str(msg)[:500],
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        result = data.get("result", data) if isinstance(data, dict) else data
        ok = not (isinstance(result, dict) and result.get("isError"))
        extracted = self._extract_content(result)
        return ToolResult(
            tool=tool_name, target=target, ok=ok, data=extracted,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    @staticmethod
    def _extract_content(result: Any) -> Any:
        """Unwrap MCP ``content`` array into a readable structure."""
        if not isinstance(result, dict):
            return result
        content = result.get("content")
        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        texts.append(str(item.get("text", "")))
                    elif item.get("type") == "json":
                        texts.append(json.dumps(item.get("json", {}), ensure_ascii=False))
            if texts:
                joined = "\n".join(texts).strip()
                parsed = _try_json(joined)
                return parsed if parsed is not None else joined
        return result

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "QccClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
