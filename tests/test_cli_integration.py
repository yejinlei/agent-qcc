"""Integration tests for the user entry points: the ``agent.py`` factory and the CLI.

Scope (per YEJ-119): the two surfaces a human or a deployment actually touches —
``build_agent`` / ``invoke`` / ``get_graph`` (``src/agent_qcc/agent.py``) and the
Typer app (``src/agent_qcc/cli.py``, including ``serve_main``) — driven end to
end with only the two external services swapped out.  The graph is compiled from
the real nodes in ``graph.py`` against the real ``pipeline.Pipeline``, the real
``QccClient`` catalogue and the real ``load_skill`` output, so the seam is the
same small surface ``test_pipeline_integration.py`` uses: ``QccClient.call`` for
the connector and ``LlmClient.invoke_*`` for the model.  Nothing in
``agent.py`` is faked — it is exercised as shipped.

Entry-point contracts are what is asserted here: exit codes, the text a user
actually reads, what a missing skill or a missing ``langgraph`` binary looks
like, and how ``AGENT_QCC_*`` values reach the factory.  Pipeline and graph
semantics (node order, evidence shape, the reflect loop, the reducers) belong
to ``test_pipeline_integration.py`` and are deliberately not repeated.

Everything here is offline and credential-free.  Tests that need a real 企查查
token and a real model live at the bottom of this file behind the
``integration`` marker (excluded by ``addopts`` in ``pyproject.toml``) and skip
themselves when the credentials are absent, so a bare
``pytest tests/test_cli_integration.py`` cannot open a network connection.

All five product defects were fixed and every test below now asserts the
intended behaviour:

* **P1** — *fixed* (YEJ-129): ``chat`` / ``tui`` used to be ``async def`` and
  Typer never awaited them, so every invocation exited 0 without printing
  anything or doing any work.  They are plain ``def`` commands again and the
  assertions below cover the intended behaviour.  The fix also drops the
  ``state=state`` feedback in ``tui``, which had been silently rerunning every
  turn on the previous question.
* **P2** — *fixed* (YEJ-130): ``serve_main`` used to crash while building its
  own log line (``TypeError: expected str instance, OptionInfo found``) before
  ``os.execvp`` was even attempted.  It now takes plain values — each entry point
  resolves its own options and threads them in — so both ``agent-qcc serve`` and
  ``agent-qcc-serve`` build a real command line, reach the missing-``langgraph``
  branch, and the printed command is the one that is exec'd.
* **P3** — *fixed* (YEJ-131): a failing ``build_agent`` used to leave the user
  with a blank screen and exit 1.  Every entry point now reports the missing
  path and the setting that decides it, and ``skills`` prints a message instead
  of raising an unhandled ``FileNotFoundError``.
* **P4** — *fixed with P2*: the documented ``serve`` flags (``README``:
  ``agent-qcc-serve --host 127.0.0.1 --port 2024``) now work.  ``serve_command``
  has its own ``--host`` / ``--port`` and forwards the resolved values, so
  ``agent-qcc serve --host 0.0.0.0 --port 3000`` reaches ``langgraph`` unchanged
  instead of exiting 2 with ``No such option``.  YEJ-132 tracks the same fix for
  the standalone ``agent-qcc-serve`` wrapper, which was already working.
* **P5** — *fixed* (YEJ-133): ``agent.invoke`` raised ``TypeError: No synchronous
  function provided`` on langgraph 1.x, because the project's synchronous
  invocation path did not run at all while ``ainvoke`` did.  ``invoke`` now drives
  ``graph.ainvoke`` through its own event loop.

P5 and P1 had to land together: the synchronous entry point makes the CLI's
call site valid, and awaiting the command body is what makes the entry point
get called at all.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import warnings
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from typer.testing import CliRunner

import agent_qcc.agent as agent_module
import agent_qcc.cli as cli_module
from agent_qcc.agent import build_agent, get_graph, invoke
from agent_qcc.config import Settings
from agent_qcc.graph import build_graph
from agent_qcc.pipeline import Pipeline
from agent_qcc.prompts import RESPOND_SYSTEM
from agent_qcc.qcc import ToolResult, extract_target, parse_sse_stream
from agent_qcc.skill import load_skill
from agent_qcc.state import new_state

COMPANY = "北京示例科技有限公司"
QUERY = f"查询：{COMPANY}｜注册资本、股东结构、实际控制人"
ANSWER = "结论：注册资本 1000 万元。"
EVIDENCE_KEYS = ("tool", "target", "ok", "data", "error", "duration_ms")
STAGES = ("route", "reason", "retrieve", "reflect", "respond")
QCC_TOKEN_ENV = "AGENT_QCC_QCC_AUTH_TOKEN"
OPENAI_KEY_ENV = "AGENT_QCC_OPENAI_API_KEY"
#: The argv ``serve`` hands to ``os.execvp`` when nothing is overridden — both
#: entry points must produce exactly this, which is what the acceptance criteria
#: ask for.
EXPECTED_SERVE_ARGV = [
    "langgraph",
    "serve",
    "src/agent_qcc/agent.py:graph",
    "--host",
    "127.0.0.1",
    "--port",
    "2024",
]


# ---------------------------------------------------------------------------
# stubs — the same small surface the pipeline tests use
# ---------------------------------------------------------------------------


class StubQcc:
    """Records connector calls and returns a scripted ``ToolResult``.

    Mirrors ``QccClient.call``: always returns a result and never raises, so the
    entry points cannot observe a half failure.
    """

    def __init__(
        self,
        tools: dict[str, Any],
        *,
        ok: bool = False,
        data: Any = None,
        error: str = "stub 连接器错误",
    ) -> None:
        self.tools = tools
        self.ok = ok
        self.data = data
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        self.calls.append((tool_name, dict(args)))
        return ToolResult(
            tool=tool_name,
            target=self._target(args),
            ok=self.ok,
            data=self.data,
            error=self.error,
            duration_ms=1,
        )

    @staticmethod
    def _target(args: dict[str, Any]) -> str:
        return extract_target(args)


class StubLlm:
    """Scripted model: one answer for ``RESPOND_SYSTEM``, no plan override."""

    def __init__(self, answer: str = ANSWER) -> None:
        self.answer = answer
        self.calls: list[str] = []

    async def invoke_json(self, system: str, human: str, *, key: str | None = None) -> dict[str, Any] | None:
        self.calls.append(system)
        return None  # keep every stage on its rule-based fallback

    async def invoke_text(self, system: str, human: str) -> str | None:
        self.calls.append(system)
        return self.answer if system == RESPOND_SYSTEM else None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_settings(**overrides: Any) -> Settings:
    """Credential-free settings straight from the declared defaults."""
    overrides.setdefault("offline", True)
    overrides.setdefault("use_llm", False)
    return Settings(_env_file=None, **overrides)


def wire(real_tuple: tuple, *, ok: bool = False, llm: Any | None = None) -> tuple:
    """Re-point an existing factory tuple's connector/model onto a stub.

    ``build_agent`` itself is untouched — skill loading, ``Pipeline``
    construction, checkpointer creation and graph compilation all ran for real.
    """
    graph, checkpointer, pipeline, skill, settings = real_tuple
    pipeline.client = StubQcc({tool.name: tool for tool in skill.tools}, ok=ok)
    if llm is not None:
        pipeline.llm = llm
    return graph, checkpointer, pipeline, skill, settings


def patch_build_agent(monkeypatch: pytest.MonkeyPatch, factory: tuple) -> list[dict[str, Any]]:
    """Replace the CLI's deferred ``from .agent import build_agent`` with a stub."""
    seen: list[dict[str, Any]] = []

    def fake_build_agent(settings=None):
        seen.append({"settings": settings})
        return factory

    monkeypatch.setattr(agent_module, "build_agent", fake_build_agent)
    return seen


