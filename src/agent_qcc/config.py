"""Runtime settings loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_DIR = REPO_ROOT / "skills"


class Settings(BaseSettings):
    """All knobs are optional so the agent runs offline by default."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_QCC_",
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM (any OpenAI-compatible endpoint) ---------------------------------
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    model_name: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    llm_timeout: float = 60.0

    # --- Qichacha connector ---------------------------------------------------
    qcc_mcp_url: str = "https://agent.qcc.com/mcp/company/stream"
    qcc_skill_name: str = "qcc-super"  # 默认使用超级智能体 skill（169 工具 + 11 MCP server + DuckDB + Semantica）
    skills_dir: Path = DEFAULT_SKILLS_DIR
    qcc_timeout: float = 45.0
    qcc_auth_token: str | None = None
    qcc_auth_header: str = "Authorization"
    qcc_auth_scheme: str = "Bearer"
    qcc_retries: int = 2
    qcc_backoff: float = 0.8

    # --- Pipeline ------------------------------------------------------------
    max_reflect_retries: int = 1
    use_llm: bool = True
    offline: bool = True
    checkpointer: str = ""


def get_settings() -> Settings:
    return Settings()
