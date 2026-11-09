"""Live integration tests: the real 企查查 connector and a real LLM endpoint.

Excluded from the default run by ``addopts = "-q -m 'not integration'"``, and every
test skips itself when a credential is missing, so enabling them by accident is safe.

Run them explicitly, with real credentials exported in the process environment
(a checked-in ``.env`` must not silently turn this into a network call — see
``live_settings`` below):

    set AGENT_QCC_QCC_AUTH_TOKEN=<token>
    set AGENT_QCC_OPENAI_API_KEY=<key>
    set AGENT_QCC_OPENAI_BASE_URL=http://47.93.82.102:9090/v1
    set AGENT_QCC_MODEL_NAME=vmodel
    python -m pytest -m integration tests/test_pipeline_integration_live.py -vv

Company name matters: ``COMPANY`` is a real registered entity (the connector
returns data for it), ``UNKNOWN_COMPANY`` does not exist (the connector returns
a validation error for it).  They are both needed — the success path and the
degradation path cannot come from one query.

Assertions stay coarse where the external data is mutable, and are pinned exactly
where they document a product defect instead of a contract.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agent_qcc.config import Settings
from agent_qcc.graph import build_graph
from agent_qcc.pipeline import CORE_TOOLS, Pipeline
from agent_qcc.qcc import QccClient
from agent_qcc.skill import load_skill
from agent_qcc.state import new_state

pytestmark = pytest.mark.integration

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"  # as in the other test files
COMPANY = "腾讯科技(深圳)有限公司"  # real registered entity
UNKNOWN_COMPANY = "北京示例科技有限公司"  # does not exist in the registry
QUERY = f"查询：{COMPANY}｜注册资本、股东结构、实际控制人"
CHIT_CHAT = "你好，今天天气怎么样"

QCC_TOKEN_ENV = "AGENT_QCC_QCC_AUTH_TOKEN"
OPENAI_KEY_ENV = "AGENT_QCC_OPENAI_API_KEY"
OPENAI_BASE_URL_ENV = "AGENT_QCC_OPENAI_BASE_URL"
MODEL_NAME_ENV = "AGENT_QCC_MODEL_NAME"
EVIDENCE_KEYS = {"tool", "target", "ok", "data", "error", "duration_ms"}


def live_settings() -> Settings:
    """Credentials and the LLM endpoint come from the process environment only.

    An empty string is not a credential, so a checked-in ``.env`` with placeholder
    values cannot silently turn these tests into network calls.  ``conftest.py``
    already clears the credential keys for the rest of the suite; these tests set
    them explicitly.  The model name falls back to the declared default rather than
    to a hardcoded alias, so a different endpoint does not need a code change.
    """
    return Settings(
        _env_file=None,
        offline=False,
        use_llm=True,
        qcc_auth_token=os.environ.get(QCC_TOKEN_ENV),
        openai_api_key=os.environ.get(OPENAI_KEY_ENV),
        openai_base_url=os.environ.get(OPENAI_BASE_URL_ENV) or "https://api.openai.com/v1",
        model_name=os.environ.get(MODEL_NAME_ENV) or Settings.model_name,
        qcc_retries=1,
        qcc_timeout=30,
    )


def _require_credentials() -> None:
    """Skip this test unless both real credentials are exported."""
    missing = [key for key in (QCC_TOKEN_ENV, OPENAI_KEY_ENV) if not os.environ.get(key)]
    if missing:
        pytest.skip(f"missing {' and '.join(missing)} — live test only")


@pytest.fixture()
def skill():
    _require_credentials()
    return load_skill(SKILLS_DIR, "qcc-company")


@pytest.fixture()
def settings():
    _require_credentials()
    return live_settings()


async def live_trace(pipeline: Pipeline, user_text: str) -> tuple[list[str], dict[str, Any]]:
    """One graph traversal that yields both the node sequence and the final state."""
    graph = build_graph(pipeline)
    sequence: list[str] = []
    state: dict[str, Any] = {}
    async for mode, payload in graph.astream(new_state(user_text), stream_mode=["updates", "values"]):
        if mode == "updates":
            # slash 网关只处理 /xxx 输入，自然语言轮次里它不属于 4R+1 顺序
            sequence.extend(str(name) for name in payload if name != "slash")
        else:
            state.update(payload)
    return sequence, state


# ---------------------------------------------------------------------------
# connector: one real call, both outcomes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_connector_report_is_well_formed(skill, settings):
    """Data or a reason, always the same ``ToolResult`` shape — never an exception."""
    client = QccClient(skill, settings)
    try:
        result = await client.call("get_company_registration_info", {"name": COMPANY})
    finally:
        await client.close()

    evidence = result.to_evidence()
    assert set(evidence) == EVIDENCE_KEYS
    assert evidence["tool"] == "get_company_registration_info"
    assert evidence["target"] == COMPANY
    assert isinstance(evidence["ok"], bool)
    assert isinstance(evidence["duration_ms"], int)
    if evidence["ok"]:
        assert evidence["data"] is not None
    else:
        assert evidence["error"]


@pytest.mark.asyncio
async def test_live_connector_needs_the_searchkey_parameter(skill, settings):
    """DEFECT (product bug, not fixed here): the local contract and the live API disagree.

    Every plan in the codebase is built from ``{"name": company}`` (the rule fallback
    in ``Pipeline.reason`` and ``_tools_for``; 14 of the 15 skill tools declare
    ``requires=()``), and ``QccClient.validate_call`` — the offline gate — therefore
    accepts it.  The live connector, however, rejects it and demands ``searchKey``.
    The two halves of the same tool call are validated by two different rules.

    Reproduce: ``client.call(tool, {"name": COMPANY})`` fails with
    ``invalid_type ... path: ["searchKey"]`` while ``{"searchKey": COMPANY}`` succeeds.

    Consequences: the offline demo validates a call shape the real endpoint rejects,
    so nothing until the first production call ever notices; and the live run only
    works because the model guesses ``searchKey`` during reflection —
    ``test_live_reflect_loop_self_heals_the_parameter_name`` pins that.
    """
    client = QccClient(skill, settings)
    try:
        failed = await client.call("get_company_registration_info", {"name": COMPANY})
        worked = await client.call("get_company_registration_info", {"searchKey": COMPANY})
    finally:
        await client.close()

    assert failed.ok is False
    assert "searchKey" in str(failed.error)
    assert worked.ok is True
    assert worked.data
    assert client.validate_call({"tool": "get_company_registration_info", "args": {"name": COMPANY}}) is None


# ---------------------------------------------------------------------------
# full chain against real services
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_pipeline_runs_end_to_end(skill, settings):
    """4R+1 with a real connector and a real model: it retrieves and then answers."""
    pipeline = Pipeline(skill, settings)
    try:
        sequence, state = await live_trace(pipeline, QUERY)
    finally:
        await pipeline.client.close()

    assert sequence and sequence[-1] == "respond"
    assert "route" in sequence and "retrieve" in sequence and "reflect" in sequence
    assert state["route"]["company"] == COMPANY
    assert state["evidence"], "the connector produced no evidence at all"
    assert any(item["ok"] for item in state["evidence"]), [
        (item["tool"], item["error"]) for item in state["evidence"]
    ]
    answer = str(state["messages"][-1].content)
    assert answer.strip()
    assert len(answer) > 100


@pytest.mark.asyncio
async def test_live_reflect_and_respond_use_the_model(skill, settings):
    """With a live model, reflect is decided by the model, not by the rules."""
    pipeline = Pipeline(skill, settings)
    assert pipeline.llm is not None
    try:
        sequence, state = await live_trace(pipeline, QUERY)
    finally:
        await pipeline.client.close()

    assert sequence[-1] == "respond"
    assert state["decision"] in ("respond", "clarify", "chitchat")
    assert state["reflections"]
    reflection = state["reflections"][-1]
    assert reflection["coverage"], "the model reported nothing it could cover"
    assert reflection["gaps"] == []
    assert isinstance(state["iteration"], int) and state["iteration"] >= 1
    assert pipeline.llm.usage.calls >= 1
    assert pipeline.llm.usage.total_tokens >= 1


@pytest.mark.asyncio
async def test_live_reflect_loop_self_heals_the_parameter_name(skill, settings):
    """The rule-based plan uses `name`; the reflect loop rewrites it to `searchKey`.

    This is the only reason the live chain returns data at all, so it is worth pinning
    — but it is also the whole quality of the "reflect" stage resting on a single
    model guess.  The revised plan is empty and the loop burns out when the model
    does not guess (``_fixes`` always returns ``[]``, see the offline suite).
    """
    pipeline = Pipeline(skill, settings)
    try:
        sequence, state = await live_trace(pipeline, QUERY)
    finally:
        await pipeline.client.close()

    assert "retrieve" in sequence, "the loop never re-ran the connector"
    first_round = state["evidence"][: len(CORE_TOOLS)]
    later_round = state["evidence"][len(CORE_TOOLS):]
    assert first_round and all(call["ok"] is False for call in first_round)
    assert later_round, "nothing was retrieved after the first round"
    assert any(call["ok"] for call in later_round)


@pytest.mark.asyncio
async def test_live_query_with_no_registered_company(skill, settings):
    """Unknown company: the connector errors and the pipeline still answers."""
    pipeline = Pipeline(skill, settings)
    try:
        sequence, state = await live_trace(pipeline, f"查询：{UNKNOWN_COMPANY}｜注册资本、股东结构")
    finally:
        await pipeline.client.close()

    assert sequence[-1] == "respond"
    assert state["route"]["company"] == UNKNOWN_COMPANY
    assert state["evidence"]
    assert any(call["ok"] is False for call in state["evidence"])
    errors = " ".join(str(call["error"]) for call in state["evidence"] if call["error"])
    assert errors, "a failed connector call left no error message behind"
    assert str(state["messages"][-1].content).strip()


# ---------------------------------------------------------------------------
# out-of-scope input and usage accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_chitchat_short_circuits_but_answers_with_the_respond_prompt(skill, settings):
    """DEFECT (product bug, not fixed here): chitchat renders as an evidence report.

    ``route`` reports ``intent="chitchat"`` and the graph skips ``reason``/``retrieve``
    (see the node sequence), but ``reflect`` never runs, so ``state["decision"]`` keeps
    the ``"respond"`` value that ``new_state()`` seeds and ``respond`` dispatches on
    ``state.get("decision") or "respond"``.  ``_chitchat()`` is never called, so the
    capability-boundary prompt is never used — the answer is produced by ``_render``,
    whose payload contains no evidence at all.

    The text still turns out fine because the respond prompt is written to say
    "only answer from the evidence", but the dispatch is wrong: an upper layer that
    branches on ``decision`` cannot tell a chat reply from a real answer, and the
    whole short-circuit exists to avoid that.  The offline suite pins the mechanism
    with a stub; this proves it against a real model.

    Reproduce: ``sequence == ["route", "respond"]`` with
    ``state["decision"] == "respond"`` and ``state["route"]["intent"] == "chitchat"``.
    """
    pipeline = Pipeline(skill, settings)
    try:
        sequence, state = await live_trace(pipeline, CHIT_CHAT)
    finally:
        await pipeline.client.close()

    assert sequence == ["route", "respond"]
    assert state["route"]["intent"] == "chitchat"
    assert state["decision"] == "respond"  # the seeded value won, not the routed intent
    assert state["evidence"] == []
    assert str(state["messages"][-1].content).strip()


@pytest.mark.asyncio
async def test_live_token_accounting_uses_langchain_field_names(skill, settings):
    """DEFECT (product bug, not fixed here): the live endpoint's tokens are not counted.

    ``llm.Usage.add`` reads ``prompt_tokens`` / ``completion_tokens``, but
    ``ChatOpenAI`` reports them as ``input_tokens`` / ``output_tokens`` in
    ``usage_metadata``.  The live endpoint does report them (asserted below), so the
    split collapses to zero and only ``total_tokens`` survives.

    Reproduce: route one real response through ``LlmClient._response`` and read
    ``usage_snapshot()`` — ``prompt_tokens`` and ``completion_tokens`` are both 0 while
    ``total_tokens`` is well above zero.  The CLI's token display and any per-token
    cost estimate therefore always read zero here — and, by construction, against
    every OpenAI-compatible API, since ``usage_metadata`` is langchain's own shape.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    pipeline = Pipeline(skill, settings)
    assert pipeline.llm is not None
    try:
        raw = await pipeline.llm._llm.ainvoke(
            [
                SystemMessage(content="把下面内容解析成一个 JSON 对象并原样返回。"),
                HumanMessage(content=json.dumps({"target": COMPANY, "aspects": ["注册资本"]}, ensure_ascii=False)),
            ]
        )
        metadata = getattr(raw, "usage_metadata", None)
        assert isinstance(metadata, dict), metadata
        input_tokens = int(metadata.get("input_tokens") or 0)
        output_tokens = int(metadata.get("output_tokens") or 0)
        assert input_tokens > 0 and output_tokens > 0, metadata  # the endpoint does report them

        pipeline.llm._response(raw)  # the one path that feeds Usage.add
        snapshot = pipeline.llm.usage_snapshot()
        assert snapshot["calls"] == 1
        assert snapshot["total_tokens"] == input_tokens + output_tokens
        assert snapshot["prompt_tokens"] == 0
        assert snapshot["completion_tokens"] == 0
    finally:
        await pipeline.client.close()