@pytest.fixture()
def factory_tuple(monkeypatch: pytest.MonkeyPatch) -> tuple:
    """A real ``build_agent()`` result with only the connector stubbed out."""
    monkeypatch.delenv("AGENT_QCC_OPENAI_API_KEY", raising=False)
    graph, checkpointer, pipeline, skill, settings = build_agent(make_settings())
    assert settings.offline is True and settings.use_llm is False
    pipeline.client = StubQcc({tool.name: tool for tool in skill.tools}, ok=False)
    return graph, checkpointer, pipeline, skill, settings


@pytest.fixture()
def captured_console(monkeypatch: pytest.MonkeyPatch) -> tuple[str, Any]:
    """Everything a real ``rich.Console`` would print, with no ANSI escapes.

    ``cli.py`` builds ``rich.Console()`` inside every render helper, which reads
    the actual terminal — on Windows that mangles CJK and control codes.  Pointing
    the ``rich.console`` module at a recording console keeps the call sites
    byte-identical.

    The reader is ``export_text(clear=False)``: rich's default clears the
    recording buffer on the first read, so a test that asserts the banner before
    the report — or reads the transcript twice — would see ``""`` from the second
    read on.  Nothing in production depends on the buffer being drained.
    """
    import rich.console as rich_console_module

    console = rich_console_module.Console(
        record=True,
        no_color=True,
        width=120,
        force_terminal=False,
        legacy_windows=False,
    )
    monkeypatch.setattr(rich_console_module, "Console", lambda *args, **kwargs: console)
    return lambda: console.export_text(clear=False), console


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# the factory: build_agent / invoke / get_graph
# ---------------------------------------------------------------------------


def test_build_agent_returns_a_compiled_graph_with_a_wired_checkpointer(factory_tuple):
    graph, checkpointer, pipeline, skill, settings = factory_tuple

    assert isinstance(checkpointer, InMemorySaver)
    assert checkpointer is graph.checkpointer  # this is what makes threads resumable
    assert pipeline.skill is skill
    assert pipeline.settings is settings
    assert skill.transport.get("type") == "streamableHttp"
    # the compiled graph carries exactly the documented 4R+1 topology
    topology = graph.get_graph().to_json()
    nodes = {str(node.get("id")) for node in topology["nodes"]}
    assert set(STAGES) <= nodes
    edges = {
        (str(edge.get("source")), str(edge.get("target"))) for edge in topology["edges"]
    }
    assert ("route", "reason") in edges
    assert ("reason", "retrieve") in edges
    assert ("retrieve", "reflect") in edges
    assert ("reflect", "retrieve") in edges  # the bounded revise loop
    assert ("reflect", "respond") in edges
    assert ("respond", "__end__") in edges


def test_build_agent_without_arguments_still_works_offline(monkeypatch):
    """No explicit ``Settings``: the factory is usable with no credentials at all."""
    monkeypatch.delenv("AGENT_QCC_OPENAI_API_KEY", raising=False)
    graph, checkpointer, pipeline, _skill, settings = build_agent()

    assert settings.offline is True
    assert checkpointer is graph.checkpointer
    assert pipeline.client.tools, "the skill's real tool catalogue must be loaded"


def test_build_agent_with_explicit_settings_is_the_single_source_of_truth():
    settings = make_settings(model_name="test-model", max_reflect_retries=3)
    graph, checkpointer, pipeline, _skill, returned = build_agent(settings)

    assert returned is settings
    assert pipeline.settings is settings
    assert settings.model_name == "test-model"
    assert checkpointer is graph.checkpointer


@pytest.mark.asyncio
async def test_async_invocation_runs_one_turn_end_to_end(factory_tuple):
    """One turn through the compiled graph — the path a REST client takes."""
    graph, _, _, _, _ = wire(factory_tuple, ok=False)
    state = await graph.ainvoke(new_state(QUERY), config={"configurable": {"thread_id": "factory-t1"}})

    assert isinstance(state, dict)
    assert set(EVIDENCE_KEYS) <= set(state["evidence"][0])
    assert len(state["messages"]) == 2 and isinstance(state["messages"][-1], AIMessage)
    assert "编排结果（规则渲染）" in state["messages"][-1].content
    assert "全部调用失败" in state["messages"][-1].content  # the failure is surfaced
    assert "缺口" in state["messages"][-1].content
    assert state["decision"] == "respond"
    assert state["iteration"] == 2  # one bounded revise attempt, then it gives up
    assert state["plan"]["target"] == COMPANY


@pytest.mark.asyncio
async def test_async_invocation_runs_two_turns_on_one_thread(factory_tuple):
    graph, _, _, _, _ = wire(factory_tuple, ok=False)
    config = {"configurable": {"thread_id": "factory-t2"}}
    first = await graph.ainvoke(new_state(QUERY), config=config)
    assert len(first["messages"]) == 2  # one turn lands two messages
    second = await graph.ainvoke(new_state("继续查一下联系方式"), config=config)

    assert len(second["messages"]) == 4
    assert [str(m.content) for m in second["messages"] if m.type == "human"] == [
        QUERY,
        "继续查一下联系方式",
    ]


def test_get_graph_is_a_compile_and_invoke_callable(monkeypatch):
    """``langgraph serve`` imports ``agent_qcc.agent.get_graph``."""
    monkeypatch.delenv("AGENT_QCC_OPENAI_API_KEY", raising=False)
    graph = get_graph()

    assert graph.checkpointer is not None
    assert isinstance(graph.checkpointer, InMemorySaver)
    assert graph is not agent_module.graph, "each call builds a fresh graph"


def test_module_level_graph_is_built_at_import_time(monkeypatch):
    """``agent_qcc.agent.graph`` is what ``langgraph serve src/agent_qcc/agent.py:graph`` loads."""
    monkeypatch.delenv("AGENT_QCC_OPENAI_API_KEY", raising=False)
    assert agent_module.graph is not None
    assert agent_module.graph.checkpointer is not None
    assert {name for name in STAGES} <= set(agent_module.graph.get_graph().nodes)


