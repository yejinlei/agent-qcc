"""LLM client wrapper.

A single ``LlmClient`` class wraps any OpenAI-compatible endpoint.  The project
is intentionally framework-light: it uses ``langchain_openai.ChatOpenAI`` for the
actual call but adds:

* robust JSON extraction (strips markdown fences, finds the first ``{`` … last
  ``}`` so tool-call agents that wrap JSON in prose still parse cleanly);
* a plain ``invoke_text`` for free-form stages (respond);
* ``astream`` for streaming responses to the TUI / REST layer;
* a module-level usage counter so the CLI can surface token spend.

``make_llm`` returns ``None`` in offline mode (no API key) so every graph stage
can fall back to rule-based logic.  This keeps ``pytest`` and first-run demos
usable without credentials.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .config import Settings

logger = logging.getLogger("agent_qcc.llm")


@dataclass
class Usage:
    """Accumulated token usage across all calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0

    def add(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        self.prompt_tokens += int(usage.get("prompt_tokens", 0))
        self.completion_tokens += int(usage.get("completion_tokens", 0))
        self.total_tokens += int(usage.get("total_tokens", 0))
        self.calls += 1

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
        }


@dataclass
class LlmResponse:
    """Normalised LLM response."""

    content: str
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?", re.MULTILINE)


def extract_json(raw: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a model response that may wrap it in prose
    or markdown code fences.  Returns ``None`` when no valid object is found.
    """
    if not raw:
        return None
    text = raw.strip()
    # Strip markdown code fences if present.
    if text.startswith("```"):
        text = _CODE_FENCE_RE.sub("", text)
        if text.endswith("```"):
            text = text[: -3].rstrip()
    text = text.strip()
    # Fast path: the whole thing is already a JSON object.
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
    # Fallback: extract the outermost { … } span.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


class LlmClient:
    """Callable, reusable LLM client over ``ChatOpenAI``.

    Every method returns ``None`` / empty when the underlying LLM is not
    configured, so callers can branch on ``None`` and use a rule-based
    fallback without separate offline plumbing.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._llm: Any | None = None
        self.usage = Usage()
        self._init_llm()

    def _init_llm(self) -> None:
        if not self.settings.use_llm:
            return
        if self.settings.openai_api_key is None:
            return
        from langchain_openai import ChatOpenAI

        self._llm = ChatOpenAI(
            model=self.settings.model_name,
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_base_url,
            temperature=self.settings.llm_temperature,
            timeout=self.settings.llm_timeout,
            max_retries=1,
        )

    @property
    def available(self) -> bool:
        return self._llm is not None

    @property
    def model_name(self) -> str:
        return self.settings.model_name

    # -- internal ----------------------------------------------------------

    def _messages(self, system: str, human: str) -> list[Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        return [SystemMessage(content=system), HumanMessage(content=human)]

    def _response(self, raw: Any) -> LlmResponse:
        content = raw.content if isinstance(raw.content, str) else str(raw.content)
        usage = {}
        raw_usage = getattr(raw, "usage_metadata", None)
        if isinstance(raw_usage, dict):
            usage = raw_usage
        elif hasattr(raw, "response_metadata"):
            meta = raw.response_metadata or {}
            token = meta.get("token_usage") or meta.get("usage")
            if isinstance(token, dict):
                usage = token
        self.usage.add(usage or None)
        return LlmResponse(content=content, usage=usage, raw=raw)

    # -- public ------------------------------------------------------------

    async def invoke_text(self, system: str, human: str) -> str | None:
        """Free-form text completion.  ``None`` means use the fallback."""
        if self._llm is None:
            return None
        try:
            raw = await self._llm.ainvoke(self._messages(system, human))
            return self._response(raw).content
        except Exception as exc:
            logger.warning("LLM invoke_text failed: %s: %s", type(exc).__name__, exc)
            return None

    async def invoke_json(
        self, system: str, human: str, *, key: str | None = None
    ) -> dict[str, Any] | None:
        """Ask the model for a JSON object.  ``None`` means use the fallback.

        When *key* is given, the response must contain that key; otherwise the
        extracted object is still returned but ``None`` is yielded when the key
        is missing so callers fall back deterministically.
        """
        if self._llm is None:
            return None
        full_system = system
        if key:
            full_system += (
                "\n\n只输出一个 JSON 对象，键必须包含 "
                + key
                + "，不要使用 markdown 代码块。"
            )
        try:
            raw = await self._llm.ainvoke(self._messages(full_system, human))
            content = self._response(raw).content
        except Exception as exc:
            logger.warning("LLM invoke_json failed: %s: %s", type(exc).__name__, exc)
            return None
        payload = extract_json(content)
        if payload is None:
            return None
        if key and key not in payload:
            return None
        return payload

    async def astream(self, system: str, human: str) -> AsyncIterator[str]:
        """Stream text chunks.  Yields nothing when offline."""
        if self._llm is None:
            return
        try:
            async for chunk in self._llm.astream(self._messages(system, human)):
                piece = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                if piece:
                    yield piece
        except Exception:
            return

    def usage_snapshot(self) -> dict[str, int]:
        return self.usage.as_dict()


def make_llm(settings: Settings) -> LlmClient | None:
    """Factory used by the pipeline.  Returns ``None`` when offline."""
    client = LlmClient(settings)
    return client if client.available else None


# Backwards-compatible module-level function so older callers still work.
async def invoke_json(
    llm: LlmClient | None, system: str, human: str, *, key: str | None = None
) -> dict[str, Any] | None:
    if llm is None:
        return None
    return await llm.invoke_json(system, human, key=key)
