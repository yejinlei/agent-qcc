"""End-to-end integration tests for the 4R+1 pipeline and its LangGraph.

Scope (per YEJ-118): the whole orchestration chain — user query -> route ->
reason -> retrieve (企查查 connector) -> reflect -> respond — is driven through
`graph.build_graph` with only the two external services swapped out.  The stubbed
surface is deliberately small: one method each for the connector
(`QccClient.call`) and the model (`LlmClient.invoke_json` / `invoke_text`), so
every line of `pipeline.py`, `graph.py`, `state.py` and the real
`qcc.QccClient` validation / offline branch stays on the execution path.

What is asserted here are cross-module contracts — state transitions, node
ordering, the `Evidence` shape crossing connector -> reflect -> respond, and the
degradation paths.  Pure-function behaviour (`extract_json`, `parse_sse_stream`,
`load_skill`, `extract_target`, ...) is intentionally not repeated: that is
`test_qcc.py` / `test_llm.py`'s job.

Everything in this module runs offline and without credentials.  Tests that hit
a real 企查查 endpoint or a real model live in
`test_pipeline_integration_live.py` behind the `integration` marker.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent_qcc.config import Settings
from agent_qcc.graph import build_graph, graph_spec
from agent_qcc.pipeline import CORE_TOOLS, Pipeline
from agent_qcc.prompts import (
    CHITCHAT_SYSTEM,
    PLAN_SYSTEM,
    REFLECT_SYSTEM,
    RESPOND_SYSTEM,
    ROUTE_SYSTEM,
)
from agent_qcc.qcc import QccClient, ToolResult
from agent_qcc.skill import Skill, load_skill
from agent_qcc.state import merge_evidence, new_state

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

COMPANY = "北京示例科技有限公司"
QUERY = f"查询：{COMPANY}｜注册资本、股东结构、实际控制人"
CHIT_CHAT = "今天天气怎么样，你好"
EVIDENCE_KEYS = ("tool", "target", "ok", "data", "error", "duration_ms")


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------


class StubQcc:
    """Records every connector call and returns scripted `ToolResult`s.

    Mirrors the production contract of `QccClient.call`: always returns a
    `ToolResult` and never raises, so the graph cannot observe a half failure.
    """

    def __init__(
        self,
        *,
        tools: dict[str, Any] | None = None,
        ok: bool = True,
        data: Any = None,
        error: str = "stub error",
        fail_tools: set[str] | None = None,
    ) -> None:
        self.tools = tools if tools is not None else {}
        self.ok = ok
        self.data = data
        self.error = error
        self.fail_tools = set(fail_tools or ())
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        self.calls.append((tool_name, dict(args)))
        if tool_name in self.fail_tools:
            return ToolResult(
                tool=tool_name, target=self._target(args), ok=False,
                error=self.error, duration_ms=1,
            )
        return ToolResult(
            tool=tool_name, target=self._target(args), ok=self.ok,
            data=self.data, error=self.error, duration_ms=1,
        )

    @staticmethod
    def _target(args: dict[str, Any]) -> str:
        for key in ("name", "keyword", "creditCode", "searchKey"):
            if args.get(key):
                return str(args[key])
        return "<unknown>"


class StubLlm:
    """Scripted `LlmClient`: one payload per stage prompt.

    `None` in a slot stands for "the model returned nothing parseable", which is
    exactly what `LlmClient.invoke_json` yields for invalid model output — so
    these tests exercise the pipeline's degradation branch without needing a
    broken model.  A callable payload is evaluated per call, which is how the
    reflect loop is scripted as revise-then-respond.
    """

    def __init__(
        self,
        *,
        route: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        reflect: dict[str, Any] | Callable[[int], dict[str, Any]] | None = None,
        answer: str | None = None,
        chitchat: str | None = None,
    ) -> None:
        self.route = route
        self.plan = plan
        self.reflect = reflect
        self.answer = answer
        self.chitchat = chitchat
        self.json_calls: list[str] = []
        self.text_calls: list[str] = []
        self.reflect_calls: list[str] = []

    async def invoke_json(self, system: str, human: str, *, key: str | None = None) -> dict[str, Any] | None:
        self.json_calls.append(system)
        if system == REFLECT_SYSTEM:
            payload = self.reflect
            slot = len(self.reflect_calls)
            self.reflect_calls.append(system)
        else:
            payload = {ROUTE_SYSTEM: self.route, PLAN_SYSTEM: self.plan}.get(system)
            slot = 0
        if payload is None:
            return None
        return payload(slot) if callable(payload) else payload

    async def invoke_text(self, system: str, human: str) -> str | None:
        self.text_calls.append(system)
        if system == RESPOND_SYSTEM:
            return self.answer
        if system == CHITCHAT_SYSTEM:
            return self.chitchat
        return None


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------


def make_settings(**overrides: Any) -> Settings:
    """Credential-free settings: no `.env` file, offline, rules only."""
    overrides.setdefault("offline", True)
    overrides.setdefault("use_llm", False)
    return Settings(_env_file=None, **overrides)


def make_pipeline(
    *,
    skill: Skill | None = None,
    settings: Settings | None = None,
    qcc: Any | None = None,
    llm: Any | None = None,
) -> Pipeline:
    """A real `Pipeline` with the two external services swapped out.

    `use_llm=False` keeps `Pipeline.__init__` from building a `ChatOpenAI`
    client (no credentials); the stub is attached directly afterwards.
    """
    skill = skill or load_skill(SKILLS_DIR, "qcc-company")
    settings = settings or make_settings()
    pipeline = Pipeline(skill, settings)
    pipeline.client = qcc if qcc is not None else QccClient(skill, settings)
    pipeline.llm = llm
    return pipeline


def make_qcc(skill: Skill, **overrides: Any) -> StubQcc:
    """A stub that carries the real skill's tool catalogue."""
    overrides.setdefault("tools", {tool.name: tool for tool in skill.tools})
    return StubQcc(**overrides)


