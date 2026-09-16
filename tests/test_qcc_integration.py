"""Integration tests for the 企查查 connector's credential handling.

Companion to `test_qcc.py` (which unit-tests `QccClient`'s pure helpers).  These
tests cross two modules — `config.Settings` -> `qcc.QccClient` -> the skill's
transport descriptor — and pin what actually happens with credentials, including
the two defects documented below.

Credential values are passed explicitly rather than through a written ``.env``,
because ``pydantic-settings`` gives environment variables priority over dotenv
files; ``conftest.py`` pins the credential env vars to empty strings so a
developer's local ``.env`` cannot influence these results.  One test deliberately
reads a real dotenv file to prove the other half of that interaction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_qcc.config import Settings
from agent_qcc.qcc import QccClient
from agent_qcc.skill import load_skill

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SKILL_NAME = "qcc-company"
DEFAULT_QCC_URL = "https://agent.qcc.com/mcp/company/stream"


def make_settings(**overrides: object) -> Settings:
    """Settings with no LLM and offline by default; credentials only if asked for."""
    overrides.setdefault("offline", True)
    overrides.setdefault("use_llm", False)
    return Settings(**overrides)


def headers_of(settings: Settings) -> dict[str, str]:
    return QccClient(load_skill(SKILLS_DIR, SKILL_NAME), settings)._headers()


@pytest.fixture()
def skill():
    return load_skill(SKILLS_DIR, SKILL_NAME)


# ---------------------------------------------------------------------------
# credential -> header contract
# ---------------------------------------------------------------------------


def test_no_token_configured_means_no_auth_header():
    """No credential -> no ``Authorization`` header, and no session id."""
    settings = make_settings()
    assert settings.qcc_auth_token is None
    headers = headers_of(settings)
    assert "Authorization" not in headers
    assert "Mcp-Session-Id" not in headers
    assert headers["Accept"] == "application/json, text/event-stream"


def test_token_is_attached_as_a_bearer_header():
    settings = make_settings(qcc_auth_token="test-token")
    assert settings.qcc_auth_token == "test-token"
    assert headers_of(settings)["Authorization"] == "Bearer test-token"


def test_custom_auth_header_redirects_the_credential():
    """`qcc_auth_header` / `qcc_auth_scheme` move and reshape the credential."""
    settings = make_settings(qcc_auth_token="key-123", qcc_auth_header="X-API-Key", qcc_auth_scheme="")
    headers = headers_of(settings)
    assert headers["X-API-Key"] == "key-123"
    assert "Authorization" not in headers


def test_missing_env_file_falls_back_to_defaults():
    """No dotenv source at all -> defaults, still no credentials."""
    settings = make_settings(_env_file=str(Path(__file__).resolve().parent / "no_such_env"))
    assert settings.qcc_auth_token is None
    assert settings.qcc_mcp_url == DEFAULT_QCC_URL
    assert settings.qcc_retries == 2


# ---------------------------------------------------------------------------
# defect: offline is not a credential gate
# ---------------------------------------------------------------------------


def test_offline_mode_still_loads_credentials_from_the_dotenv_file(tmp_path):
    """DEFECT (product bug, not fixed here): ``offline`` is not a credential gate.

    ``Settings(offline=True)`` still parses ``.env`` and still carries a live
    token, and ``QccClient._headers()`` therefore emits an ``Authorization``
    header for a client that will never make a request.

    The risk: "offline" is only enforced inside ``QccClient.call``.  Every other
    reader of ``settings`` (a logger, a metric tag, a future caller of
    ``_headers()``) sees credentials it should not have, and flipping
    ``AGENT_QCC_OFFLINE`` in ``.env`` is the *entire* difference between "no
    network" and "authenticated against production".  Every ``Settings(...)``
    construction also re-parses the dotenv file from disk.

    Reproduce: ``Settings(offline=True).qcc_auth_token`` — non-empty whenever
    ``.env`` carries a token, including CI images built from this repo.

    `test_qcc.py::test_auth_headers_not_set_when_no_token` already assumed this
    was false, which is why it fails on machines with a real ``.env``.  This
    test proves it with a synthetic env file and pins the actual behaviour so
    the discrepancy cannot be missed again; fixing the settings layer changes
    production behaviour and belongs in a separate change.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_QCC_QCC_AUTH_TOKEN=committed-token\n", encoding="utf-8")
    settings = make_settings(_env_file=str(env_file))

    assert settings.offline is True  # the flag says "no network"...
    assert settings.qcc_auth_token == "committed-token"  # ...but the credential loaded
    assert headers_of(settings)["Authorization"] == "Bearer committed-token"  # and was attached


