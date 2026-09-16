"""Compile the 4R+1 LangGraph."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .pipeline import Pipeline
from .state import AgentState


def _route_next(state: AgentState) -> str:
    """Skip straight to `respond` when the request is out of scope."""
    route = state.get("route") or {}
    return "respond" if route.get("intent") == "chitchat" else "reason"


def _reflect_next(state: AgentState) -> str:
    """`revise` loops back to retrieve; everything else answers."""
    return "retrieve" if state.get("decision") == "revise" else "respond"


def build_graph(pipeline: Pipeline, *, checkpointer=None) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("route", pipeline.route)
    graph.add_node("reason", pipeline.reason)
    graph.add_node("retrieve", pipeline.retrieve)
    graph.add_node("reflect", pipeline.reflect)
    graph.add_node("respond", pipeline.respond)

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        _route_next,
        {"reason": "reason", "respond": "respond"},
    )
    graph.add_edge("reason", "retrieve")
    graph.add_edge("retrieve", "reflect")
    graph.add_conditional_edges(
        "reflect",
        _reflect_next,
        {"retrieve": "retrieve", "respond": "respond"},
    )
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)


def graph_spec() -> dict[str, str]:
    """Human readable topology, reused by README and the architecture diagram."""
    return {
        "route": "R1 Route    意图识别 + 企业名抽取（chitchat 直接短路）",
        "reason": "R2 Reason   依据 skill 工具清单规划最少调用",
        "retrieve": "R3 Retrieve 并发/顺序调用企查查连接器",
        "reflect": "R4 Reflect  覆盖率反思，必要时回环补调用",
        "respond": "+1 Respond  证据约束作答",
    }