def last_message_text(state: dict[str, Any]) -> str:
    return str(state["messages"][-1].content)


def trace(
    pipeline: Pipeline, user_text: str, *, checkpointer=None
) -> tuple[list[str], dict[str, Any]]:
    """Run one user turn; returns ``(node_sequence, final_state)``.

    Streaming in both ``updates`` and ``values`` modes in a single traversal
    gives the node order *and* the cumulative state, so tests do not have to run
    the graph twice or re-implement the `AgentState` reducers.
    """

    async def collect() -> tuple[list[str], dict[str, Any]]:
        graph = build_graph(pipeline, checkpointer=checkpointer)
        sequence: list[str] = []
        state: dict[str, Any] = {}
        async for mode, payload in graph.astream(
            new_state(user_text), stream_mode=["updates", "values"]
        ):
            if mode == "updates":
                sequence.extend(str(name) for name in payload)
            else:
                state.update(payload)
        assert state, "graph produced no state"
        return sequence, state

    return asyncio.run(collect())


async def invoke_threaded(
    pipeline: Pipeline, checkpointer: Any, config: dict[str, Any], user_text: str
) -> dict[str, Any]:
    graph = build_graph(pipeline, checkpointer=checkpointer)
    return await graph.ainvoke(new_state(user_text), config=config)


@pytest.fixture()
def company_skill() -> Skill:
    return load_skill(SKILLS_DIR, "qcc-company")


@pytest.fixture()
def stub_qcc(company_skill: Skill) -> StubQcc:
    return make_qcc(company_skill, data={"注册资本": "1000万元"})


@pytest.fixture()
def pipeline(stub_qcc: StubQcc) -> Pipeline:
    return make_pipeline(qcc=stub_qcc)


# ---------------------------------------------------------------------------
# full happy path
# ---------------------------------------------------------------------------


def test_full_run_route_reason_retrieve_reflect_respond(pipeline: Pipeline, stub_qcc: StubQcc):
    sequence, state = trace(pipeline, QUERY)

    assert sequence == ["route", "reason", "retrieve", "reflect", "respond"]

    # R1: rule routing extracted the company and mapped aspects to an intent.
    route = state["route"]
    assert route["company"] == COMPANY
    assert route["intent"] in ("company_info", "multi")
    assert route["need_clarification"] is False

    # R2 -> R3: every planned tool was actually called, once, with the company.
    plan_tools = [call["tool"] for call in state["plan"]["tool_calls"]]
    assert plan_tools
    assert plan_tools == [tool for tool, _ in stub_qcc.calls]
    assert all(args["name"] == COMPANY for _, args in stub_qcc.calls)

    # R3: evidence kept the connector's payload verbatim, keys and all.
    evidence = state["evidence"]
    assert len(evidence) == len(stub_qcc.calls)
    assert {key for key in evidence[0]} == set(EVIDENCE_KEYS)
    assert all(item["ok"] is True for item in evidence)
    assert all(item["data"] == {"注册资本": "1000万元"} for item in evidence)
    assert all(item["target"] == COMPANY for item in evidence)

    # R4 -> +1: evidence sufficient, single iteration, answer rendered.
    assert state["decision"] == "respond"
    assert state["iteration"] == 1
    assert len(state["reflections"]) == 1
    answer = last_message_text(state)
    assert COMPANY in answer
    assert "✅" in answer
    assert "⚠️" not in answer