def test_environment_value_outranks_the_dotenv_file(tmp_path):
    """Same defect, other face: ``offline=True`` cannot clear a credential from env.

    Environment variables win over the dotenv file in ``pydantic-settings``, so
    a token exported in the shell (or baked into an image) survives every
    ``offline=True`` request.  ``config.Settings`` has no path from ``offline``
    to credential suppression at all — the flag is transport-only.

    Reproduce: export ``AGENT_QCC_QCC_AUTH_TOKEN``, then read
    ``Settings(offline=True).qcc_auth_token`` — it is still set.

    Pinned as-is: asserting the intended behaviour here would hide the gap.
    """
    import os

    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_QCC_QCC_AUTH_TOKEN=from-file\n", encoding="utf-8")
    os.environ["AGENT_QCC_QCC_AUTH_TOKEN"] = "from-environment"
    try:
        settings = make_settings(_env_file=str(env_file))
    finally:
        os.environ["AGENT_QCC_QCC_AUTH_TOKEN"] = ""  # restore the session baseline

    assert settings.offline is True
    assert settings.qcc_auth_token == "from-environment"
    assert headers_of(settings)["Authorization"] == "Bearer from-environment"


# ---------------------------------------------------------------------------
# transport selection
# ---------------------------------------------------------------------------


def test_offline_client_never_constructs_a_transport():
    """Offline mode validates locally; no ``httpx`` client is created."""
    client = QccClient(load_skill(SKILLS_DIR, SKILL_NAME), make_settings())
    assert client._client is None
    assert client.url == DEFAULT_QCC_URL
    assert client.transport == "streamableHttp"


def test_url_and_transport_come_from_the_skill_descriptor():
    """The endpoint is read from ``skills/<name>/mcp.json``, not from settings."""
    skill = load_skill(SKILLS_DIR, SKILL_NAME)
    client = QccClient(skill, make_settings())
    assert client.url == skill.transport.get("url")
    assert client.transport == skill.transport.get("type", "streamableHttp")


def test_endpoint_comes_only_from_the_skill_descriptor(skill):
    """DEFECT (product bug, not fixed here): ``AGENT_QCC_QCC_MCP_URL`` is ignored.

    ``Settings.qcc_mcp_url`` exists and is documented in ``.env.example`` as the
    connector endpoint, but ``QccClient`` never reads it — it resolves the URL
    from ``skills/<name>/mcp.json`` only
    (``QccClient.url`` returns ``self.skill.transport.get("url")``).

    Consequences:
    * the documented config knob does nothing, so routing to a staging or
      self-hosted endpoint requires editing the skill's ``mcp.json``;
    * while the committed skill is installed, ``settings.qcc_mcp_url`` can never
      change which endpoint is contacted — and it is not the way to disable the
      connector either.  The "connector 未配置 URL" degradation branch only
      triggers for a skill whose ``mcp.json`` omits the URL, which is a narrow,
      hard-to-hit case (see ``test_missing_connector_url_degrades_to_a_tool_result``).

    Reproduce: ``Settings(offline=False, qcc_mcp_url="")`` still yields
    ``QccClient.url == "https://agent.qcc.com/mcp/company/stream"``.

    Pinned as-is so the dead config knob cannot be "fixed" by editing a test
    into looking like it works.
    """
    settings = make_settings(offline=False, qcc_mcp_url="")
    client = QccClient(skill, settings)
    assert settings.qcc_mcp_url == ""  # the knob was set...
    assert client.url == DEFAULT_QCC_URL  # ...and ignored; the skill descriptor wins


def test_missing_connector_url_degrades_to_a_tool_result():
    """The "connector not configured" branch: a `ToolResult`, never an exception."""
    import asyncio

    from agent_qcc.skill import Skill

    skill = Skill(
        name=SKILL_NAME,
        directory=SKILLS_DIR / SKILL_NAME,
        display_name="企查查",
        description="",
        transport={"type": "streamableHttp"},  # no url key at all
        tools=(),
    )
    client = QccClient(skill, make_settings(offline=False))
    assert client.url is None

    result = asyncio.run(client.call("get_company_profile", {"name": "测试公司"}))
    assert result.ok is False
    assert "未配置 URL" in result.error
    evidence = result.to_evidence()
    assert evidence["tool"] == "get_company_profile"
    assert evidence["target"] == "测试公司"


def test_online_client_with_a_url_but_no_token_is_unauthenticated(skill):
    """An endpoint alone must not imply credentials."""
    client = QccClient(skill, make_settings(offline=False))
    assert client.url == DEFAULT_QCC_URL
    headers = client._headers()
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"
