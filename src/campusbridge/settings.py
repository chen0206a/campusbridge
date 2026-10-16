import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from urllib.parse import urlsplit


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    environment: Literal["development", "test"] = "development"
    performance_enabled: bool = False
    trusted_proxy_host: str | None = Field(default=None, pattern=r"^[a-zA-Z][a-zA-Z0-9.-]{0,62}$")
    database_path: Path | None = None
    database_url: SecretStr | None = Field(default=None, repr=False, exclude=True)
    knowledge_database_path: Path | None = None
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_pool_timeout: int = Field(default=5, ge=1, le=30)
    db_pool_recycle: int = Field(default=1800, ge=30, le=3600)
    db_connect_timeout: int = Field(default=5, ge=1, le=30)
    db_io_timeout: int = Field(default=10, ge=6, le=60)
    model_provider: Literal["mock", "openai-compatible"] = "mock"
    endpoint: str | None = Field(default=None, repr=False)
    model: str | None = Field(default=None, max_length=120)
    api_key: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    provider_json_mode: bool = True
    provider_max_tokens_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    provider_thinking_mode: Literal["enabled", "disabled"] | None = None
    knowledge_backend: Literal["fts5", "demo"] = "fts5"
    knowledge_top_k: int = Field(default=3, ge=1, le=5)
    retrieval_mode: Literal["bm25", "dense", "hybrid"] = "bm25"
    retrieval_fallback: Literal["error", "bm25"] = "error"
    retrieval_candidate_k: int = Field(default=20, ge=5, le=40)
    dense_min_score: float = Field(default=0.65, ge=-1, le=1)
    embedding_model_path: Path = Path("models/bge-small-zh-v1.5")
    embedding_threads: int = Field(default=2, ge=1, le=16)
    redis_cache_enabled: bool = False
    redis_rate_limit_enabled: bool = False
    redis_url: SecretStr = Field(default=SecretStr("redis://127.0.0.1:6379/0"), repr=False, exclude=True)
    redis_namespace: str = Field(default="campusbridge:d2", pattern=r"^[A-Za-z0-9:_-]{1,64}$")
    redis_timeout: float = Field(default=0.15, ge=0.01, le=2)
    redis_max_connections: int = Field(default=32, ge=1, le=128)
    redis_cache_ttl: int = Field(default=120, ge=1, le=3600)
    login_limit: int = Field(default=10, ge=1, le=1000)
    login_peer_limit: int = Field(default=100, ge=1, le=10000)
    login_window_seconds: int = Field(default=60, ge=1, le=3600)
    message_limit: int = Field(default=20, ge=1, le=1000)
    message_window_seconds: int = Field(default=60, ge=1, le=3600)
    rate_limit_status: Literal[429, 503] = 429
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
        redis_parts = urlsplit(self.redis_url.get_secret_value())
        if (redis_parts.scheme not in {"redis", "rediss"} or not redis_parts.hostname
                or redis_parts.query or redis_parts.fragment):
            raise ValueError("REDIS_URL_REQUIRES_REDIS_OR_REDISS_WITHOUT_QUERY")
        if self.redis_cache_enabled and self.redis_timeout * 2 >= self.context_timeout:
            raise ValueError("REDIS_CACHE_TIMEOUT_MUST_LEAVE_CONTEXT_BUDGET")
        if self.database_url is not None and not self.database_url.get_secret_value():
            raise ValueError("DATABASE_URL_MUST_NOT_BE_EMPTY")
        if self.database_url:
            from sqlalchemy.engine import make_url
            try: url = make_url(self.database_url.get_secret_value())
            except Exception: raise ValueError("DATABASE_URL_INVALID") from None
            if url.drivername != "mysql+pymysql" or not url.database or not url.username:
                raise ValueError("DATABASE_URL_REQUIRES_MYSQL_PYMYSQL_DATABASE_USER")
            if self.environment == "test" and (not url.database.startswith("cb_d1_test_") or not self.knowledge_database_path):
                raise ValueError("TEST_MYSQL_REQUIRES_ISOLATED_NAME_AND_KNOWLEDGE_PATH")
        if self.environment == "test" and self.database_path is None and self.database_url is None:
            raise ValueError("Test applications require an explicit isolated database_path")
        if self.environment == "test" and self.database_path and "development" in self.database_path.parts:
            raise ValueError("Tests cannot use the development database")
        if self.environment == "test" and self.knowledge_database_path and "development" in self.knowledge_database_path.parts:
            raise ValueError("Tests cannot use development knowledge")
        if self.knowledge_file.resolve() == self.db_file.resolve():
            raise ValueError("BUSINESS_AND_KNOWLEDGE_DATABASES_MUST_BE_SEPARATE")
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

    @property
    def knowledge_file(self):
        return self.knowledge_database_path or self.db_file.with_name(self.db_file.stem + ".knowledge.sqlite3")

    @classmethod
    def from_environment(cls):
        fields = {"environment": "CAMPUSBRIDGE_ENV", "database_path": "CAMPUSBRIDGE_DATABASE_PATH",
                  "trusted_proxy_host": "CAMPUSBRIDGE_TRUSTED_PROXY_HOST",
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
        fields.update({"retrieval_mode": "CAMPUSBRIDGE_RETRIEVAL_MODE",
            "retrieval_fallback": "CAMPUSBRIDGE_RETRIEVAL_FALLBACK",
            "retrieval_candidate_k": "CAMPUSBRIDGE_RETRIEVAL_CANDIDATE_K",
            "dense_min_score": "CAMPUSBRIDGE_DENSE_MIN_SCORE",
            "embedding_model_path": "CAMPUSBRIDGE_EMBEDDING_MODEL_PATH",
            "embedding_threads": "CAMPUSBRIDGE_EMBEDDING_THREADS"})
        fields.update({"database_url": "CAMPUSBRIDGE_DATABASE_URL", "knowledge_database_path": "CAMPUSBRIDGE_KNOWLEDGE_DATABASE_PATH",
            "performance_enabled": "CAMPUSBRIDGE_PERFORMANCE_ENABLED",
            "db_pool_size": "CAMPUSBRIDGE_DB_POOL_SIZE", "db_max_overflow": "CAMPUSBRIDGE_DB_MAX_OVERFLOW",
            "db_pool_timeout": "CAMPUSBRIDGE_DB_POOL_TIMEOUT", "db_pool_recycle": "CAMPUSBRIDGE_DB_POOL_RECYCLE",
            "db_connect_timeout": "CAMPUSBRIDGE_DB_CONNECT_TIMEOUT", "db_io_timeout": "CAMPUSBRIDGE_DB_IO_TIMEOUT"})
        values = {key: os.environ[name] for key, name in fields.items() if name in os.environ}
        for key in ("redis_cache_enabled", "redis_rate_limit_enabled", "redis_url", "redis_namespace", "redis_timeout",
                    "redis_max_connections", "redis_cache_ttl", "login_limit", "login_peer_limit", "login_window_seconds",
                    "message_limit", "message_window_seconds", "rate_limit_status"):
            name = "CAMPUSBRIDGE_" + key.upper()
            if name in os.environ:
                values[key] = int(os.environ[name]) if key == "rate_limit_status" else os.environ[name]
        if values.get("model_provider") == "openai-compatible":
            values.setdefault("model_timeout", 20)
            values.setdefault("run_timeout", 90)
        return cls(**values)