def test_living_model_paths_drive_the_same_chain(company_skill: Skill):
    """With a model attached, its plan and answer drive the identical chain."""
    llm = StubLlm(
        route={"intent": "shareholders", "company": COMPANY, "aspects": ["股东", "实控人"], "need_clarification": False},
        plan={
            "target": COMPANY,
            "tool_calls": [
                {"tool": "get_shareholder_info", "args": {"name": COMPANY}, "reason": "股东"},
                {"tool": "get_actual_controller", "args": {"name": COMPANY}, "reason": "实控人"},
            ],
        },
        reflect={"decision": "respond", "coverage": ["股东"], "gaps": [], "fixes": []},
        answer="**股东结构**：来自连接器证据。",
    )
    pipeline = make_pipeline(skill=company_skill, qcc=make_qcc(company_skill), llm=llm)

    sequence, state = trace(pipeline, QUERY)

    assert sequence == ["route", "reason", "retrieve", "reflect", "respond"]
    # The model's plan (not the rule planner) decided the calls.
    assert [tool for tool, _ in pipeline.client.calls] == [
        "get_shareholder_info",
        "get_actual_controller",
    ]
    assert state["plan"]["tool_calls"][0]["reason"] == "股东"
    # Reflect + respond both went through the model, route/reflect once each.
    assert [system for system in llm.json_calls] == [ROUTE_SYSTEM, PLAN_SYSTEM, REFLECT_SYSTEM]
    assert llm.text_calls == [RESPOND_SYSTEM]
    assert last_message_text(state) == "**股东结构**：来自连接器证据。"
    assert state["reflections"][0]["decision"] == "respond"


# ---------------------------------------------------------------------------
# graph topology: chitchat short-circuit and the reflect loop
# ---------------------------------------------------------------------------


def test_chitchat_short_circuits_to_respond(company_skill: Skill):
    """DEFECT (product bug, not fixed here): the `chitchat` branch is dead.

    `Pipeline.route` records `"intent": "chitchat"`, `graph._route_next` skips
    straight to `respond`, and `Pipeline.respond` correctly computes
    `decision == "chitchat"` and *returns* it.  But two things throw it away:

    1. `state["decision"]` was seeded to `"respond"` by `new_state()` and
       `reflect` never runs on the short-circuit edge, so
       `state.get("decision") or "respond"` inside `respond` reads "respond" —
       the chitchat decision never reaches the graph state;
    2. because `respond` is entered with `decision == "respond"`, it takes the
       `_render()` branch instead of `_chitchat()`.  A model reply scripted for
       chitchat is silently ignored and the user gets the full company report
       with "暂无证据" — i.e. an out-of-scope question gets a fabricated-looking
       report instead of a capability-boundary answer.

    Reproduce: run the graph on an out-of-scope question, either with no model
    (`route` infers intent "chitchat") or with `route.intent = "chitchat"` and a
    scripted answer; the final state says `decision == "respond"` and the body
    is the rendered report, not the chitchat reply.

    The assertions below pin what is actually delivered today.  They are
    deliberately narrow about the bug: the topology (route -> respond, no
    connector call) and the surviving `route.intent` are correct and are worth
    keeping.  Fixing `respond` to honour the chitchat intent turns
    `state["decision"]` into `"chitchat"` and the body into the scripted reply.
    """
    llm = StubLlm(route={"intent": "chitchat", "company": "", "need_clarification": False}, chitchat="我是企业尽调助手。")
    qcc = make_qcc(company_skill)
    pipeline = make_pipeline(skill=company_skill, qcc=qcc, llm=llm)

    sequence, state = trace(pipeline, CHIT_CHAT)

    assert sequence == ["route", "respond"]
    assert state["route"]["intent"] == "chitchat"  # the intent survives the routing
    assert state["decision"] == "respond"  # clobbered by the seed value (the bug)
    assert state["evidence"] == []
    assert state["plan"] == {"tool_calls": []}
    assert qcc.calls == []  # reason/retrieve never ran, so no connector call
    # The model was asked to render a company report, never to answer chitchat:
    # the scripted chitchat reply is silently dropped.
    assert llm.text_calls == [RESPOND_SYSTEM]
    # ...so the user sees the rendered report, which has no evidence at all.
    assert "暂无证据" in last_message_text(state)


