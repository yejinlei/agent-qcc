"""Convenience factory shared by the CLI, TUI and `langgraph serve`."""

from __future__ import annotations

import asyncio

from langgraph.checkpoint.memory import InMemorySaver

from .config import Settings, get_settings
from .graph import build_graph
from .pipeline import Pipeline
from .skill import load_skill
from .store import make_checkpointer


def build_agent(settings: Settings | None = None):
    """Return ``(compiled_graph, checkpointer, pipeline, skill, settings)``."""
    settings = settings or get_settings()
    skill = load_skill(settings.skills_dir, settings.qcc_skill_name)
    pipeline = Pipeline(skill, settings)
    checkpointer = InMemorySaver()
    graph = build_graph(pipeline, checkpointer=checkpointer)
    return graph, checkpointer, pipeline, skill, settings


def invoke(graph, user_input: str, *, thread_id: str = "default", state=None):
    """Run one turn with a stable thread id so history is resumable.

    The 4R+1 stages are coroutines, and langgraph 1.x rejects a synchronous
    ``CompiledGraph.invoke`` for an async-only graph (``TypeError: No synchronous
    function provided``), so a synchronous caller needs its own event loop.
    Running ``ainvoke`` under ``asyncio.run`` keeps this entry point callable
    from plain ``def`` code while the REST path keeps ``ainvoke``.

    A fresh loop per turn is deliberate: it tears down the turn's client resources
    as it goes and leaves no cached resource pinned to a closed loop — the next
    turn opens a new one, which is already how a new CLI process behaves.
    """
    config = {"configurable": {"thread_id": thread_id}}
    from .state import new_state

    payload = state if state is not None else new_state(user_input)
    return asyncio.run(graph.ainvoke(payload, config))


# `langgraph serve` imports this callable-style graph definition.
def get_graph():
    graph, *_ = build_agent()
    return graph


graph = get_graph()
