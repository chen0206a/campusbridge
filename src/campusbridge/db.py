from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
import threading

from sqlalchemy import Boolean, CheckConstraint, Double, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, create_engine, event, select, text
from campusbridge.exact_json import ExactJSON
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))
    campus_id: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginToken(Base):
    __tablename__ = "login_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    expires_at: Mapped[float] = mapped_column(Double)


class Student(Base):
    __tablename__ = "students"
    id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("student_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    operation_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    input_text: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default="QUEUED")
    candidate: Mapped[dict | None] = mapped_column(ExactJSON, nullable=True)
    approval: Mapped[dict | None] = mapped_column(ExactJSON, nullable=True)
    response_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class ModelCall(Base):
    __tablename__ = "model_calls"
    __table_args__ = (UniqueConstraint("run_id", "call_index"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    call_index: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="STARTED")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_ms: Mapped[float | None] = mapped_column(Double, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    output_valid: Mapped[bool | None]
    finish_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("document_id", "campus_id", "revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(100))
    campus_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(160))
    revision: Mapped[str] = mapped_column(String(80))
    license: Mapped[str] = mapped_column(String(40))
    author: Mapped[str] = mapped_column(String(160))
    origin: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    source_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    document_key: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("run_id", "role"), UniqueConstraint("conversation_id", "order_index", name="uq_messages_conversation_order"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event: Mapped[dict] = mapped_column(ExactJSON)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), unique=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    assessment: Mapped[dict] = mapped_column(ExactJSON)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class SupportCase(Base):
    __tablename__ = "support_cases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    risk_event_id: Mapped[str] = mapped_column(ForeignKey("risk_events.id"), unique=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"))
    campus_id: Mapped[str] = mapped_column(String(40), index=True)
    state: Mapped[str] = mapped_column(String(30), default="OPEN")
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class CaseEvent(Base):
    __tablename__ = "case_events"
    __table_args__ = (UniqueConstraint("case_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("support_cases.id"), index=True)
    operation_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    action: Mapped[str] = mapped_column(String(30))
    from_state: Mapped[str] = mapped_column(String(30))
    to_state: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text)
    result_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


KNOWLEDGE_TABLES = {"knowledge_documents", "knowledge_chunks"}
Index("ix_support_cases_campus_state_created", SupportCase.campus_id, SupportCase.state, SupportCase.created_at)
Index("ix_agent_runs_state_created", AgentRun.state, AgentRun.created_at)
for _table in Base.metadata.tables.values():
    _table.dialect_options["mysql"].update(engine="InnoDB", charset="utf8mb4", collate="utf8mb4_0900_bin")
for _table, _column in ((AgentRun.__table__,"candidate"),(AgentRun.__table__,"approval"),(RunEvent.__table__,"event"),(RiskEvent.__table__,"assessment")):
    _table.append_constraint(CheckConstraint(f"{_column} IS NULL OR JSON_VALID({_column})",
        name="ck_"+_table.name+"_"+_column+"_json").ddl_if(dialect="mysql"))


class RuntimeOwnershipError(RuntimeError):
    pass


class Database:
    """Business connections, short transactions, and an explicit SQLite compatibility path.

    Database(path) remains the combined lightweight fixture/RAG index constructor.
    Applications use business(...), which requires Alembic and never creates FTS tables.
    """
    def __init__(self, path=None, *, url=None, scope="combined", schema="create", pool_size=10,
                 max_overflow=10, pool_timeout=5, pool_recycle=1800, connect_timeout=5, io_timeout=10):
        if scope not in {"combined", "business", "knowledge"}:
            raise ValueError("INVALID_DATABASE_SCOPE")
        if url is None:
            path = Path(path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            from sqlalchemy.engine import URL
            url = URL.create("sqlite", database=path.as_posix())
        else:
            url = make_url(url)
        if url.drivername not in {"sqlite", "mysql+pymysql"}:
            raise ValueError("DATABASE_DRIVER_UNSUPPORTED")
        self.is_mysql = url.drivername == "mysql+pymysql"
        if self.is_mysql and (scope != "business" or schema == "create"):
            raise ValueError("MYSQL_REQUIRES_BUSINESS_ALEMBIC_SCHEMA")
        if self.is_mysql:
            if not url.database or url.query.get("charset", "utf8mb4") != "utf8mb4":
                raise ValueError("MYSQL_DATABASE_AND_UTF8MB4_REQUIRED")
            url = url.update_query_dict({"charset": "utf8mb4"})
            options = dict(pool_size=pool_size, max_overflow=max_overflow, pool_timeout=pool_timeout,
                pool_recycle=pool_recycle, pool_pre_ping=True, isolation_level="READ COMMITTED",
                connect_args={"connect_timeout": connect_timeout, "read_timeout": io_timeout, "write_timeout": io_timeout})
        else:
            options = dict(connect_args={"check_same_thread": False, "timeout": 5})
        self.engine = create_engine(url, hide_parameters=True, **options)
        self.owner_connection = self.owner_engine = self.owner_file = None
        self.owner_guard = threading.Lock()
        self.runtime_owner_required = False
        if not self.is_mysql:
            @event.listens_for(self.engine, "connect")
            def constraints(connection, record): connection.execute("PRAGMA foreign_keys=ON")
        else:
            @event.listens_for(self.engine, "connect")
            def bounded_locks(connection, record):
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION innodb_lock_wait_timeout=5")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        try:
            if schema == "create":
                tables = [t for t in Base.metadata.sorted_tables if scope == "combined" or
                          (t.name in KNOWLEDGE_TABLES) == (scope == "knowledge")]
                Base.metadata.create_all(self.engine, tables=tables)
            elif schema in {"upgrade", "verify"}:
                from campusbridge.schema import migrate, verify_schema
                if schema == "upgrade": migrate(self.engine)
                verify_schema(self.engine)
            elif schema != "none":
                raise ValueError("INVALID_SCHEMA_MODE")
        except BaseException:
            self.close()
            raise

    @classmethod
    def business(cls, settings):
        raw = settings.database_url.get_secret_value() if settings.database_url else None
        mysql = bool(raw and make_url(raw).drivername == "mysql+pymysql")
        database = cls(settings.db_file, url=raw, scope="business",
            schema="verify" if mysql else "upgrade", pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow, pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle, connect_timeout=settings.db_connect_timeout,
            io_timeout=settings.db_io_timeout)
        if not mysql:
            from sqlalchemy import inspect
            if inspect(database.engine).has_table("knowledge_documents"):
                from campusbridge.data_migration import verify_legacy_knowledge_split
                try: verify_legacy_knowledge_split(database.engine, settings.knowledge_file)
                except BaseException:
                    database.close()
                    raise
        return database

    @contextmanager
    def write(self):
        self.assert_runtime_owner()
        with self.sessions.begin() as session:
            if not self.is_mysql: session.execute(text("BEGIN IMMEDIATE"))
            yield session

    def lock(self, session, model, identity):
        return session.scalar(select(model).where(model.id == identity).with_for_update().execution_options(populate_existing=True))

    def acquire_runtime_owner(self, *, enforce=False):
        if enforce: self.runtime_owner_required = True
        if not self.is_mysql:
            import os
            stream = open(self.engine.url.database + ".runtime.lock", "a+b")
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    if not stream.read(1): stream.write(b"1"); stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                stream.close()
                raise RuntimeOwnershipError("SINGLE_RUNTIME_OWNER_REQUIRED") from None
            self.owner_file = stream
            return
        from campusbridge.contracts import digest
        # One extra dedicated connection, independent of request-pool exhaustion.
        self.owner_name = "campusbridge:" + digest(self.engine.url.database)[:40]
        self.owner_engine = create_engine(self.engine.url, poolclass=NullPool, hide_parameters=True,
            connect_args={"connect_timeout": 5, "read_timeout": 5, "write_timeout": 5})
        try:
            self.owner_connection = self.owner_engine.connect()
            acquired = self.owner_connection.scalar(text("SELECT GET_LOCK(:name,0)"), {"name": self.owner_name})
            if acquired != 1: raise RuntimeOwnershipError("SINGLE_RUNTIME_OWNER_REQUIRED")
            self.owner_connection.commit()
        except BaseException:
            self.release_runtime_owner()
            raise

    def assert_runtime_owner(self):
        if self.runtime_owner_required and self.owner_connection is None and self.owner_file is None:
            raise RuntimeOwnershipError("RUNTIME_OWNERSHIP_LOST")
        if self.owner_connection is None: return
        with self.owner_guard:
            try:
                alive = self.owner_connection.scalar(text("SELECT IS_USED_LOCK(:name)=CONNECTION_ID()"), {"name": self.owner_name})
                self.owner_connection.commit()
                if alive != 1: raise RuntimeOwnershipError("RUNTIME_OWNERSHIP_LOST")
            except Exception:
                raise RuntimeOwnershipError("RUNTIME_OWNERSHIP_LOST") from None

    def release_runtime_owner(self):
        if self.owner_file is not None:
            self.owner_file.close()
            self.owner_file = None
        if self.owner_connection is not None:
            self.owner_connection.close()
            self.owner_connection = None
        if self.owner_engine is not None:
            self.owner_engine.dispose()
            self.owner_engine = None

    def close(self):
        self.release_runtime_owner()
        self.engine.dispose()


class KnowledgeDatabase(Database):
    def __init__(self, path):
        super().__init__(path, scope="knowledge")