def test_cli_is_importable_without_credentials():
    """``agent_qcc.cli`` is the distribution entry point; importing must not need a key."""
    import agent_qcc.cli

    assert hasattr(agent_qcc.cli, "app")
    assert hasattr(agent_qcc.cli, "serve_main")


@pytest.mark.asyncio
async def test_synchronous_invoke_runs_one_turn_on_an_async_only_graph(factory_tuple):
    """P5 (fixed) — ``agent.invoke`` is the CLI's entry point and must answer.

    The pipeline stages are coroutines, so a synchronous ``graph.invoke`` raises
    ``TypeError: No synchronous function provided`` on langgraph 1.x.  The fix
    drives ``graph.ainvoke`` through an event loop of its own, which is what makes
    the CLI's unchanged ``asyncio.to_thread(invoke, ...)`` call site valid again —
    that coupling is why P5 had to land before P1 could be fixed.
    """
    graph, _, _, _, _ = wire(factory_tuple, ok=False)
    state = await asyncio.to_thread(invoke, graph, QUERY, thread_id="fixed-sync")

    assert isinstance(state, dict)
    assert state["plan"]["target"] == COMPANY  # routing + planning really ran
    assert set(EVIDENCE_KEYS) <= set(state["evidence"][0])
    assert len(state["messages"]) == 2 and isinstance(state["messages"][-1], AIMessage)
    assert "编排结果（规则渲染）" in state["messages"][-1].content
    assert "全部调用失败" in state["messages"][-1].content  # the failure is surfaced
    assert state["decision"] == "respond"

    # a second turn on the same thread reads the checkpoint the first one wrote:
    # the synchronous entry point shares the checkpointer, so history is resumable
    second = await asyncio.to_thread(invoke, graph, "继续查一下联系方式", thread_id="fixed-sync")

    assert len(second["messages"]) == 4
    assert [str(m.content) for m in second["messages"] if m.type == "human"] == [
        QUERY,
        "继续查一下联系方式",
    ]


@pytest.mark.asyncio
async def test_synchronous_invoke_rejects_a_loop_that_is_already_running(factory_tuple):
    """``invoke`` runs its own event loop, so it cannot nest inside one.

    Calling it from inside a running loop raises instead of deadlocking — the
    async callers must keep using ``graph.ainvoke`` directly.
    """
    graph, _, _, _, _ = wire(factory_tuple, ok=False)
    # the rejected ``asyncio.run`` fails fast, so the inner ``ainvoke`` coroutine is
    # never scheduled and is collected unawaited — swallow that warning, since the
    # assertion is about the early failure, not about the unreferenced coroutine
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(RuntimeError, match="asyncio.run\\(\\) cannot be called"):
            asyncio.run(invoke(graph, QUERY, thread_id="nested-loop"))


@pytest.mark.asyncio
async def test_synchronous_invoke_ignores_the_question_when_state_is_passed_back(
    factory_tuple,
):
    """The ``state=state`` form ``tui`` uses today silently drops the new question.

    A previous state fed back as input is not a continuation: ``route`` reads the
    last ``HumanMessage`` from ``state`` (``Pipeline._user_text``), and merging the
    old state against the checkpoint keeps the *old* question in front of it, so
    ``user_input`` is never seen.  The stages rerun on the previous turn —
    evidence accumulates and ``iteration`` moves on — and the answer the user sees
    is to the question they just asked, again.

    Separate from P5: it behaves exactly the same behind ``ainvoke``, so it is not
    this entry point's doing, and the real fix belongs to the CLI (stop threading
    state back; the checkpoint already is the thread's source of truth — see
    YEJ-129).  Pinned so the silent drop cannot be mistaken for working history.
    """
    graph, _, _, _, _ = wire(factory_tuple, ok=False)
    first = await asyncio.to_thread(invoke, graph, QUERY, thread_id="fixed-sync-tui")
    assert len(first["messages"]) == 2
    assert len(first["evidence"]) == 3
    assert first["iteration"] == 2  # route → retrieve → reflect → respond

    follow_up = f"再查一下{COMPANY}的联系方式"
    second = await asyncio.to_thread(
        invoke, graph, follow_up, thread_id="fixed-sync-tui", state=first
    )

    assert len(second["messages"]) == 3  # the rerun adds one more answer
    assert [m.type for m in second["messages"]] == ["human", "ai", "ai"]
    assert len(second["evidence"]) == 9  # 3 tools x 3 iterations, appended
    assert second["iteration"] == 3  # the full chain ran again
    assert second["route"] == first["route"]  # routed on the OLD question

    # and nothing on that thread can recover the lost question
    assert all(m.content != follow_up for m in second["messages"])
    third = await asyncio.to_thread(
        invoke, graph, "再查一下联系方式", thread_id="fixed-sync-tui"
    )
    assert len(third["messages"]) == 5
    assert [str(m.content) for m in third["messages"] if m.type == "human"] == [
        QUERY,
        "再查一下联系方式",
    ]  # "继续查一下联系方式" was never recorded anywhere


@pytest.mark.asyncio
async def test_synchronous_invoke_continues_the_thread_via_the_checkpoint(factory_tuple):
    """The form that works: let the checkpointer carry history, omit ``state``.

    With no ``state=`` argument each turn appends to the thread the previous one
    wrote, so multi-turn conversation through the synchronous entry point is
    correct — and this is the shape the CLI should be using.
    """
    graph, _, _, _, _ = wire(factory_tuple, ok=False)
    first = await asyncio.to_thread(invoke, graph, QUERY, thread_id="fixed-sync-resume")
    second = await asyncio.to_thread(
        invoke, graph, "继续查一下联系方式", thread_id="fixed-sync-resume"
    )

    assert len(first["messages"]) == 2
    assert len(second["messages"]) == 4
    assert [str(m.content) for m in second["messages"] if m.type == "human"] == [
        QUERY,
        "继续查一下联系方式",
    ]


# ---------------------------------------------------------------------------
# configuration injection into the factory
# ---------------------------------------------------------------------------


def test_environment_variables_reach_the_factory(tmp_path, monkeypatch):
    """``AGENT_QCC_*`` -> ``Settings`` -> ``build_agent`` -> the connector."""
    skill_dir = tmp_path / "skills"
    (skill_dir / "fake-skill").mkdir(parents=True)
    (skill_dir / "fake-skill" / "SKILL.md").write_text("---\nname: fake-skill\n---\n", encoding="utf-8")

    monkeypatch.setenv("AGENT_QCC_MODEL_NAME", "injected-model")
    monkeypatch.setenv("AGENT_QCC_OFFLINE", "false")
    monkeypatch.setenv("AGENT_QCC_SKILLS_DIR", str(skill_dir))
    monkeypatch.setenv("AGENT_QCC_QCC_SKILL_NAME", "fake-skill")
    monkeypatch.setenv("AGENT_QCC_MAX_REFLECT_RETRIES", "7")

    settings = Settings()
    assert settings.model_name == "injected-model"
    assert settings.offline is False
    assert Path(settings.skills_dir) == skill_dir
    assert settings.qcc_skill_name == "fake-skill"
    assert settings.max_reflect_retries == 7

    graph, checkpointer, pipeline, _skill, returned = build_agent(settings)
    assert returned is settings
    assert pipeline.settings is settings
    assert graph.checkpointer is checkpointer


