"""Guard against an import-only regression hiding inside the pytest process.

Context (YEJ-165): ``src/agent_qcc/graph.py`` imported
``langgraph.graph.graph.CompiledGraph``, a name that stopped existing in
langgraph 1.0.  ``tests/conftest.py`` used to hide it by injecting a
``sys.modules`` shim at collection time, so ``pytest`` reported green while
every real entry point (`agent-qcc chat`, `agent-qcc tui`, `langgraph serve`,
a bare `import agent_qcc`) died at import with
``ModuleNotFoundError: No module named 'langgraph.graph.graph'``.

The process-level guard below runs the entry points in a fresh interpreter,
where conftest cannot reach them.  It fails with an assertion that names the
broken module, instead of a green suite that never imported the code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
PYTHON = sys.executable  # the interpreter under test, not a system python

# The repo .env carries live credentials; subprocesses must run offline so this
# guard cannot turn into a network call.  Env vars outrank the dotenv file.
GUARD_ENV = {
    **os.environ,
    "AGENT_QCC_OFFLINE": "true",
    "AGENT_QCC_USE_LLM": "false",
    "AGENT_QCC_QCC_AUTH_TOKEN": "",
    "AGENT_QCC_QCC_MCP_URL": "",
}


def _run(
    entry: list[str],
    *,
    timeout: float = 180.0,
    env_override: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the real entry point in a fresh interpreter.

    The timeout is generous on purpose: these guards assert *import success* and
    that a real pipeline report is produced, not speed.  A cold child process
    pays the full ``langchain_core`` + ``langgraph`` import cost (~50s on some
    Windows hosts) before the CLI body even runs, so a tight limit would flag a
    healthy entry point as broken.

    A child process never imports this repo's ``conftest.py``, so it sees the
    package exactly as a user would — the shim that used to live in
    ``sys.modules["langgraph.graph.graph"]`` is absent here.  ``-I`` is
    deliberately not used: it implies ``-E`` and would ignore the
    ``PYTHONPATH`` / ``PYTHONUTF8`` we need for an editable repo layout.

    ``env_override`` injects a bad setting to drive an error path: the child
    still starts from the repo ``.env`` plus this shell's variables, which is
    the configuration a user actually has.
    """
    env = {
        **GUARD_ENV,
        # On Windows the default GBk console codec breaks Rich's bullet
        # characters; this is a display setting, not a code path under test.
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(SRC),
        **(env_override or {}),
    }
    proc = subprocess.run(
        [str(PYTHON), *entry],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        timeout=timeout,
    )
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, _decode(proc.stdout), _decode(proc.stderr)
    )


def _decode(payload: bytes) -> str:
    """Decode child output as UTF-8 regardless of the parent's locale."""
    return payload.decode("utf-8", errors="replace") if payload else ""


def _fail(message: str, result: subprocess.CompletedProcess[str]) -> str:
    """Format the child's output tail for an assertion message.  Never raises."""
    text = (result.stdout or "") + (result.stderr or "")
    tail = "\n".join(text.splitlines()[-25:])
    return f"{message}\n\nexit code: {result.returncode}\n{tail}"


def test_guard_agent_qcc_graph_imports_without_the_pytest_shim():
    """A bare import must work in a fresh interpreter, which conftest cannot patch.

    The child never imports this repo's ``conftest.py`` or any pytest plugin, so
    nothing outside the package can stand up a missing module before this
    assertion runs.
    """
    result = _run(["-c", "import agent_qcc.graph as g; print(type(g.build_graph))"])

    assert result.returncode == 0, _fail("a bare `import agent_qcc.graph` failed", result)
    assert "ModuleNotFoundError" not in (result.stdout + result.stderr), _fail(
        "the import is masked by a missing module", result
    )


def test_guard_agent_qcc_cli_chat_runs_a_turn():
    """The real entry point must reach the 4R+1 pipeline, not die at import.

    This is the acceptance check for the fix: a user typing
    ``agent-qcc chat "查询：..."`` gets a rendered report.  It asserts a real
    pipeline marker, so a partial start (help text, a blank screen, a swallowed
    traceback) still fails the test.
    """
    result = _run(["-m", "agent_qcc.cli", "chat", "查询：北京示例科技有限公司"])

    assert result.returncode == 0, _fail("the CLI did not exit cleanly", result)
    assert "ModuleNotFoundError" not in (result.stdout + result.stderr), _fail(
        "the CLI still cannot import the agent", result
    )
    assert "编排结果" in result.stdout, _fail(
        "the CLI ran but never produced a pipeline report", result
    )


def test_guard_agent_qcc_agent_module_exposes_a_compiled_graph():
    """``langgraph serve src/agent_qcc/agent.py:graph`` loads this exact object.

    ``agent.py`` compiles the whole graph at import time, so this is the code
    path the server takes.  The type check is version tolerant: 0.x exposes
    ``CompiledGraph``, 1.x exposes ``CompiledStateGraph``.
    """
    result = _run(
        [
            "-c",
            "from langgraph.graph.state import CompiledStateGraph as C; "
            "import agent_qcc.agent as a; "
            "assert isinstance(a.graph, C); "
            "print(type(a.graph).__name__)",
        ]
    )

    assert result.returncode == 0, _fail(
        "the compiled graph is not reachable from agent_qcc.agent", result
    )
    assert "CompiledStateGraph" in result.stdout, _fail("unexpected graph type", result)


def test_guard_agent_qcc_cli_reports_a_bad_skills_directory():
    """A misconfigured skills root is reported, and it is reported for the CLI too.

    ``agent_qcc.agent`` compiles the graph at module level, so a missing skill
    raises *while the CLI imports it* — the command body never runs, which is
    where the guard for the friendly message used to live and where it was blind.
    This is the exact case from YEJ-131: the user sees a diagnostic naming the
    path and the setting, not a blank screen and not a traceback from agent.py.
    """
    result = _run(
        ["-m", "agent_qcc.cli", "chat", "查询：北京示例科技有限公司"],
        env_override={"AGENT_QCC_SKILLS_DIR": "nope"},
    )

    assert result.returncode == 1, _fail("a misconfigured skills dir must exit non-zero", result)
    combined = result.stdout + result.stderr
    assert "SKILL.md" in combined, _fail(
        "the missing skill path is not named", result
    )
    assert "AGENT_QCC_SKILLS_DIR" in combined, _fail(
        "the setting that decides the path is not named", result
    )
    assert "Traceback" not in combined, _fail(
        "the user is shown a traceback instead of a message", result
    )


def test_guard_agent_qcc_cli_skills_reports_a_missing_directory():
    """``agent-qcc skills --dir`` on a missing path prints a message, not a traceback."""
    result = _run(["-m", "agent_qcc.cli", "skills", "--dir", "no/such/skills"])

    assert result.returncode == 1, _fail("a missing skills dir must exit non-zero", result)
    combined = result.stdout + result.stderr
    assert "AGENT_QCC_SKILLS_DIR" in combined, _fail(
        "the setting that decides the path is not named", result
    )
    assert "Traceback" not in combined, _fail(
        "the user is shown a traceback instead of a message", result
    )
