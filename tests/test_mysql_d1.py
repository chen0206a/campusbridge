"""Opt-in acceptance against a REAL isolated MySQL 8 process (never an SQLite substitute)."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, TimeoutError as PoolTimeout

from campusbridge.api import create_app
from campusbridge.contracts import AgentName, Event, Identity, ModelReply, RiskOutput, RunState, StateConflict, identifier
from campusbridge.data_migration import copy_business
from campusbridge.db import Account, AgentRun, CaseEvent, Database, Message, ModelCall, RiskEvent, RunEvent, RuntimeOwnershipError, SupportCase
from campusbridge.models import MockModel
from campusbridge.retrieval import SQLiteKnowledge, bundled_documents
from campusbridge.runtime import CampusRuntime
from campusbridge.schema import migrate, verify_schema
from campusbridge.schema_v1 import metadata
from campusbridge.security import Authentication, DEMO_PASSWORD, password_hash
from campusbridge.settings import Settings
from campusbridge.store import Store
from tests.helpers import ControlledModel


@pytest.fixture
def mysql_db(tmp_path):
    if os.environ.get("CAMPUSBRIDGE_TEST_MYSQL_ALLOW") != "1": pytest.skip("Opt-in isolated MySQL acceptance; use scripts/test_mysql_d1.py")
    url = os.environ.get("CAMPUSBRIDGE_TEST_MYSQL_URL", "")
    admin_url = os.environ.get("CAMPUSBRIDGE_TEST_MYSQL_MIGRATION_URL", "")
    parsed, admin_parsed = make_url(url), make_url(admin_url)
    if not parsed.database.startswith("cb_d1_test_") or parsed.database != admin_parsed.database or parsed.host != admin_parsed.host:
        pytest.fail("REFUSE_NON_ISOLATED_MYSQL_TEST_TARGET")
    admin = Database(url=admin_url, scope="business", schema="none")
    try:
        from alembic import command
        from campusbridge.schema import config
        with admin.engine.connect() as connection:
            command.downgrade(config(connection), "base")
            connection.commit()
        migrate(admin.engine)
        db = Database(url=url, scope="business", schema="verify")
        settings = Settings(environment="test", database_url=url, knowledge_database_path=tmp_path / "knowledge.sqlite3", orchestration="dynamic")
        auth = Authentication(db, settings)
        auth.seed()
        student = auth.authenticate(auth.login("student_lan", DEMO_PASSWORD)["access_token"])
        counselor = auth.authenticate(auth.login("counselor_yu", DEMO_PASSWORD)["access_token"])
        yield db, admin, settings, auth, Store(db), student, counselor
        db.close()
    finally: admin.close()


def run_and_case(values):
    db, _, settings, _, store, student, counselor = values
    conv = store.create_conversation(student)["conversation_id"]
    run = store.enqueue(conv, student, "需要工作人员帮忙", "risk-turn")
    store.begin(run, 12)
    case = store.persist_attention(run, RiskOutput(level="LOW", needs_human_support=True, reason_codes=("NO_SIGNAL",)))
    return conv, run, case


def test_real_mysql_schema_pool_and_no_fts(mysql_db):
    db, admin, _, _, _, _, _ = mysql_db
    assert verify_schema(db.engine)["verified"] and db.is_mysql
    names = set(inspect(db.engine).get_table_names())
    assert "knowledge_fts" not in names and "knowledge_documents" not in names
    assert db.engine.pool.size() == 10 and db.engine.pool._pre_ping
    with db.engine.connect() as connection:
        assert connection.scalar(text("SELECT @@transaction_isolation")) == "READ-COMMITTED"
        assert connection.scalar(text("SELECT @@character_set_connection")) == "utf8mb4"
        assert connection.scalar(text("SELECT @@innodb_lock_wait_timeout")) == 5
        assert connection.scalar(text("SELECT @@collation_connection")) == "utf8mb4_0900_ai_ci"  # Tables use binary NO PAD collation.
    with pytest.raises(Exception):
        with db.engine.begin() as connection: connection.exec_driver_sql("CREATE TABLE forbidden_ddl(id INT)")


def test_concurrent_duplicate_enqueue_and_conflicting_payload(mysql_db):
    db, _, _, _, store, student, _ = mysql_db
    conv = store.create_conversation(student)["conversation_id"]
    barrier = Barrier(16)
    def enqueue(_):
        barrier.wait()
        return store.enqueue(conv, student, "中文🌟独立请求", "opaque-key")
    with ThreadPoolExecutor(max_workers=16) as pool: runs = list(pool.map(enqueue, range(16)))
    assert len(set(runs)) == 1
    with db.sessions() as session: assert session.scalar(select(func.count()).select_from(AgentRun)) == 1
    with pytest.raises(StateConflict, match="KEY_REUSED"): store.enqueue(conv, student, "不同内容", "opaque-key")


def test_idempotency_across_conversations_and_exact_key_semantics(mysql_db):
    _, _, _, _, store, student, _ = mysql_db
    first = store.create_conversation(student)["conversation_id"]
    second = store.create_conversation(student)["conversation_id"]
    initial = store.enqueue(first, student, "请求", "Key")
    with pytest.raises(StateConflict): store.enqueue(second, student, "请求", "Key")
    assert len({initial, store.enqueue(first, student, "请求", "key"), store.enqueue(first, student, "请求", "Key ")}) == 3


def test_parallel_claim_has_one_winner_and_atomic_audit(mysql_db):
    db, _, _, _, store, _, counselor = mysql_db
    _, _, case = run_and_case(mysql_db)
    second_id = identifier()
    with db.write() as session:
        session.add(Account(id=second_id,username="second_counselor",password_hash=password_hash(DEMO_PASSWORD),role="counselor",campus_id=counselor.campus_id))
    second = Identity(account_id=second_id,role="counselor",campus_id=counselor.campus_id)
    barrier = Barrier(16)
    def claim(n):
        barrier.wait()
        try: return store.case_action(case, counselor if n%2 else second, "claim", "", 0, "claim-"+str(n))["version"]
        except StateConflict: return "conflict"
    with ThreadPoolExecutor(max_workers=16) as pool: results = list(pool.map(claim, range(16)))
    assert results.count(1) == 1 and results.count("conflict") == 15
    detail = store.case_detail(case, counselor)
    assert detail["version"] == 1 and [e["action"] for e in detail["events"]] == ["CREATE", "CLAIM"]


def test_same_case_operation_replays_and_versions_do_not_advance(mysql_db):
    _, _, _, _, store, _, counselor = mysql_db
    _, _, case = run_and_case(mysql_db)
    with ThreadPoolExecutor(max_workers=12) as pool:
        replies = list(pool.map(lambda _: store.case_action(case, counselor, "claim", "", 0, "same"), range(12)))
    assert sum(not r["idempotent_replay"] for r in replies) == 1
    assert {r["version"] for r in replies} == {1}
    with pytest.raises(StateConflict): store.case_action(case, counselor, "process", "已联系学生", 1, "same")
    store.case_action(case, counselor, "process", "已联系学生", 1, "process")
    store.case_action(case, counselor, "close", "确认完成跟进", 2, "close")
    assert store.case_detail(case, counselor)["state"] == "CLOSED"


def test_parallel_model_budget_usage_and_event_sequences(mysql_db):
    db, _, _, _, store, _, _ = mysql_db
    _, run, _ = run_and_case(mysql_db)
    with ThreadPoolExecutor(max_workers=16) as pool:
        calls = list(pool.map(lambda _: store.start_model_call(run, AgentName.INTENT, 6, "mock", "synthetic"), range(16)))
    calls = [c for c in calls if c]
    assert len(calls) == 6 and len(set(calls)) == 6
    def finish(call): store.finish_model_call(call, "SUCCESS", 1.2345, reply=ModelReply(content="ok", input_tokens=7, output_tokens=11))
    with ThreadPoolExecutor(max_workers=12) as pool: list(pool.map(finish, calls+calls))
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda _: store.audit(run, Event(state=RunState.ANALYZING, code="CONCURRENT_AUDIT")), range(32)))
    with db.sessions() as session:
        row = session.get(AgentRun, run)
        assert (row.model_calls, row.input_tokens, row.output_tokens) == (6, 42, 66)
        sequences = list(session.scalars(select(RunEvent.sequence).where(RunEvent.run_id == run).order_by(RunEvent.sequence)))
        assert sequences == list(range(1, len(sequences)+1))


def test_attention_is_singleton_under_concurrent_persistence(mysql_db):
    db, _, _, _, store, student, _ = mysql_db
    conv = store.create_conversation(student)["conversation_id"]
    run = store.enqueue(conv, student, "需要帮助", "attention")
    risk = RiskOutput(level="LOW", needs_human_support=True, reason_codes=("NO_SIGNAL",))
    with ThreadPoolExecutor(max_workers=12) as pool: cases = list(pool.map(lambda _: store.persist_attention(run, risk), range(12)))
    assert len(set(cases)) == 1
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RiskEvent)) == 1
        assert session.scalar(select(func.count()).select_from(SupportCase)) == 1
        assert session.scalar(select(func.count()).select_from(CaseEvent)) == 1


def test_single_runtime_owner_and_ownership_loss_fails_closed(mysql_db):
    db, admin, settings, _, store, _, _ = mysql_db
    peer = Database(url=settings.database_url.get_secret_value(), scope="business", schema="verify")
    try:
        db.acquire_runtime_owner()
        with pytest.raises(RuntimeOwnershipError): peer.acquire_runtime_owner()
        db.assert_runtime_owner()
        with db.owner_guard:
            db.owner_connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name":db.owner_name})
            db.owner_connection.commit()
        with pytest.raises(RuntimeOwnershipError): db.assert_runtime_owner()
        peer.acquire_runtime_owner()
    finally: peer.close()


def test_connection_pre_ping_replaces_killed_idle_connection(mysql_db):
    db, admin, _, _, _, _, _ = mysql_db
    with db.engine.connect() as connection: connection_id = connection.scalar(text("SELECT CONNECTION_ID()"))
    with admin.engine.connect() as connection: connection.exec_driver_sql("KILL CONNECTION " + str(int(connection_id)))
    with db.engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
        assert connection.scalar(text("SELECT CONNECTION_ID()")) != connection_id


def test_pool_exhaustion_has_finite_timeout(mysql_db):
    _, _, settings, _, _, _, _ = mysql_db
    db = Database(url=settings.database_url.get_secret_value(), scope="business", schema="verify", pool_size=1, max_overflow=0, pool_timeout=1)
    try:
        with db.engine.connect():
            with pytest.raises(PoolTimeout): db.engine.connect()
    finally: db.close()


def test_api_mock_sse_rag_scope_claim_and_generation_failure(mysql_db):
    db, _, settings, _, _, _, _ = mysql_db
    app = create_app(settings, model=ControlledModel(AgentName.RESPONSE, "exception"))
    with TestClient(app) as client:
        def login(name):
            response = client.post("/api/auth/login", json={"username":name,"password":DEMO_PASSWORD})
            assert response.status_code == 200
            return {"Authorization":"Bearer "+response.json()["access_token"]}
        student, counselor, other = login("student_lan"), login("counselor_yu"), login("student_he")
        conv = client.post("/api/conversations",headers=student).json()["conversation_id"]
        response = client.post(f"/api/conversations/{conv}/stream",headers={**student,"Idempotency-Key":"risk"},json={"message":"我无法保证自己的安全"})
        assert response.status_code == 200 and "SAFE_FALLBACK" in response.text
        assert client.get(f"/api/conversations/{conv}", headers=other).status_code == 404
        case = client.get("/api/cases", headers=counselor).json()["cases"][0]
        def claim(n): return client.post(f"/api/cases/{case['case_id']}/claim",headers={**counselor,"Idempotency-Key":"api-claim-"+str(n)},json={"expected_version":0}).status_code
        with ThreadPoolExecutor(max_workers=12) as pool: replies = list(pool.map(claim,range(12)))
        assert replies.count(200) == 1 and replies.count(409) == 11
        assert "accounts" not in inspect(app.state.knowledge_database.engine).get_table_names()


def test_real_mysql_duplicate_http_chat_one_model_chain_and_citation(mysql_db):
    _, _, settings, _, _, _, _ = mysql_db
    model = ControlledModel()
    app = create_app(settings, model=model)
    with TestClient(app) as client:
        token = client.post("/api/auth/login",json={"username":"student_lan","password":DEMO_PASSWORD}).json()["access_token"]
        headers = {"Authorization":"Bearer "+token}
        conv = client.post("/api/conversations",headers=headers).json()["conversation_id"]
        def chat(_): return client.post(f"/api/conversations/{conv}/messages",headers={**headers,"Idempotency-Key":"one-chat"},json={"message":"图书馆预约座位需要准备什么？"})
        with ThreadPoolExecutor(max_workers=8) as pool: responses = list(pool.map(chat,range(8)))
        assert all(r.status_code == 200 for r in responses)
        assert len({r.json()["run_id"] for r in responses}) == 1
        assert len(model.requests) == 4
        assert len(client.get(f"/api/conversations/{conv}",headers=headers).json()["messages"]) == 2
        citation = responses[0].json()["citations"][0]
        assert client.get("/api/knowledge/sources/"+citation["source_id"], headers=headers).json()["source_hash"] == citation["source_hash"]


def test_mysql_restart_recovery_preserves_case_and_token_precision(mysql_db):
    db, _, settings, _, store, student, counselor = mysql_db
    _, run, case = run_and_case(mysql_db)
    store.start_model_call(run, AgentName.RESPONSE, 6, "mock", "synthetic")
    store.recover_interrupted()
    assert store.run_snapshot(run)["state"] == RunState.FAILED
    assert store.case_detail(case,counselor)["state"] == "OPEN"
    assert store.trace(run,counselor)["calls"][0]["status"] == "INTERRUPTED"
    from campusbridge.db import LoginToken
    with db.write() as session:
        row = session.scalars(select(LoginToken)).first()
        key = row.token_hash
        row.expires_at = 1792048823.123456
    with db.sessions() as session: assert abs(session.get(LoginToken,key).expires_at-1792048823.123456) < .00001


def test_copy_business_from_readonly_sqlite_is_atomic_and_idempotency_survives(mysql_db,tmp_path):
    db, admin, _, _, _, _, _ = mysql_db
    # Target must be completely empty. Remove only this isolated fixture's demo rows.
    with admin.engine.begin() as connection:
        for table in reversed(metadata().sorted_tables): connection.execute(table.delete())
    source = Database(tmp_path/"legacy.sqlite3")
    settings = Settings(environment="test", database_path=tmp_path/"legacy.sqlite3")
    auth = Authentication(source, settings); auth.seed()
    actor = auth.authenticate(auth.login("student_lan", DEMO_PASSWORD)["access_token"])
    store = Store(source)
    conv = store.create_conversation(actor)["conversation_id"]
    run = store.enqueue(conv,actor,"迁移中文🌟","preserved-key")
    risk_conversation = store.create_conversation(actor)["conversation_id"]
    risk_run = store.enqueue(risk_conversation,actor,"我无法保证自己的安全","preserved-risk")
    from campusbridge.knowledge import DemoKnowledge
    runtime = CampusRuntime(store,MockModel(),DemoKnowledge(),settings)
    asyncio.run(runtime.complete(risk_run))
    original_reply = store.deliverable(risk_run,actor)
    counselor = auth.authenticate(auth.login("counselor_yu",DEMO_PASSWORD)["access_token"])
    case = store.cases(counselor)[0]["case_id"]
    for action,version,note in (("claim",0,""),("process",1,"已联系学生"),("close",2,"确认跟进完成")):
        store.case_action(case,counselor,action,note,version,"preserved-"+action)
    source.close()
    import hashlib
    before = hashlib.sha256((tmp_path/"legacy.sqlite3").read_bytes()).hexdigest()
    report = copy_business(tmp_path/"legacy.sqlite3",db)
    assert report["transaction_verified"] and report["counts"]["agent_runs"] == 2
    assert before == hashlib.sha256((tmp_path/"legacy.sqlite3").read_bytes()).hexdigest()
    assert Store(db).enqueue(conv,actor,"迁移中文🌟","preserved-key") == run
    assert Store(db).deliverable(risk_run,actor) == original_reply
    assert Store(db).case_detail(case,counselor)["state"] == "CLOSED"
    with pytest.raises(ValueError,match="MUST_BE_EMPTY"): copy_business(tmp_path/"legacy.sqlite3",db)


def test_real_mysql_tenant_and_case_owner_isolation(mysql_db):
    from campusbridge.contracts import AccessDenied, NotFound
    db, _, _, _, store, student, counselor = mysql_db
    conv, run, case = run_and_case(mysql_db)
    for role in ("student", "counselor"):
        other = Identity(account_id=identifier(),role=role,campus_id="different-campus")
        with pytest.raises(NotFound): store.trace(run,other)
        if role == "counselor":
            with pytest.raises(NotFound): store.case_action(case,other,"claim","",0,"bad-campus")
            assert store.cases(other) == []
        else:
            with pytest.raises(AccessDenied): store.cases(other)
    store.case_action(case,counselor,"claim","",0,"owner")
    other_id=identifier()
    with db.write() as session:
        session.add(Account(id=other_id,username="colleague",password_hash=password_hash(DEMO_PASSWORD),role="counselor",campus_id=counselor.campus_id))
    colleague=Identity(account_id=other_id,role="counselor",campus_id=counselor.campus_id)
    with pytest.raises(AccessDenied): store.case_action(case,colleague,"process","已联系学生",1,"not-owner")


def test_database_unique_message_order_is_a_final_fence(mysql_db):
    db, _, _, _, store, student, _ = mysql_db
    conv, run, _ = run_and_case(mysql_db)
    second=store.enqueue(conv,student,"另一请求","second")
    with pytest.raises(IntegrityError):
        with db.write() as session:
            session.add(Message(id=identifier(),conversation_id=conv,run_id=second,role="user",text="重复位置",order_index=1))
    with db.sessions() as session: assert session.scalar(select(func.count()).select_from(Message)) == 1


def test_application_restart_recovers_interrupted_mysql_run(mysql_db):
    _, _, settings, _, _, student, counselor = mysql_db
    app=create_app(settings)
    with TestClient(app):
        store=app.state.store
        conv=store.create_conversation(student)["conversation_id"]
        run=store.enqueue(conv,student,"重启前人工求助","restart-case")
        store.begin(run,12)
        case=store.persist_attention(run,RiskOutput(level="LOW",needs_human_support=True,reason_codes=("NO_SIGNAL",)))
        store.start_model_call(run,AgentName.RESPONSE,6,"mock","synthetic")
    reopened=create_app(settings)
    with TestClient(reopened):
        assert reopened.state.store.run_snapshot(run)["state"] == RunState.FAILED
        trace=reopened.state.store.trace(run,counselor)
        assert trace["failure_code"] == "SERVER_RESTARTED" and trace["calls"][0]["status"] == "INTERRUPTED"
        assert reopened.state.store.case_detail(case,counselor)["state"] == "OPEN"
        assert reopened.state.store.case_action(case,counselor,"claim","",0,"after-restart")["state"] == "CLAIMED"


def test_mysql_copy_failure_rolls_back_already_inserted_parents(mysql_db,tmp_path):
    from sqlalchemy.exc import DataError
    db,admin,_,_,_,_,_=mysql_db
    with admin.engine.begin() as connection:
        for table in reversed(metadata().sorted_tables): connection.execute(table.delete())
    source=Database(tmp_path/"invalid.sqlite3")
    auth=Authentication(source,Settings(environment="test",database_path=tmp_path/"invalid.sqlite3")); auth.seed()
    actor=auth.authenticate(auth.login("student_lan",DEMO_PASSWORD)["access_token"])
    conv=Store(source).create_conversation(actor)["conversation_id"]
    from campusbridge.db import Conversation
    with source.write() as session: session.get(Conversation,conv).created_at="x"*100
    source.close()
    with pytest.raises(DataError): copy_business(tmp_path/"invalid.sqlite3",db)
    with db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Account)) == 0


def test_exact_json_float_roundtrip_and_database_json_valid_fence(mysql_db):
    from sqlalchemy.exc import DBAPIError
    db,_,_,_,_,_,_=mysql_db
    _,run,_=run_and_case(mysql_db)
    payload={"finished_ms":float.fromhex("0x1.fffffffffffffp-4"),"nested":[0.0,1.0,True,None,"中文🌟"]}
    event_id=identifier()
    with db.write() as session:
        session.add(RunEvent(id=event_id,run_id=run,sequence=500,event=payload))
    with db.sessions() as session: assert session.get(RunEvent,event_id).event == payload
    with pytest.raises(DBAPIError) as failed:
        with db.engine.begin() as connection:
            connection.execute(text("UPDATE run_events SET event=:invalid WHERE id=:id"),{"invalid":"{broken","id":event_id})
    assert failed.value.orig.args[0] == 3819
