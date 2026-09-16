"""Graph state shared by the 4R+1 stages."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages


def merge_evidence(left: list[Evidence], right: list[Evidence]) -> list[Evidence]:
    """Reducer so the reflect loop can append evidence instead of overwriting it."""
    return [*left, *right]


Decision = Literal["respond", "revise", "clarify", "chitchat"]


class ToolCallSpec(TypedDict, total=False):
    """One planned connector call."""

    tool: str
    args: dict[str, Any]
    reason: str


class Evidence(TypedDict, total=False):
    """One retrieved result, kept verbatim so `respond` cannot invent data."""

    tool: str
    target: str
    ok: bool
    data: Any
    error: str
    duration_ms: int


class AgentState(TypedDict, total=False):
    """State graph schema.

    `messages` follows the LangGraph standard so the same graph works with
    `langgraph-cli serve`, REST clients and human-in-the-loop thread resumption.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    route: dict[str, Any]
    plan: dict[str, Any]
    evidence: Annotated[list[Evidence], merge_evidence]
    reflections: list[dict[str, Any]]
    decision: Decision
    iteration: int


def new_state(user_input: str) -> AgentState:
    """Initial state for a single user turn."""
    return {
        "messages": [HumanMessage(content=user_input)],
        "route": {},
        "plan": {"tool_calls": []},
        "evidence": [],
        "reflections": [],
        "decision": "respond",
        "iteration": 0,
    }
