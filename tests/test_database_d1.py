import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError

from campusbridge.api import create_app
from campusbridge.contracts import AgentName, Event, Identity, RiskOutput, RunState, StateConflict
from campusbridge.data_migration import split_knowledge
from campusbridge.db import Account, AgentRun, Database, KnowledgeDatabase, Message, RuntimeOwnershipError
from campusbridge.retrieval import SQLiteKnowledge, bundled_documents
from campusbridge.schema import migrate, verify_schema
from campusbridge.schema_v1 import metadata
from campusbridge.settings import Settings


def test_fresh_business_migrations_and_drift_detection(tmp_path):
    db = Database(tmp_path / "business.sqlite3", scope="business", schema="upgrade")
    try:
        assert verify_schema(db.engine)["revision"] == "d1_0003"
        assert "knowledge_documents" not in inspect(db.engine).get_table_names()
        migrate(db.engine)
        with db.engine.begin() as connection: connection.exec_driver_sql("DROP INDEX uq_messages_conversation_order")
        with pytest.raises(RuntimeError, match="UNIQUE_FENCE_MISSING"): verify_schema(db.engine)
    finally: db.close()


def test_explicit_legacy_adoption_and_no_implicit_stamp(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "legacy.sqlite3").as_posix())
    try:
        metadata().create_all(engine)
        with pytest.raises(RuntimeError, match="EXPLICIT_ADOPTION"): migrate(engine)
        assert "alembic_version" not in inspect(engine).get_table_names()
        migrate(engine, adopt_legacy=True)
        assert verify_schema(engine)["verified"]
    finally: engine.dispose()


def test_unknown_legacy_structure_refused_without_stamp(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "legacy.sqlite3").as_posix())
    try:
        metadata().create_all(engine)
        with engine.begin() as connection: connection.exec_driver_sql("ALTER TABLE accounts ADD COLUMN unexpected TEXT")
        with pytest.raises(RuntimeError, match="COLUMNS_MISMATCH"): migrate(engine, adopt_legacy=True)
        assert "alembic_version" not in inspect(engine).get_table_names()
    finally: engine.dispose()


def test_separate_application_business_and_knowledge_stores(tmp_path):
    from fastapi.testclient import TestClient
    app = create_app(Settings(environment="test", database_path=tmp_path / "business.sqlite3"))
    with TestClient(app):
        assert "accounts" in inspect(app.state.database.engine).get_table_names()
        assert "knowledge_documents" not in inspect(app.state.database.engine).get_table_names()
        assert "knowledge_fts" in inspect(app.state.knowledge_database.engine).get_table_names()
        assert "accounts" not in inspect(app.state.knowledge_database.engine).get_table_names()


def test_knowledge_split_preserves_all_versions_hashes_and_original_bytes(tmp_path):
    import hashlib
    source, target = tmp_path / "legacy.sqlite3", tmp_path / "knowledge.sqlite3"
    original = Database(source)
    knowledge = SQLiteKnowledge(original)
    knowledge.import_documents(bundled_documents())
    actor = Identity(account_id="synthetic", role="student", campus_id="fictional-campus-a")
    old = asyncio.run(knowledge.search("图书馆预约", actor.campus_id))[0]
    revised = bundled_documents()[0].model_copy(update={"revision": "d1-new", "text": "图书馆预约新版登记资料。"})
    knowledge.import_documents([revised])
    original.close()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = split_knowledge(source, target)
    assert result["documents"] == 9 and result["chunks"] == 9
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    db = KnowledgeDatabase(target)
    try:
        current = SQLiteKnowledge(db)
        assert current.source(old.source_id, actor)["active_revision"] is False
        assert current.source(old.source_id, actor)["source_hash"] == old.source_hash
        hits = asyncio.run(current.search("图书馆预约", actor.campus_id))
        assert hits[0].revision == "d1-new"
        assert not asyncio.run(current.search("图书馆预约", "different-campus"))
        assert "accounts" not in inspect(db.engine).get_table_names()
    finally: db.close()
    with pytest.raises(ValueError, match="MUST_BE_NEW"): split_knowledge(source, target)


def test_single_sqlite_runtime_guard_and_release(tmp_path):
    one = Database(tmp_path / "owner.sqlite3")
    two = Database(tmp_path / "owner.sqlite3")
    try:
        one.acquire_runtime_owner()
        with pytest.raises(RuntimeOwnershipError): two.acquire_runtime_owner()
        with pytest.raises(RuntimeOwnershipError): split_knowledge(tmp_path / "owner.sqlite3", tmp_path / "copy.sqlite3")
        one.release_runtime_owner()
        two.acquire_runtime_owner()
    finally: one.close(); two.close()


