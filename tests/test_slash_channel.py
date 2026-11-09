"""Slash 网关（R0）双通道契约测试。

钉住用户任务「本 Agent 要支持 skill 中所有 slash 命令和自然语言输入」：
- ``/xxx`` → graph 的 ``slash`` 节点直通 skill 分发器，命中后立即 END，不进 4R+1、不进 LLM
- 自然语言 → ``slash`` 节点写回空 ``slash_result``，条件边指回 ``route``，走完整 4R+1

外部依赖（企查查连接器 / 模型）全部离线打桩；只有 slash 分发器在真实用例里被调用一次，
以证明 ``slash_router`` → ``skills/qcc-super/scripts/slash.py`` 的端到端接线成立。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agent_qcc import slash_router
from agent_qcc.config import Settings
from agent_qcc.graph import build_graph
from agent_qcc.pipeline import Pipeline, slash_markdown
from agent_qcc.qcc import ToolResult
from agent_qcc.slash_router import is_slash_command, try_dispatch_slash
from agent_qcc.skill import load_skill
from agent_qcc.state import new_state

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
COMPANY = "北京示例科技有限公司"
NL_QUERY = f"查询：{COMPANY}｜注册资本、股东结构"


# ---------------------------------------------------------------------------
# stubs / helpers
# ---------------------------------------------------------------------------


class StubQcc:
    """记录调用并恒返回成功的连接器桩（离线、无凭据）。"""

    def __init__(self, tools: dict[str, Any]) -> None:
        self.tools = tools
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        self.calls.append((tool_name, dict(args)))
        target = args.get("name") or args.get("searchKey") or ""
        return ToolResult(
            tool=tool_name, target=target, ok=True, data={"ok": True},
            error="", duration_ms=1,
        )


def make_pipeline() -> Pipeline:
    settings = Settings(_env_file=None, offline=True, use_llm=False)
    skill = load_skill(SKILLS_DIR, "qcc-company")
    pipeline = Pipeline(skill, settings)
    pipeline.client = StubQcc({tool.name: tool for tool in skill.tools})
    pipeline.llm = None
    return pipeline


def run_graph(pipeline: Pipeline, text: str) -> tuple[list[str], dict[str, Any]]:
    """跑一轮，返回 (含 slash 的完整节点序列, 最终 state)。"""

    async def collect() -> tuple[list[str], dict[str, Any]]:
        graph = build_graph(pipeline)
        sequence: list[str] = []
        state: dict[str, Any] = {}
        async for mode, payload in graph.astream(
            new_state(text), stream_mode=["updates", "values"]
        ):
            if mode == "updates":
                sequence.extend(str(name) for name in payload)
            else:
                state.update(payload)
        return sequence, state

    return asyncio.run(collect())


# ---------------------------------------------------------------------------
# slash_router 纯函数契约
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("/qcc-help", True),
        ("/kg-query foo", True),
        ("  /raw-sql select 1", True),
        ("查询北京示例科技有限公司", False),
        ("", False),
        ("/", False),  # 只有一个斜杠不算命令
        ("a/b 路径", False),
    ],
)
def test_is_slash_command_truth_table(text: str, expected: bool) -> None:
    assert is_slash_command(text) is expected


def test_natural_language_is_not_dispatched() -> None:
    assert try_dispatch_slash(NL_QUERY) is None


def test_real_dispatcher_handles_qcc_help() -> None:
    """端到端接线：真实 slash.py 的 /qcc-help 返回命令清单。"""
    result = try_dispatch_slash("/qcc-help")
    assert result is not None
    assert result["ok"] is True
    assert result["command"] == "/qcc-help"
    assert result["commands"], "命令清单不应为空"


def test_unknown_slash_command_degrades_to_error_dict() -> None:
    result = try_dispatch_slash("/definitely-not-a-real-command")
    assert result is not None
    assert result["command"] == "/definitely-not-a-real-command"
    # 未知命令要么被 handler 判为失败，要么抛错被兜底成 ok=False，绝不返回 None
    assert result.get("ok") is False


# ---------------------------------------------------------------------------
# graph 级 R0 网关：双通道分流
# ---------------------------------------------------------------------------


def test_graph_slash_channel_short_circuits_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """/xxx 命中后：只跑 slash 节点即 END，不进 route/reason/retrieve，不触连接器。"""
    monkeypatch.setattr(
        slash_router,
        "try_dispatch_slash",
        lambda text: {"ok": True, "command": "/qcc-help", "commands": [{"slash": "/qcc-help"}]},
    )
    pipeline = make_pipeline()

    sequence, state = run_graph(pipeline, "/qcc-help")

    assert sequence == ["slash"]  # 网关直接结束，无 4R+1
    assert state["slash_result"], "slash_result 应记录本轮命令结果"
    assert pipeline.client.calls == []  # 连接器从未被调用
    # 结果以 markdown 落到 messages，供 REST / Playground 读取
    assert "/qcc-help" in str(state["messages"][-1].content)


def test_graph_natural_language_runs_full_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """自然语言：slash 网关写回空结果，条件边指回 route，跑完整 4R+1。"""
    monkeypatch.setattr(slash_router, "try_dispatch_slash", lambda text: None)
    pipeline = make_pipeline()

    sequence, state = run_graph(pipeline, NL_QUERY)

    assert sequence[0] == "slash"  # 网关先行
    assert not state.get("slash_result")  # 空 dict → 未短路
    for stage in ("route", "reason", "retrieve", "reflect", "respond"):
        assert stage in sequence, f"自然语言应经过 {stage}"
    assert pipeline.client.calls, "自然语言通道应触达连接器"


def test_graph_slash_result_is_reset_between_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一 thread 第二轮自然语言必须清空上一轮遗留的 slash_result。"""
    calls = {"n": 0}

    def fake_dispatch(text: str):
        calls["n"] += 1
        if text.startswith("/"):
            return {"ok": True, "command": "/qcc-help", "commands": []}
        return None

    monkeypatch.setattr(slash_router, "try_dispatch_slash", fake_dispatch)
    pipeline = make_pipeline()

    slash_seq, slash_state = run_graph(pipeline, "/qcc-help")
    assert slash_seq == ["slash"] and slash_state["slash_result"]

    nl_seq, nl_state = run_graph(pipeline, NL_QUERY)
    assert not nl_state.get("slash_result")  # 网关把它重置为空
    assert "route" in nl_seq


# ---------------------------------------------------------------------------
# slash_markdown 渲染形态
# ---------------------------------------------------------------------------


def test_slash_markdown_failure_includes_error_and_hint() -> None:
    md = slash_markdown({"ok": False, "command": "/kg-query", "error": "boom", "hint": "先 /kg-build"})
    assert "执行失败" in md
    assert "boom" in md
    assert "先 /kg-build" in md


def test_slash_markdown_commands_renders_a_table() -> None:
    md = slash_markdown(
        {
            "ok": True,
            "command": "/qcc-help",
            "commands": [{"slash": "/qcc-profile", "category": "核心", "label": "画像", "usage": "/qcc-profile <name>"}],
        }
    )
    assert "| 命令 |" in md
    assert "/qcc-profile" in md


def test_slash_markdown_plain_result_renders_json_body() -> None:
    md = slash_markdown({"ok": True, "command": "/raw-sql", "rows": [{"a": 1}]})
    assert "```json" in md
    assert "rows" in md
    # 内部字段不外泄
    assert "handler" not in md
