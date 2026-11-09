"""Checkpointer wiring for the ``langgraph serve`` deployment.

The default store keeps thread state in process memory so the REST API works
out of the box.  Swap in a durable saver by setting
``AGENT_QCC_CHECKPOINTER`` to an import path such as
``langgraph.checkpoint.postgres.PostgresSaver``.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver


def make_checkpointer(settings) -> Any:
    path = getattr(settings, "checkpointer", "") or ""
    if not path:
        return InMemorySaver()
    module_name, _, attribute = path.rpartition(".")
    if not module_name:
        raise ValueError(f"invalid AGENT_QCC_CHECKPOINTER: {path!r}")
    import importlib

    module = importlib.import_module(module_name)
    target = getattr(module, attribute)
    if callable(target):
        try:
            return target()
        except TypeError:
            return target
    return target