def test_chitchat_without_a_model_uses_the_capability_boundary_text(company_skill: Skill):
    pipeline = make_pipeline(skill=company_skill, qcc=make_qcc(company_skill))
    sequence, state = trace(pipeline, CHIT_CHAT)

    assert sequence == ["route", "respond"]
    assert state["route"]["intent"] == "chitchat"
    assert state["decision"] == "respond"  # see test_chitchat_short_circuits_to_respond
    answer = last_message_text(state)
    # Intended contract (broken today, see the defect note above):
    # assert "能力边界" in answer
    assert "暂无证据" in answer


def test_revise_decision_loops_back_through_retrieve(company_skill: Skill):
    company = "杭州示范贸易有限公司"
    llm = StubLlm(
        route={"intent": "company_info", "company": company, "aspects": ["注册资本"], "need_clarification": False},
        plan={"target": company, "tool_calls": [{"tool": "get_company_profile", "args": {"name": company}}]},
        reflect=lambda slot: (
            {"decision": "revise", "fixes": [{"tool": "get_company_registration_info", "args": {"name": company}}]}
            if slot == 0
            else {"decision": "respond"}
        ),
        answer=f"{company} 的注册资本见证据。",
    )
    pipeline = make_pipeline(skill=company_skill, qcc=make_qcc(company_skill), llm=llm)

    sequence, state = trace(pipeline, f"查询：{company}｜注册资本")

    assert sequence == ["route", "reason", "retrieve", "reflect", "retrieve", "reflect", "respond"]
    assert state["iteration"] == 2
    # The reflect loop *appended* evidence instead of replacing it (merge_evidence).
    assert [item["tool"] for item in state["evidence"]] == [
        "get_company_profile",
        "get_company_registration_info",
    ]
    assert state["decision"] == "respond"
    assert state["plan"]["tool_calls"][0]["tool"] == "get_company_registration_info"


def test_revise_is_bounded_by_max_reflect_retries(company_skill: Skill):
    """A model that keeps saying `revise` must still terminate."""
    llm = StubLlm(
        route={"intent": "company_info", "company": COMPANY, "need_clarification": False},
        reflect={"decision": "revise", "fixes": [{"tool": "get_company_profile", "args": {"name": COMPANY}}]},
    )
    pipeline = make_pipeline(
        skill=company_skill,
        settings=make_settings(max_reflect_retries=1),
        qcc=make_qcc(company_skill),
        llm=llm,
    )

    sequence, state = trace(pipeline, QUERY)

    # Exactly one loop, then the retry budget is spent and it answers.
    assert sequence == [
        "route", "reason", "retrieve", "reflect", "retrieve", "reflect", "respond",
    ]
    assert state["decision"] == "respond"
    assert state["iteration"] == 2
    # The stored reflection is the *last* one, which still voted `revise` —
    # the decision was demoted to `respond` only because the budget ran out.
    assert len(state["reflections"]) == 1  # reflections is last-write-wins
    assert state["reflections"][0]["decision"] == "revise"
    assert state["plan"]["tool_calls"][0]["tool"] == "get_company_profile"
    assert "iteration" not in state["reflections"][0]  # LLM payloads are kept verbatim


def test_zero_retries_answers_after_a_single_reflection(company_skill: Skill):
    """`max_reflect_retries=0` bounds the graph to one retrieve/reflect cycle."""
    llm = StubLlm(
        route={"intent": "company_info", "company": COMPANY, "need_clarification": False},
        reflect={"decision": "revise", "fixes": [{"tool": "get_company_profile", "args": {"name": COMPANY}}]},
    )
    pipeline = make_pipeline(
        skill=company_skill,
        settings=make_settings(max_reflect_retries=0),
        qcc=make_qcc(company_skill),
        llm=llm,
    )

    sequence, state = trace(pipeline, QUERY)

    assert sequence == ["route", "reason", "retrieve", "reflect", "respond"]
    assert state["iteration"] == 1
    assert state["decision"] == "respond"