def test_mysql_lock_compilation_and_opaque_key_collation():
    statement = select(AgentRun).where(AgentRun.id == "x").with_for_update()
    assert "FOR UPDATE" in str(statement.compile(dialect=mysql.dialect()))
    assert "BEGIN IMMEDIATE" not in str(statement.compile(dialect=mysql.dialect()))
    table = metadata().tables["agent_runs"]
    assert table.dialect_options["mysql"]["collate"] == "utf8mb4_0900_bin"
    assert str(metadata().tables["login_tokens"].c.expires_at.type) == "DOUBLE"


@pytest.mark.parametrize("values", [
    {"environment": "test", "database_url": ""},
    {"database_url": "postgresql://u:secret@localhost/test"},
    {"environment": "test", "database_url": "mysql+pymysql://u:secret@localhost/production"},
    {"database_path": Path("same.db"), "knowledge_database_path": Path("same.db")},
])
def test_database_settings_reject_unsafe_or_unsupported(values):
    with pytest.raises(ValueError): Settings(**values)


def test_database_url_excluded_from_settings_repr_and_export():
    config = Settings(database_url="mysql+pymysql://app:private-example@localhost/campusbridge")
    assert "private-example" not in repr(config)
    assert "database_url" not in config.model_dump()


def test_sqlite_parallel_idempotency_and_single_claim(foundation):
    _, _, _, store, student, counselor = foundation
    conversation = store.create_conversation(student)["conversation_id"]
    with ThreadPoolExecutor(max_workers=12) as pool:
        runs = list(pool.map(lambda _: store.enqueue(conversation, student, "并发请求", "one-key"), range(12)))
    assert len(set(runs)) == 1
    store.begin(runs[0], 12)
    case_id = store.persist_attention(runs[0], RiskOutput(level="LOW", needs_human_support=True, reason_codes=("NO_SIGNAL",)))
    def claim(n):
        try: return store.case_action(case_id, counselor, "claim", "", 0, "claim-"+str(n))["version"]
        except StateConflict: return "conflict"
    with ThreadPoolExecutor(max_workers=12) as pool: outcomes = list(pool.map(claim, range(12)))
    assert outcomes.count(1) == 1 and outcomes.count("conflict") == 11


def test_legacy_knowledge_requires_verified_split_before_application(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    original = Database(path)
    SQLiteKnowledge(original).import_documents(bundled_documents())
    # Simulate an explicitly adopted old combined schema at the current revision.
    from alembic import command
    from campusbridge.schema import config
    with original.engine.connect() as connection:
        command.stamp(config(connection), "d1_0003"); connection.commit()
    original.close()
    settings = Settings(environment="test", database_path=path)
    with pytest.raises(RuntimeError, match="SPLIT_REQUIRED"): create_app(settings)
    split_knowledge(path, settings.knowledge_file)
    from fastapi.testclient import TestClient
    with TestClient(create_app(settings)) as client: assert client.get("/health").status_code == 200


def test_late_writer_after_runtime_close_is_rejected(tmp_path):
    db = Database(tmp_path / "owner.sqlite3")
    db.acquire_runtime_owner(enforce=True)
    db.close()
    with pytest.raises(RuntimeOwnershipError):
        with db.write(): pass


def test_bounded_deadlock_retry_and_no_retry_of_unknown_commit():
    from sqlalchemy.exc import OperationalError
    from campusbridge.store import mysql_conflict_retry
    attempts = []
    @mysql_conflict_retry
    def retryable():
        attempts.append(1)
        raise OperationalError("synthetic", {}, Exception(1213, "deadlock"))
    with pytest.raises(StateConflict, match="WRITE_CONFLICT"): retryable()
    assert len(attempts) == 3
    attempts.clear()
    @mysql_conflict_retry
    def disconnected():
        attempts.append(1)
        raise OperationalError("synthetic", {}, Exception(2013, "connection lost"))
    with pytest.raises(OperationalError): disconnected()
    assert len(attempts) == 1


def test_partial_sqlite_schema_and_foreign_key_damage_are_not_stamped(tmp_path):
    engine = create_engine("sqlite:///"+(tmp_path / "broken.sqlite3").as_posix())
    try:
        metadata().create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO conversations VALUES('orphan','missing-student','now')")
        with pytest.raises(RuntimeError, match="FOREIGN_KEY_VIOLATION"): migrate(engine, adopt_legacy=True)
        assert "alembic_version" not in inspect(engine).get_table_names()
    finally: engine.dispose()


def test_exact_json_codec_keeps_float_precision_and_null():
    from campusbridge.exact_json import ExactJSON
    codec=ExactJSON()
    dialect=mysql.dialect()
    original={"finished_ms":float.fromhex("0x1.fffffffffffffp-4"),"other":[0.0,1.0,True,None,"中文🌟"]}
    encoded=codec.process_bind_param(original,dialect)
    assert codec.process_result_value(encoded,dialect) == original
    assert codec.process_bind_param(None,dialect) is None
    assert codec.process_result_value(None,dialect) is None
