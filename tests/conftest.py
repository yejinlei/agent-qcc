"""Shared pytest configuration.

``Settings`` reads the repo ``.env`` unconditionally
(``model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"))``), so a
developer's local credentials — the live 企查查 token, the OpenAI key, the local
model name and a private base URL — leak into every test that builds
``Settings``.  A CI image has no ``.env``, which is why credential assertions
pass in CI and fail on laptops.  The session fixture below points
``model_config["env_file"]`` at a file that does not exist, so the suite always
starts from the declared defaults.  Tests that need a specific credential pass
it explicitly (``Settings(qcc_auth_token="...")``) or read a synthetic file
via ``_env_file=``.  Production code is untouched.

There used to be a second fixture here: ``_compat_langgraph_graph_graph()``
injected a ``langgraph.graph.graph`` module into ``sys.modules`` so that
``src/agent_qcc/graph.py`` could be imported under langgraph >= 1.0, where that
submodule has been removed.  It is gone now — the import is fixed in
``graph.py`` (YEJ-165) — and the shim must not come back: it made this suite
green while every real entry point (``agent-qcc chat``, ``agent-qcc tui``,
``langgraph serve``, a bare ``import agent_qcc``) died at import with
``ModuleNotFoundError``.  Import regressions are pinned by
``tests/test_import_sanity.py``, which exercises the imports in a fresh
interpreter this conftest cannot reach.
"""

import os
import sys
from pathlib import Path

import pytest

# Ensure src/ is on the path so `import agent_qcc` works without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SESSION_BASELINE_ENV_FILE = Path(__file__).resolve().parent / "no-such-env-file"


@pytest.fixture(scope="session", autouse=True)
def _isolate_dotenv_source():
    """Start every test session from the declared defaults, not a local ``.env``.

    Environment variables outrank the dotenv file in ``pydantic-settings``, so
    this clears the credential keys that may already be in the shell *and*
    removes the dotenv source, which otherwise wins for values the shell does
    not carry.  Both directions are needed: a token can arrive either way.

    ``model_config`` is a plain dict and is read at instantiation time, so
    mutating it here affects every ``Settings(...)`` built afterwards; the
    original value is restored on teardown, so running the tests does not change
    the module the way it would for production code.
    """
    import agent_qcc.config as config_module

    saved_env_file = config_module.Settings.model_config.get("env_file")
    saved_env = {key: os.environ.get(key) for key in ("AGENT_QCC_QCC_AUTH_TOKEN", "AGENT_QCC_OPENAI_API_KEY")}
    config_module.Settings.model_config["env_file"] = str(SESSION_BASELINE_ENV_FILE)
    for key in saved_env:
        if saved_env[key] is None:
            os.environ.pop(key, None)

    yield

    config_module.Settings.model_config["env_file"] = saved_env_file
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