def test_missing_skill_fails_with_a_specific_message():
    """The failure prompt is readable: it names the skill path that is missing."""
    with pytest.raises(FileNotFoundError) as excinfo:
        build_agent(make_settings(qcc_skill_name="no-such-skill"))
    assert "connector skill not found" in str(excinfo.value)
    assert "SKILL.md" in str(excinfo.value)


# ---------------------------------------------------------------------------
# CLI: Typer layer
# ---------------------------------------------------------------------------


def test_help_lists_every_command(runner):
    result = runner.invoke(cli_module.app, ["--help"])
    assert result.exit_code == 0
    for command in ("chat", "tui", "skills", "serve"):
        assert command in result.output


def test_no_arguments_shows_help_and_errors(runner):
    result = runner.invoke(cli_module.app, [])
    assert result.exit_code == 2
    assert "--help" in result.output


def test_unknown_command_errors_with_a_hint(runner):
    result = runner.invoke(cli_module.app, ["nosuchcommand"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_missing_question_is_a_usage_error(runner):
    result = runner.invoke(cli_module.app, ["chat"])
    assert result.exit_code == 2
    assert "Missing argument 'question'" in result.output


def test_unknown_option_is_a_usage_error(runner):
    result = runner.invoke(cli_module.app, ["chat", QUERY, "--bogus"])
    assert result.exit_code == 2
    assert "No such option: --bogus" in result.output


def test_chat_help_lists_the_documented_options(runner):
    result = runner.invoke(cli_module.app, ["chat", "--help"])
    assert result.exit_code == 0
    assert "--thread" in result.output
    assert "--verbose" in result.output


# ---------------------------------------------------------------------------
# CLI: skills
# ---------------------------------------------------------------------------


def test_skills_lists_the_connector_catalog(runner):
    result = runner.invoke(cli_module.app, ["skills"])
    assert result.exit_code == 0
    assert "Connector skills" in result.output
    assert "qcc-company" in result.output
    assert "streamableHttp" in result.output
    assert "https://agent.qcc.com/mcp/company/stream" in result.output


def test_skills_with_a_missing_directory_is_a_readable_error(runner, tmp_path, monkeypatch):
    """A misconfigured ``--dir`` names the path, and never raises an unhandled error.

    ``skills --dir`` takes a ``Path``, so the value is already a ``PosixPath`` /
    ``WindowsPath`` before the command body runs: the error reports the path
    Typer resolved (OS-native separators), so the assertion accepts either.
    """
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_module.app, ["skills", "--dir", "no/such/dir"])

    assert result.exit_code == 1
    assert result.exception is not None and not isinstance(result.exception, FileNotFoundError)
    # the path that was actually checked, in whichever form Typer resolved it to
    assert "no/such/dir" in result.output or "no\\such\\dir" in result.output
    assert "AGENT_QCC_SKILLS_DIR" in result.output
    assert "AGENT_QCC_QCC_SKILL_NAME" in result.output
    assert "Traceback" not in result.output


def test_skills_with_a_file_instead_of_a_directory_is_a_readable_error(runner, tmp_path):
    """``iterdir`` would raise on a file; the command reports it instead."""
    not_a_dir = tmp_path / "not-a-dir.txt"
    not_a_dir.write_text("no", encoding="utf-8")

    result = runner.invoke(cli_module.app, ["skills", "--dir", str(not_a_dir)])

    assert result.exit_code == 1
    assert result.exception is not None
    assert not_a_dir.name in result.output
    assert "AGENT_QCC_SKILLS_DIR" in result.output
    assert "AGENT_QCC_QCC_SKILL_NAME" in result.output
    assert "Traceback" not in result.output


def test_skills_with_an_empty_directory_prints_an_empty_table(runner, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(cli_module.app, ["skills", "--dir", str(empty)])
    assert result.exit_code == 0
    assert "Connector skills" in result.output


def test_skills_shows_broken_skills_without_hiding_the_rest(runner, tmp_path):
    skills = tmp_path / "skills"
    (skills / "qcc-company").mkdir(parents=True)
    (skills / "qcc-company" / "SKILL.md").write_text(
        "---\nname: qcc-company\ndisplay_name: 企查查\n---\n\n"
        "## 1. 企业画像 `get_company_profile`\n\n查询企业画像。\n",
        encoding="utf-8",
    )
    (skills / "broken").mkdir()
    (skills / "broken" / "SKILL.md").write_text("---\nname: broken\n---\n", encoding="utf-8")
    (skills / "broken" / "mcp.json").write_text("{not json", encoding="utf-8")

    result = runner.invoke(cli_module.app, ["skills", "--dir", str(skills)])
    assert result.exit_code == 0
    assert "qcc-company" in result.output
    assert "broken" in result.output
    assert "broken:" in result.output


def test_skills_shows_a_broken_skill_md_without_hiding_the_rest(runner, tmp_path):
    skills = tmp_path / "skills"
    (skills / "qcc-company").mkdir(parents=True)
    (skills / "qcc-company" / "SKILL.md").write_text(
        "---\nname: qcc-company\n---\n\n## 1. 画像 `get_company_profile`\n\n查询画像。\n",
        encoding="utf-8",
    )
    (skills / "malformed").mkdir()
    (skills / "malformed" / "SKILL.md").write_text("no front matter at all", encoding="utf-8")

    result = runner.invoke(cli_module.app, ["skills", "--dir", str(skills)])
    assert result.exit_code == 0
    assert "qcc-company" in result.output
    assert "malformed" in result.output


def test_skills_shows_an_unparseable_mcp_json_as_broken(runner, tmp_path):
    skills = tmp_path / "skills"
    (skills / "qcc-company").mkdir(parents=True)
    (skills / "qcc-company" / "SKILL.md").write_text(
        "---\nname: qcc-company\n---\n\n## 1. 画像 `get_company_profile`\n\n查询画像。\n",
        encoding="utf-8",
    )
    (skills / "qcc-company" / "mcp.json").write_text("{not json", encoding="utf-8")

    result = runner.invoke(cli_module.app, ["skills", "--dir", str(skills)])
    assert result.exit_code == 0
    assert "qcc-company" in result.output
    assert "broken:" in result.output


def test_skills_shows_an_unloadable_skill_transport(runner, tmp_path):
    """A skill whose mcp.json is valid JSON but missing a server stays in the table."""
    skills = tmp_path / "skills"
    (skills / "qcc-company").mkdir(parents=True)
    (skills / "qcc-company" / "SKILL.md").write_text(
        "---\nname: qcc-company\n---\n\n## 1. 画像 `get_company_profile`\n\n查询画像。\n",
        encoding="utf-8",
    )
    (skills / "qcc-company" / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    result = runner.invoke(cli_module.app, ["skills", "--dir", str(skills)])
    assert result.exit_code == 0
    assert "qcc-company" in result.output


def test_skills_skips_directories_without_a_skill_file(runner, tmp_path):
    skills = tmp_path / "skills"
    (skills / "no-skill-md").mkdir(parents=True)
    result = runner.invoke(cli_module.app, ["skills", "--dir", str(skills)])
    assert result.exit_code == 0
    assert "no-skill-md" not in result.output


# ---------------------------------------------------------------------------
# CLI: serve
# ---------------------------------------------------------------------------


def _record_execvp(monkeypatch, calls: list[list[str]]) -> None:
    """Swap ``os.execvp`` for a recorder that returns without replacing the process.

    ``serve`` execs rather than spawns, so the whole contract of the command is
    what reaches ``execvp`` — the argument vector, whose ``argv[0]`` is the
    binary.  The real call must never be made: it would replace the pytest
    process with ``langgraph`` instead of letting any assertion run.
    """
    def record(_binary: str, argv: list[str]) -> None:
        calls.append(list(argv))

    monkeypatch.setattr(os, "execvp", record)


def test_serve_help_lists_host_and_port(runner):
    """P4 (fixed) — the README's documented flags are options of the sub-command."""
    result = runner.invoke(cli_module.app, ["serve", "--help"])

    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output


def test_serve_defaults_are_real_values_through_the_sub_command(monkeypatch, runner):
    """P2 (fixed) — ``serve`` reaches ``execvp`` with resolved defaults.

    Driven through the sub-command, which is where the bug lived: ``serve_command``
    used to call ``serve_main()`` with no arguments, so Typer's ``OptionInfo``
    defaults leaked into the log line and ``" ".join(args)`` raised before the
    binary was ever looked up.  Asserting the default values proves the values are
    resolved and threaded through, not merely that the join stopped failing.
    """
    calls: list[list[str]] = []
    _record_execvp(monkeypatch, calls)

    result = runner.invoke(cli_module.app, ["serve"])

    assert result.exit_code == 0
    assert calls == [EXPECTED_SERVE_ARGV]
    assert "$ langgraph serve src/agent_qcc/agent.py:graph --host 127.0.0.1 --port 2024" in result.output
    assert "OptionInfo" not in result.output


def test_serve_passes_through_host_and_port(monkeypatch, runner):
    """P4 (fixed) — user-supplied flags reach ``langgraph`` unchanged."""
    calls: list[list[str]] = []
    _record_execvp(monkeypatch, calls)

    result = runner.invoke(cli_module.app, ["serve", "--host", "0.0.0.0", "--port", "3000"])

    assert result.exit_code == 0
    assert calls == [EXPECTED_SERVE_ARGV[:3] + ["--host", "0.0.0.0", "--port", "3000"]]
    assert "--host 0.0.0.0" in result.output
    assert "--port 3000" in result.output


def test_serve_quotes_host_values_in_the_printed_command(monkeypatch, runner):
    """A host with spaces is shell-escaped, so the printed line stays re-runnable."""
    calls: list[list[str]] = []
    _record_execvp(monkeypatch, calls)

    result = runner.invoke(cli_module.app, ["serve", "--host", "my host"])

    assert result.exit_code == 0
    assert "'my host'" in result.output
    assert "--host my host" not in result.output


def test_serve_without_langgraph_exits_1(monkeypatch, runner):
    """``serve`` fails loudly when the binary is missing — with a hint, not a crash.

    ``os.execvp`` now receives real values, so this is the missing-binary branch
    rather than a crash while formatting the log line: exit 1 plus an install hint,
    no traceback.  The recorded argv proves the command line was built correctly
    before the lookup failed.
    """
    calls: list[list[str]] = []

    def no_such_binary(_binary: str, argv: list[str]) -> None:
        calls.append(list(argv))
        raise OSError("not found")

    monkeypatch.setattr(os, "execvp", no_such_binary)

    result = runner.invoke(cli_module.app, ["serve", "--host", "127.0.0.1", "--port", "2024"])

    assert result.exit_code == 1
    assert calls == [EXPECTED_SERVE_ARGV]
    assert "无法启动 langgraph CLI" in result.output
    assert "请先安装" in result.output
    assert "TypeError" not in result.output


def test_serve_main_standalone_execs_the_expected_command(runner, monkeypatch):
    """``agent-qcc-serve`` resolves its own options and builds the expected argv."""
    import typer

    standalone = typer.Typer()
    standalone.command()(cli_module.serve_main)

    calls: list[list[str]] = []
    _record_execvp(monkeypatch, calls)

    result = runner.invoke(standalone, ["--host", "127.0.0.1", "--port", "2024"])

    assert result.exit_code == 0
    assert calls == [EXPECTED_SERVE_ARGV]
    assert "$ langgraph" in result.output


# ---------------------------------------------------------------------------
# defects pinned as-is
# ---------------------------------------------------------------------------


def test_DEFECT_async_cli_commands_are_never_awaited(runner, factory_tuple, monkeypatch):
    """P1 (fixed) — the CLI commands are plain ``def`` functions Typer really runs.

    ``chat`` and ``tui`` used to be ``async def``; Typer wraps such a callback in a
    plain ``def`` that returns the coroutine, so the body never ran and every
    invocation exited 0 with no output, no work and no connector call — only a
    ``RuntimeWarning: coroutine 'chat' was never awaited``.  Pinned the other way
    round now: nothing is left for Typer to forget to await, so the body runs and the
    command reaches the synchronous entry point YEJ-133 made valid on an async-only
    graph — ``graph.invoke`` raises on this langgraph, which is why the CLI goes
    through ``invoke``.
    """
    graph, _checkpointer, pipeline, _skill, _settings = factory_tuple
    calls: list[Any] = []

    def fake_invoke(graph_, question, *, thread_id="default", state=None):
        calls.append((question, thread_id))
        return new_state(question)  # a real shape for `_render` to print

    monkeypatch.setattr(agent_module, "invoke", fake_invoke)

    result = runner.invoke(cli_module.app, ["chat", QUERY, "--thread", "defect-t1"])

    assert result.exit_code == 0
    assert calls == [(QUERY, "defect-t1")]  # reached invoke with the --thread value
    assert not inspect.iscoroutinefunction(cli_module.chat)
    assert not inspect.iscoroutinefunction(cli_module.tui)
    assert not inspect.iscoroutinefunction(cli_module._run)

    with pytest.raises(TypeError, match="No synchronous function provided"):
        graph.invoke(new_state(QUERY), config={"configurable": {"thread_id": "direct-sync"}})


def test_DEFECT_tui_is_also_never_awaited(runner, factory_tuple, monkeypatch):
    """P1 (fixed), second command: ``tui`` reads its prompt and exits cleanly."""
    graph, _checkpointer, pipeline, _skill, _settings = factory_tuple
    calls: list[Any] = []

    def fake_invoke(graph_, question, *, thread_id="default", state=None):
        calls.append((question, thread_id))
        return new_state(question)  # a real shape for `_render` to print

    monkeypatch.setattr(agent_module, "invoke", fake_invoke)

    result = runner.invoke(cli_module.app, ["tui", "--thread", "defect-t2"], input="q\n")

    assert result.exit_code == 0
    assert "已退出" in result.output
    assert calls == []  # quit before asking anything, so no turn ran


def test_DEFECT_serve_main_builds_a_malformed_command_line(monkeypatch, runner):
    """P2 (fixed) — ``serve_main`` takes real values, so no argument is malformed.

    Called with no arguments it used to receive Typer ``OptionInfo`` defaults for
    ``host`` / ``port``, so ``" ".join(args)`` raised ``TypeError`` before
    ``os.execvp`` was attempted.  Now the values are resolved by the caller, so a bare
    call builds a real argv, stringifies it before joining, and shell-quotes the
    printed command.  The crash path
    is gone: a bare call exits 0 with a recorded ``execvp``, not 1 with a traceback.

    Driven through the standalone console-script app rather than ``subprocess``:
    ``execvp`` replaces the process image, so the recorders never let a real one
    through — otherwise the test would turn into a ``langgraph`` server instead
    of an assertion.
    """
    import typer

    standalone = typer.Typer()
    standalone.command()(cli_module.serve_main)

    calls: list[list[str]] = []
    _record_execvp(monkeypatch, calls)

    result = runner.invoke(standalone, [])

    assert result.exit_code == 0
    assert "expected str instance, OptionInfo found" not in result.output
    assert "$ langgraph serve src/agent_qcc/agent.py:graph" in result.output
    assert calls == [EXPECTED_SERVE_ARGV]


def test_serve_main_called_without_arguments_uses_parameter_defaults(monkeypatch):
    """P2, at the exact call site that crashed: ``serve_main()`` with no arguments.

    That is how both wrappers used to invoke it, and it is the line that raised
    ``TypeError`` while joining Typer ``OptionInfo`` objects.  The values now come
    from the parameter defaults, so a bare call is valid and reaches ``execvp``.
    """
    calls: list[list[str]] = []
    _record_execvp(monkeypatch, calls)

    cli_module.serve_main()

    assert calls == [EXPECTED_SERVE_ARGV]


def test_DEFECT_factory_failure_is_a_readable_error_instead_of_a_blank_screen(runner, monkeypatch):
    """P3 (fixed) — a failing ``build_agent`` no longer leaves a blank screen.

    ``chat`` used to call the factory bare: a missing skill or a misconfigured
    ``AGENT_QCC_SKILLS_DIR`` produced exit 1 and an empty screen, so the user
    could not tell what was wrong.  Now the failure names the skills root that
    was read, the exact ``SKILL.md`` path that is expected, and the two settings
    that decide both.

    The factory is replaced with something that raises, so this covers
    ``chat``'s error handling without depending on the on-disk skills tree;
    ``test_chat_names_the_missing_skill_when_the_configuration_is_wrong`` drives
    the same command through a real ``Settings`` instead.  Before P1 was fixed
    the old ``exit_code == 1`` assertion passed vacuously — ``chat`` never ran at
    all, so nothing could have raised and no message could have been printed.
    """

    def boom(settings=None):
        raise FileNotFoundError("connector skill not found: skills/nope/SKILL.md")

    monkeypatch.setattr(agent_module, "build_agent", boom)

    result = runner.invoke(cli_module.app, ["chat", QUERY])

    assert result.exit_code == 1  # still a non-zero exit, as before
    message = result.output
    assert message.strip() != ""  # the user is never left with a blank screen
    assert "错误：" in message  # our diagnostic, not Click's error renderer
    assert "SKILL.md" in message  # the missing path is named
    assert "skills" in message
    assert "AGENT_QCC_SKILLS_DIR" in message
    assert "AGENT_QCC_QCC_SKILL_NAME" in message
    assert result.exception is not None and not isinstance(result.exception, FileNotFoundError)
    assert "Traceback" not in message


def test_chat_names_the_missing_skill_when_the_configuration_is_wrong(runner, monkeypatch):
    """The report shows the configuration the user actually set, end to end.

    ``AGENT_QCC_SKILLS_DIR`` points at a directory that does not exist: ``chat``
    exits 1 and prints the root it tried to read plus the exact ``SKILL.md`` it
    was looking for — both settings are named so the fix is one grep away.
    """
    monkeypatch.setenv("AGENT_QCC_SKILLS_DIR", "nope")
    monkeypatch.setenv("AGENT_QCC_QCC_SKILL_NAME", "qcc-company")

    result = runner.invoke(cli_module.app, ["chat", QUERY])

    assert result.exit_code == 1
    message = result.output
    assert "nope" in message  # the root the user configured is shown
    assert "qcc-company" in message  # the skill that was missing is named
    assert "SKILL.md" in message
    assert "AGENT_QCC_SKILLS_DIR" in message
    assert "Traceback" not in message


def test_tui_reports_what_is_missing_when_the_configuration_is_wrong(runner, monkeypatch):
    """``tui`` shares the guard, so its start-up cannot be a blank screen either."""
    monkeypatch.setenv("AGENT_QCC_SKILLS_DIR", "nope")
    monkeypatch.setenv("AGENT_QCC_QCC_SKILL_NAME", "nope-skill")

    result = runner.invoke(cli_module.app, ["tui"], input="q\n")

    assert result.exit_code == 1
    message = result.output
    assert "nope" in message
    assert "nope-skill" in message
    assert "AGENT_QCC_QCC_SKILL_NAME" in message


def test_DEFECT_chat_reports_no_output_even_for_a_valid_question(
    runner, captured_console, monkeypatch, factory_tuple
):
    """P1 + P5 (both fixed) — a valid query now produces a real report.

    Before the fix this same call exited 0 while printing nothing at all: the
    command body never ran (P1) and, had it run, ``invoke`` raised on the
    async-only graph (P5).  Now the banner, the 4R+1 progress lines and the
    rendered answer all reach the user, the connector really gets called with the
    company out of the question, and exit 0 means the query actually completed.
    """
    graph, _, pipeline, skill, settings = factory_tuple
    seen: list[Any] = []

    def fake_build_agent(st=None):
        seen.append(st)
        return (graph, None, pipeline, skill, settings)

    monkeypatch.setattr(agent_module, "build_agent", fake_build_agent)
    export_text, _ = captured_console

    result = runner.invoke(cli_module.app, ["chat", QUERY, "--thread", "report-t1"])

    assert result.exit_code == 0
    assert seen == [None]  # the factory ran, with no credentials smuggled in
    output = export_text()
    assert "企业尽调 Agent" in output  # the banner
    assert settings.model_name in output
    assert "mode: offline" in output  # the banner states the mode
    assert "route:" in output
    assert "evidence:" in output
    assert "reflect:" in output
    assert "编排结果（规则渲染）" in output  # _render printed the report
    assert "全部调用失败" in output  # the stub failure is surfaced, not hidden
    assert "plan:" not in output  # a failed turn leaves tool_calls empty
    assert COMPANY in output

    tools = {name for name, _args in pipeline.client.calls}
    assert tools, "the connector was never called"
    assert tools <= set(skill.tool_names())
    for _name, args in pipeline.client.calls:
        assert args == {"name": COMPANY}  # the question's target reached the connector


def test_chat_reports_a_successful_query(runner, captured_console, monkeypatch, factory_tuple):
    """Offline happy path: a healthy connector yields a report without gaps."""
    graph, _, pipeline, skill, settings = factory_tuple
    pipeline.client = StubQcc(
        {tool.name: tool for tool in skill.tools},
        ok=True,
        data={"注册资本": "1000万元", "股东": ["自然人股东"]},
    )
    monkeypatch.setattr(agent_module, "build_agent", lambda _settings=None: factory_tuple)
    export_text, _ = captured_console

    result = runner.invoke(cli_module.app, ["chat", QUERY, "--verbose"])

    assert result.exit_code == 0
    output = export_text()
    calls = len(pipeline.client.calls)
    assert f"{calls}/{calls} ok" in output  # every planned call succeeded
    assert "缺口" not in output  # no gaps, so no reflection gap section
    assert "注册资本" in output
    assert "tool_calls" in output  # --verbose dumps the state


def test_chat_surfaces_a_connector_failure_as_a_report(runner, captured_console, monkeypatch, factory_tuple):
    """A dead connector is reported, not swallowed: exit 0 with the failure in it."""
    graph, _, pipeline, skill, settings = factory_tuple
    pipeline.client = StubQcc({tool.name: tool for tool in skill.tools}, ok=False, error="连接器 500")
    monkeypatch.setattr(agent_module, "build_agent", lambda _settings=None: factory_tuple)
    export_text, _ = captured_console

    result = runner.invoke(cli_module.app, ["chat", QUERY])

    assert result.exit_code == 0
    output = export_text()
    assert "0/3 ok" in output  # three core tools, none of them healthy
    assert "全部调用失败" in output
    assert "连接器 500" in output


def test_chat_with_an_unanswerable_question_still_renders(
    runner, captured_console, monkeypatch, factory_tuple
):
    """No company in the question: the pipeline asks for one instead of crashing."""
    graph, _, pipeline, skill, settings = factory_tuple
    monkeypatch.setattr(agent_module, "build_agent", lambda _settings=None: factory_tuple)
    export_text, _ = captured_console

    result = runner.invoke(cli_module.app, ["chat", "你好"])

    assert result.exit_code == 0
    assert "编排结果（规则渲染）" in export_text()
    assert pipeline.client.calls == []  # nothing to look up, no connector call


def test_chat_live_mode_announces_the_connector(runner, captured_console, monkeypatch):
    """A non-offline pipeline tells the user which endpoint it will call."""
    graph, checkpointer, pipeline, skill, settings = build_agent(make_settings(offline=False))
    monkeypatch.setattr(
        agent_module, "build_agent", lambda _settings=None: (graph, checkpointer, pipeline, skill, settings)
    )
    export_text, _ = captured_console

    result = runner.invoke(cli_module.app, ["chat", QUERY])

    assert result.exit_code == 0
    assert "mode: live" in export_text()  # the banner states live mode
    assert pipeline.client.url in export_text()


def test_chat_threads_state_to_a_resumable_thread(runner, factory_tuple, monkeypatch):
    """Two ``chat`` calls with the same ``--thread`` continue one conversation."""
    graph, _, pipeline, skill, settings = factory_tuple
    monkeypatch.setattr(agent_module, "build_agent", lambda _settings=None: factory_tuple)

    first = runner.invoke(cli_module.app, ["chat", QUERY, "--thread", "chat-2"])
    follow_up = f"再查一下{COMPANY}的联系方式"
    second = runner.invoke(cli_module.app, ["chat", follow_up, "--thread", "chat-2"])

    assert first.exit_code == 0 and second.exit_code == 0
    snapshot = graph.get_state({"configurable": {"thread_id": "chat-2"}})
    assert [str(m.content) for m in snapshot.values["messages"] if m.type == "human"] == [
        QUERY,
        follow_up,
    ]
    assert len(pipeline.client.calls) == 4  # 3 core tools + the contact lookup
    for _tool, args in pipeline.client.calls:  # every call named the same company
        assert COMPANY in str(args)


def test_chat_uses_the_synchronous_entry_point_that_drives_ainvoke(
    runner, captured_console, monkeypatch, factory_tuple
):
    """P1 + P5 together at the seam: the CLI calls ``invoke``, which runs ``ainvoke``.

    ``_run`` is the one line that ties the two halves of the fix together — the
    synchronous command body (P1) reaches the synchronous entry point that YEJ-133
    made valid on langgraph 1.x.  This asserts the call goes through ``invoke``
    with the requested question and thread id, that a real turn ran end to end,
    and that ``graph.invoke`` is not what does the work: the pipeline stages are
    coroutines, so a synchronous ``CompiledGraph.invoke`` raises
    ``TypeError: No synchronous function provided`` on this langgraph.
    """
    graph, _checkpointer, pipeline, skill, _settings = factory_tuple
    entered: list[Any] = []
    original = agent_module.invoke

    def traced(graph_, question, *, thread_id="default", state=None):
        entered.append((question, thread_id))
        return original(graph_, question, thread_id=thread_id, state=state)

    monkeypatch.setattr(agent_module, "build_agent", lambda _settings=None: factory_tuple)
    monkeypatch.setattr(agent_module, "invoke", traced)
    export_text, _ = captured_console

    result = runner.invoke(cli_module.app, ["chat", QUERY, "--thread", "p1-p5"])

    assert result.exit_code == 0
    assert entered == [(QUERY, "p1-p5")]
    assert pipeline.client.calls, "the turn really ran"
    assert "编排结果（规则渲染）" in export_text()

    with pytest.raises(TypeError, match="No synchronous function provided"):
        graph.invoke(new_state(QUERY), config={"configurable": {"thread_id": "direct-sync"}})


def test_tui_answers_two_turns_and_keeps_history(runner, captured_console, monkeypatch, factory_tuple):
    """P1 (fixed) in ``tui``: every entered question runs and the thread resums.

    Turn one routes and answers; turn two is routed on its own question and the
    checkpoint — not a state dict fed back into ``invoke`` — carries the history
    forward, so the human messages accumulate.  Feeding ``state=state`` back used
    to rerun every turn on the previous question and drop the new one (pinned in
    ``test_synchronous_invoke_ignores_the_question_when_state_is_passed_back``).
    """
    graph, _, pipeline, skill, settings = factory_tuple
    monkeypatch.setattr(agent_module, "build_agent", lambda _settings=None: factory_tuple)
    export_text, _ = captured_console

    follow_up = f"再查一下{COMPANY}的联系方式"
    result = runner.invoke(
        cli_module.app, ["tui", "--thread", "tui-t1"], input=f"{QUERY}\n{follow_up}\nq\n"
    )

    assert result.exit_code == 0
    assert "已退出" in result.output

    output = export_text()
    assert output.count("编排结果（规则渲染）") == 2  # both turns rendered
    assert output.count("route:") == 2
    assert COMPANY in output
    assert follow_up in output  # the second question reached the pipeline
    assert "联系方式" in output  # ...and was routed on its own target

    assert len(pipeline.client.calls) == 4  # 3 core tools + the contact lookup
    for _tool, args in pipeline.client.calls:
        assert COMPANY in str(args)

    snapshot = graph.get_state({"configurable": {"thread_id": "tui-t1"}})
    assert [str(m.content) for m in snapshot.values["messages"] if m.type == "human"] == [
        QUERY,
        follow_up,
    ]


def test_tui_offline_notice_and_quit_before_any_question(runner, captured_console, monkeypatch, factory_tuple):
    """Entering ``q`` immediately costs no connector call and exits cleanly."""
    graph, _, pipeline, skill, settings = factory_tuple
    monkeypatch.setattr(agent_module, "build_agent", lambda _settings=None: factory_tuple)
    export_text, _ = captured_console

    result = runner.invoke(cli_module.app, ["tui"], input="q\n")

    assert result.exit_code == 0
    assert "已退出" in result.output
    assert "离线演示模式" in export_text()
    assert pipeline.client.calls == []


def test_tui_ignores_blank_input_and_keeps_asking(runner, captured_console, monkeypatch, factory_tuple):
    graph, _, pipeline, skill, settings = factory_tuple
    monkeypatch.setattr(agent_module, "build_agent", lambda _settings=None: factory_tuple)
    export_text, _ = captured_console

    result = runner.invoke(cli_module.app, ["tui"], input=f"\n  {QUERY}  \nexit\n")

    assert result.exit_code == 0
    assert export_text().count("编排结果（规则渲染）") == 1
    assert len(pipeline.client.calls) == 3


def test_chat_and_tui_share_a_thread_when_given_the_same_id(runner, monkeypatch, factory_tuple):
    """``--thread`` is honoured by both commands, so handoff between them works."""
    graph, _, pipeline, skill, settings = factory_tuple
    monkeypatch.setattr(agent_module, "build_agent", lambda _settings=None: factory_tuple)

    first = runner.invoke(cli_module.app, ["chat", QUERY, "--thread", "shared-1"])
    second = runner.invoke(cli_module.app, ["tui", "--thread", "shared-1"], input="q\n")

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert len(pipeline.client.calls) == 3  # only the chat turn ran
    snapshot = graph.get_state({"configurable": {"thread_id": "shared-1"}})
    assert [str(m.content) for m in snapshot.values["messages"] if m.type == "human"] == [QUERY]


# ---------------------------------------------------------------------------
# live: needs real 企查查 + real LLM credentials
# ---------------------------------------------------------------------------


def _require_live_credentials() -> Settings:
    """A checked-in ``.env`` with placeholders cannot turn this into a network call."""
    token = os.environ.get(QCC_TOKEN_ENV)
    key = os.environ.get(OPENAI_KEY_ENV)
    if not token or not key:
        pytest.skip(f"requires {QCC_TOKEN_ENV} and {OPENAI_KEY_ENV}")
    return Settings(
        _env_file=None,
        offline=False,
        use_llm=True,
        qcc_auth_token=token,
        openai_api_key=key,
        openai_base_url=os.environ.get("AGENT_QCC_OPENAI_BASE_URL") or "https://api.openai.com/v1",
        qcc_retries=int(os.environ.get("AGENT_QCC_QCC_RETRIES", "1")),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_INTEGRATION_factory_runs_a_real_turn():
    """The factory path with real credentials: assert shape, never fixed content."""
    settings = _require_live_credentials()
    graph, checkpointer, pipeline, skill, returned = build_agent(settings)

    assert returned is settings
    assert graph.checkpointer is checkpointer
    assert pipeline.settings is settings
    assert pipeline.llm is not None

    state = await graph.ainvoke(
        new_state(f"查询：{COMPANY}｜注册资本、股东结构"),
        config={"configurable": {"thread_id": "live-t1"}},
    )

    assert isinstance(state["messages"][-1], AIMessage)
    assert state["route"]["company"]
    for evidence in state["evidence"]:
        assert set(EVIDENCE_KEYS) <= set(evidence)
        # the agent must never invent a connector tool that is not in the skill
        assert evidence["tool"] in skill.tool_names()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_INTEGRATION_factory_reaches_the_real_connector():
    """A live query must actually call the MCP endpoint, unchanged in shape."""
    settings = _require_live_credentials()
    skill = load_skill(settings.skills_dir, settings.qcc_skill_name)
    pipeline = Pipeline(skill, settings)
    assert pipeline.client.url, "a live pipeline must have a connector endpoint"

    calls: list[tuple[str, dict[str, Any]]] = []
    client = pipeline.client

    async def recorded(tool_name: str, args: dict[str, Any]) -> ToolResult:
        """Exercise the real MCP transport, then report it as an evidence item."""
        await client._initialize()
        response = await client._get_client().post(
            client.url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": args},
            },
            headers=client._headers(),
        )
        response.raise_for_status()
        calls.append((tool_name, dict(args)))
        return ToolResult(
            tool=tool_name,
            target=extract_target(args),
            ok=not (isinstance(response.json().get("result"), dict) and response.json()["result"].get("isError")),
            data=_mcp_payload(response),
            duration_ms=1,
        )

    pipeline.client.call = recorded  # type: ignore[method-assign]
    graph = build_graph(pipeline, checkpointer=InMemorySaver())

    state = await graph.ainvoke(new_state(f"查询：{COMPANY}｜注册资本、股东结构"))

    assert calls, "the connector was never called"
    assert all("name" in args for _, args in calls)
    assert state["evidence"]


def _mcp_payload(response: Any) -> Any:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        return parse_sse_stream(response.text) or {}
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict):
        return payload.get("result", payload)
    return payload