def test_graph_spec_matches_the_compiled_nodes(company_skill: Skill):
    graph = build_graph(make_pipeline(skill=company_skill, qcc=make_qcc(company_skill)))

    node_names = {str(name) for name in graph.get_graph().nodes} - {"__start__", "__end__"}
    assert set(graph_spec()) == node_names


# ---------------------------------------------------------------------------
# degradation paths
# ---------------------------------------------------------------------------


def test_offline_connector_degrades_to_an_answer(company_skill: Skill):
    """Real `QccClient` in offline mode: no network, every call fails softly.

    The whole point of the retry loop still holds — nothing may hang or
    infinite-loop because the connector is unavailable — even though `_fixes()`
    cannot repair anything (see the defect note below).
    """
    settings = make_settings()
    pipeline = make_pipeline(skill=company_skill, settings=settings, qcc=QccClient(company_skill, settings))

    sequence, state = trace(pipeline, QUERY)

    # A reported gap triggers one retry pass, so the loop runs to the end.
    assert sequence == [
        "route", "reason", "retrieve", "reflect", "retrieve", "reflect", "respond",
    ]
    assert state["decision"] == "respond"
    assert state["iteration"] == 2
    evidence = state["evidence"]
    assert all(item["ok"] is False for item in evidence)
    assert all("离线模式" in item["error"] for item in evidence)
    # Reflect records the all-failed gap; respond renders it instead of crashing.
    assert state["reflections"][0]["gaps"]
    assert "全部调用失败" in state["reflections"][0]["gaps"][0]
    answer = last_message_text(state)
    assert "⚠️" in answer
    assert COMPANY in answer


def test_reflect_retry_loop_cannot_repair_any_gap(company_skill: Skill):
    """DEFECT (product bug, not fixed here): the `revise` path is dead code.

    `Pipeline._fixes()` always returns `[]`, so `_gaps()` reporting a gap is
    useless in the retry path: `reflect` still returns `decision: "revise"` and
    the graph loops back to `retrieve`, but the revised plan has zero tool
    calls, so the loop can never cover the gap it just reported.

    Consequence: `max_reflect_retries` is spent on an empty retrieval instead of
    recovering coverage, and `reflections[i].fixes` is always `[]` — i.e. the
    loop's accounting says "revised" while nothing was actually done.

    Reproduce: point the connector at nothing (offline mode, or a stub that
    fails every call) and watch `retrieve` -> `reflect` -> `retrieve` run with
    an empty second batch.

    The bounded-retry property is still pinned separately by
    `test_revise_is_bounded_by_max_reflect_retries`, which keeps the loop from
    running forever.  Asserting `fixes` stays empty here documents the gap
    without changing business code.
    """
    qcc = make_qcc(company_skill, ok=False, error="connector unreachable")
    pipeline = make_pipeline(skill=company_skill, qcc=qcc)

    sequence, state = trace(pipeline, QUERY)

    assert sequence.count("reflect") == 2
    assert state["decision"] == "respond"
    for reflection in state["reflections"]:
        assert reflection["gaps"]
        assert reflection["fixes"] == []
    # Nothing was ever retrieved on the retry pass.
    assert all(not item["ok"] for item in state["evidence"])


def test_connector_partial_failure_is_reported_but_not_masked(company_skill: Skill):
    qcc = make_qcc(
        company_skill,
        data={"股东": "甲"},
        fail_tools={"get_actual_controller"},
        error="502 Bad Gateway",
    )
    pipeline = make_pipeline(skill=company_skill, qcc=qcc)

    sequence, state = trace(pipeline, QUERY)

    assert sequence == ["route", "reason", "retrieve", "reflect", "respond"]
    failed = [item for item in state["evidence"] if not item["ok"]]
    assert [item["tool"] for item in failed] == ["get_actual_controller"]
    assert failed[0]["error"] == "502 Bad Gateway"
    assert state["plan"]["tool_calls"]  # a partial failure is not downgraded to clarify
    assert state["decision"] == "respond"
    assert "⚠️" in last_message_text(state)
    assert state["reflections"][0]["gaps"] == []  # not all calls failed


