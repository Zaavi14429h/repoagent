from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from REPOAGENT_* environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="REPOAGENT_",
        extra="ignore",
    )

    environment: str = "development"
    database_path: Path = Path(".repoagent/repoagent.db")
    workspace_root: Path = Path(".repoagent/workspaces")
    max_corrections: int = Field(default=3, ge=0, le=10)

    agent_backend: Literal["crewai", "deterministic"] = "crewai"
    model: str = "openai/gpt-4.1-mini"
    model_api_key: str | None = None
    model_base_url: str | None = None
    model_input_cost_per_million: float = Field(default=0.0, ge=0)
    model_output_cost_per_million: float = Field(default=0.0, ge=0)

    adk_model: str = "gemini-2.5-flash"
    adk_a2a_url: str = "http://localhost:8100"

    sandbox_mode: Literal["docker", "local"] = "docker"
    python_runner_image: str = "repoagent-runner-python:latest"
    node_runner_image: str = "repoagent-runner-node:latest"
    command_timeout_seconds: int = Field(default=300, ge=5, le=1800)
    max_output_chars: int = Field(default=50_000, ge=1_000, le=1_000_000)

    github_token: str | None = None
    langfuse_enabled: bool = False

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
