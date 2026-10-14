import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from urllib.parse import urlsplit


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    environment: Literal["development", "test"] = "development"
    database_path: Path | None = None
    model_provider: Literal["mock", "openai-compatible"] = "mock"
    endpoint: str | None = Field(default=None, repr=False)
    model: str | None = Field(default=None, max_length=120)
    api_key: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    provider_json_mode: bool = True
    provider_max_tokens_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    provider_thinking_mode: Literal["enabled", "disabled"] | None = None
    knowledge_backend: Literal["fts5", "demo"] = "fts5"
    knowledge_top_k: int = Field(default=3, ge=1, le=5)
    seed_demo: bool = True
    model_timeout: float = Field(default=2, gt=0, le=30)
    context_timeout: float = Field(default=1, gt=0, le=10)
    run_timeout: float = Field(default=15, gt=0, le=240)
    max_model_calls: int = Field(default=6, ge=0, le=6)
    orchestration: Literal["sequential", "dynamic"] = "sequential"
    max_parallel_agents: int = Field(default=2, ge=1, le=2)
    max_task_executions: int = Field(default=16, ge=0, le=24)
    max_turn_token_reservation: int = Field(default=131072, ge=0, le=524288)
    history_messages: int = Field(default=12, ge=0, le=30)
    stream_chunk_chars: int = Field(default=28, ge=1, le=200)
    token_lifetime_seconds: int = Field(default=3600, ge=60, le=86400)

    @model_validator(mode="after")
    def isolated_database(self):
        if self.environment == "test" and self.database_path is None:
            raise ValueError("Test applications require an explicit isolated database_path")
        if self.environment == "test" and "development" in self.database_path.parts:
            raise ValueError("Tests cannot use the development database")
        if self.model_provider == "openai-compatible":
            if not self.endpoint or not self.model or not self.api_key.get_secret_value():
                raise ValueError("Provider requires CAMPUSBRIDGE_ENDPOINT / MODEL / API_KEY")
            parts = urlsplit(self.endpoint)
            if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
                    or parts.query or parts.fragment):
                raise ValueError("Endpoint must be an HTTP(S) base URL without credentials, query or fragment")
            if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Remote providers require HTTPS")
        return self

    @property
    def db_file(self):
        return self.database_path or Path("data/development/campusbridge.sqlite3")

    @classmethod
    def from_environment(cls):
        fields = {"environment": "CAMPUSBRIDGE_ENV", "database_path": "CAMPUSBRIDGE_DATABASE_PATH",
                  "model_provider": "CAMPUSBRIDGE_MODEL_PROVIDER", "seed_demo": "CAMPUSBRIDGE_SEED_DEMO",
                  "endpoint": "CAMPUSBRIDGE_ENDPOINT", "model": "CAMPUSBRIDGE_MODEL", "api_key": "CAMPUSBRIDGE_API_KEY",
                  "provider_json_mode": "CAMPUSBRIDGE_JSON_MODE", "model_timeout": "CAMPUSBRIDGE_MODEL_TIMEOUT",
                  "run_timeout": "CAMPUSBRIDGE_RUN_TIMEOUT", "knowledge_backend": "CAMPUSBRIDGE_KNOWLEDGE_BACKEND",
                  "provider_max_tokens_parameter": "CAMPUSBRIDGE_MAX_TOKENS_PARAMETER",
                  "provider_thinking_mode": "CAMPUSBRIDGE_THINKING_MODE",
                  "orchestration": "CAMPUSBRIDGE_ORCHESTRATION",
                  "max_parallel_agents": "CAMPUSBRIDGE_MAX_PARALLEL_AGENTS",
                  "max_task_executions": "CAMPUSBRIDGE_MAX_TASK_EXECUTIONS",
                  "max_turn_token_reservation": "CAMPUSBRIDGE_MAX_TURN_TOKEN_RESERVATION"}
        values = {key: os.environ[name] for key, name in fields.items() if name in os.environ}
        if values.get("model_provider") == "openai-compatible":
            values.setdefault("model_timeout", 20)
            values.setdefault("run_timeout", 90)
        return cls(**values)