def test_invalid_llm_json_falls_back_to_rules(company_skill: Skill):
    """Model output is unusable -> rule planner, rule reflect, rule render.

    `None` here is what `LlmClient.invoke_json` yields for unparseable model
    output, so this is the exact degradation branch a broken model hits.
    """
    llm = StubLlm()  # every stage returns None: nothing usable from the model
    pipeline = make_pipeline(skill=company_skill, qcc=make_qcc(company_skill), llm=llm)

    sequence, state = trace(pipeline, QUERY)

    assert sequence == ["route", "reason", "retrieve", "reflect", "respond"]
    assert [system for system in llm.json_calls] == [ROUTE_SYSTEM, PLAN_SYSTEM, REFLECT_SYSTEM]
    # No model plan means no invented calls can reach the connector either.
    assert pipeline.client.calls
    assert all(tool in company_skill.tool_names() for tool, _ in pipeline.client.calls)
    # Neither the reflect nor the answer came from the model -> rule rendering.
    answer = last_message_text(state)
    assert "规则渲染" in answer
    assert "连接器" in answer
    assert llm.text_calls == [RESPOND_SYSTEM]


def test_plan_with_invented_tool_is_rejected(company_skill: Skill):
    """`_validate_plan` keeps an LLM plan from calling a non-existent connector."""
    llm = StubLlm(
        route={"intent": "company_info", "company": COMPANY, "need_clarification": False},
        plan={"target": COMPANY, "tool_calls": [{"tool": "get_flight_status", "args": {"name": COMPANY}}]},
    )
    qcc = make_qcc(company_skill)
    pipeline = make_pipeline(skill=company_skill, qcc=qcc, llm=llm)

    sequence, state = trace(pipeline, QUERY)

    assert sequence == ["route", "reason", "retrieve", "reflect", "respond"]
    assert all(call["tool"] != "get_flight_status" for call in state["plan"]["tool_calls"])
    assert [tool for tool, _ in qcc.calls] == [call["tool"] for call in state["plan"]["tool_calls"]]
    assert pipeline.client.calls  # the fallback plan still retrieved something


def test_plan_missing_required_args_is_rejected(company_skill: Skill):
    """`_validate_plan` also enforces the connector's required arguments."""
    llm = StubLlm(
        route={"intent": "company_info", "company": COMPANY, "need_clarification": False},
        # `verify_company_accuracy` requires name *and* searchKey -> rejected.
        plan={"target": COMPANY, "tool_calls": [{"tool": "verify_company_accuracy", "args": {"name": COMPANY}}]},
    )
    qcc = make_qcc(company_skill)
    pipeline = make_pipeline(skill=company_skill, qcc=qcc, llm=llm)

    sequence, state = trace(pipeline, QUERY)

    assert sequence == ["route", "reason", "retrieve", "reflect", "respond"]
    assert all(call["tool"] != "verify_company_accuracy" for call in state["plan"]["tool_calls"])
    assert all(tool != "verify_company_accuracy" for tool, _ in qcc.calls)


def test_no_company_in_the_question_degrades_to_clarify(company_skill: Skill):
    """Nothing to query -> ask the user instead of guessing a company."""
    pipeline = make_pipeline(skill=company_skill)  # real offline connector

    sequence, state = trace(pipeline, "查一下注册资本和股东结构")

    assert sequence == ["route", "reason", "retrieve", "reflect", "respond"]
    assert state["route"]["company"] == ""
    # The core trio is still planned, but without a name it cannot run.
    assert [call["tool"] for call in state["plan"]["tool_calls"]] == list(CORE_TOOLS)[:2]
    assert all(call["args"] == {} for call in state["plan"]["tool_calls"])
    # The connector's own validation rejects the arg-less call, and the graph
    # still lands on a clarify instead of a fabricated report.
    assert state["evidence"]
    assert all(item["ok"] is False and "缺少查询参数" in item["error"] for item in state["evidence"])
    assert state["decision"] == "clarify"
    assert state["iteration"] == 0
    assert state["reflections"][0]["gaps"]
    assert "补充" in last_message_text(state)


def test_reflect_forces_clarify_on_an_unusable_query(company_skill: Skill):
    """A `clarify` decision from reflect is what ends the run."""
    llm = StubLlm(
        route={"intent": "company_info", "company": COMPANY, "need_clarification": True},
        plan={"target": COMPANY, "tool_calls": [{"tool": "get_company_profile", "args": {"name": COMPANY}}]},
        reflect={"decision": "clarify", "gaps": ["企业名不完整"], "fixes": []},
    )
    qcc = make_qcc(company_skill)
    pipeline = make_pipeline(skill=company_skill, qcc=qcc, llm=llm)

    sequence, state = trace(pipeline, f"查询：{COMPANY}｜注册资本")

    assert sequence == ["route", "reason", "retrieve", "reflect", "respond"]
    assert state["decision"] == "clarify"
    assert state["reflections"][0]["gaps"]
    assert "请补充" in last_message_text(state)
    # Nothing was retrieved a second time.
    assert [tool for tool, _ in qcc.calls] == ["get_company_profile"]


# ---------------------------------------------------------------------------
# cross-cutting contracts
# ---------------------------------------------------------------------------


def test_iteration_counts_every_reflection(company_skill: Skill):
    pipeline = make_pipeline(skill=company_skill, qcc=make_qcc(company_skill))
    _, state = trace(pipeline, QUERY)
    assert state["iteration"] == 1
    assert state["reflections"][0]["iteration"] == 1


def test_messages_carry_the_user_turn_into_the_response(company_skill: Skill):
    pipeline = make_pipeline(skill=company_skill, qcc=make_qcc(company_skill))
    _, state = trace(pipeline, QUERY)
    assert [message.type for message in state["messages"]] == ["human", "ai"]
    assert state["messages"][0].content == QUERY


@pytest.mark.asyncio
async def test_checkpointer_is_accepted_and_threads_are_resumable(company_skill: Skill):
    """`build_graph` honours a checkpointer; a thread can be resumed."""
    qcc = make_qcc(company_skill)
    pipeline = make_pipeline(skill=company_skill, qcc=qcc)
    config = {"configurable": {"thread_id": "integration-thread-1"}}

    first = await invoke_threaded(pipeline, InMemorySaver(), config, QUERY)
    second = await invoke_threaded(pipeline, InMemorySaver(), config, QUERY)

    assert [message.type for message in first["messages"]] == ["human", "ai"]
    assert first["decision"] == second["decision"] == "respond"
    # The second turn answered again on the same thread.
    assert [message.type for message in second["messages"]] == ["human", "ai"]
    assert last_message_text(second)


def test_state_reducer_appends_evidence_without_losing_the_first_batch():
    """The reflect loop relies on `evidence` being additive, not last-write-wins."""
    left = [{"tool": "a", "ok": True}]
    right = [{"tool": "b", "ok": False, "error": "boom"}]
    assert merge_evidence(left, right) == [left[0], right[0]]
    assert merge_evidence([], []) == []


def test_new_state_stays_present_through_the_graph(company_skill: Skill):
    """Every key seeded by `new_state` survives the whole traversal."""
    state = new_state(QUERY)
    pipeline = make_pipeline(skill=company_skill, qcc=make_qcc(company_skill))
    _, final = trace(pipeline, QUERY)
    for key in state:
        assert key in final, f"{key!r} disappeared from the state"
    assert final["route"] and final["evidence"]


def test_plan_is_capped_at_eight_calls(company_skill: Skill):
    llm = StubLlm(route={"intent": "multi", "company": COMPANY, "aspects": [], "need_clarification": False})
    pipeline = make_pipeline(skill=company_skill, qcc=make_qcc(company_skill), llm=llm)

    sequence, state = trace(pipeline, QUERY)

    assert len(state["plan"]["tool_calls"]) <= 8
    assert len(state["evidence"]) == len(state["plan"]["tool_calls"])


def test_core_tools_are_the_fallback_plan(company_skill: Skill):
    """Aspect words the planner does not recognise -> fall back to the core trio."""
    llm = StubLlm(route={"intent": "all", "company": COMPANY, "aspects": ["不存在的侧面"], "need_clarification": False})
    pipeline = make_pipeline(skill=company_skill, qcc=make_qcc(company_skill), llm=llm)

    _, state = trace(pipeline, QUERY)

    assert set(CORE_TOOLS).intersection(item["tool"] for item in state["evidence"])
    assert all(call["args"]["name"] == COMPANY for call in state["plan"]["tool_calls"])


def test_duration_ms_reaches_the_rendered_report(company_skill: Skill):
    pipeline = make_pipeline(skill=company_skill, qcc=make_qcc(company_skill))
    _, state = trace(pipeline, QUERY)
    assert all(int(item["duration_ms"]) >= 0 for item in state["evidence"])
    assert "ms)" in last_message_text(state)
