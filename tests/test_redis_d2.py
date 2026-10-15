import asyncio
import importlib.util
import json
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from campusbridge.api import create_app
from campusbridge.contracts import Identity
from campusbridge.db import KnowledgeDatabase
from campusbridge.dense_retrieval import RetrievalConfig, SemanticKnowledge, RetrievalUnavailable
from campusbridge.redis_support import RedisSupport, RateLimitError
from campusbridge.retrieval import SQLiteKnowledge, bundled_documents
from campusbridge.retrieval_cache import CachedKnowledge, normalized_query
from campusbridge.security import DEMO_PASSWORD
from campusbridge.settings import Settings


@pytest.fixture(scope="module")
def server():
    pytest.importorskip("redis", reason="Install .[redis] for real Redis D2 tests")
    spec = importlib.util.spec_from_file_location("private_redis_d2", Path(__file__).parents[1] / "scripts/redis_test_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance = module.PrivateRedis().start()
    yield instance
    instance.close()


@pytest.fixture
def setup(server, tmp_path):
    settings = Settings(environment="test", database_path=tmp_path / "business.sqlite3", redis_cache_enabled=True,
        redis_rate_limit_enabled=True, redis_url=server.url, redis_namespace="cb:test:" + uuid.uuid4().hex,
        redis_cache_ttl=1, login_limit=3, login_peer_limit=100, message_limit=1, login_window_seconds=1)
    support = RedisSupport(settings)
    db = KnowledgeDatabase(tmp_path / "knowledge.sqlite3")
    lexical = SQLiteKnowledge(db)
    lexical.import_documents(bundled_documents())
    cache = CachedKnowledge(lexical, support)
    yield settings, support, cache
    support.close()
    db.close()


def search(cache, query="图书馆预约", campus="fictional-campus-a"):
    return asyncio.run(cache.search(query, campus))


class TestEmbedder:
    """Test double: protocol compatibility only, never retrieval quality evidence."""
    identity, dimension = "d2-test-embedder", 3
    def encode(self, values, *, query=False):
        return [[float("图书" in v), float("宿舍" in v), .1] for v in values]


@pytest.mark.parametrize("mode", ["bm25", "dense", "hybrid"])
def test_real_cache_modes_hit_miss_ttl(setup, mode):
    _, support, cache = setup
    if mode != "bm25":
        cache = CachedKnowledge(SemanticKnowledge(cache.inner, RetrievalConfig(mode=mode, dense_min_score=.1), TestEmbedder()), support)
    first = search(cache)
    assert first and first == search(cache)
    assert support.metrics.snapshot()["cache_hit"] == 1
    time.sleep(1.1)
    assert first == search(cache)
    assert support.metrics.snapshot()["cache_miss"] == 2


def test_campus_isolation_and_historical_source(setup):
    _, _, cache = setup
    old = search(cache)[0]
    assert search(cache, campus="unknown-campus") == ()
    cache.import_documents([bundled_documents()[0].model_copy(update={"revision": "d2-new", "text": "图书馆预约采用新登记。"})])
    assert all(h.source_id != old.source_id for h in search(cache))
    actor = Identity(account_id="a", role="student", campus_id="fictional-campus-a")
    assert cache.source(old.source_id, actor)["active_revision"] is False


def test_global_bm25_corpus_invalidates_other_campus_import(setup):
    _, support, cache = setup
    search(cache)
    cache.import_documents([bundled_documents()[0].model_copy(update={"campus_id": "other-campus"})])
    assert search(cache)
    assert support.metrics.snapshot()["cache_miss"] == 2


def test_read_update_race_never_delivers_retired_cache(setup, monkeypatch):
    _, support, cache = setup
    retired = search(cache)[0].source_id
    original = support.get
    once = []
    def racing(key):
        raw = original(key)
        if not once:
            once.append(True)
            cache.import_documents([bundled_documents()[0].model_copy(update={"revision": "race-v2", "text": "图书馆预约更新登记。"})])
        return raw
    monkeypatch.setattr(support, "get", racing)
    assert all(i.source_id != retired for i in search(cache))
    assert support.metrics.snapshot()["cache_version_retry"] == 1


def test_origin_update_race_retries(setup, monkeypatch):
    _, support, cache = setup
    original = cache.inner.search
    once = []
    async def racing(query, campus):
        result = await original(query, campus)
        if not once:
            once.append(True)
            cache.import_documents([bundled_documents()[0].model_copy(update={"revision": "race-origin", "text": "图书馆预约更新登记。"})])
        return result
    monkeypatch.setattr(cache.inner, "search", racing)
    assert search(cache)[0].revision == "race-origin"
    assert support.metrics.snapshot()["cache_version_retry"] == 1


@pytest.mark.parametrize("corruption", ["text", "source_hash", "revision", "source_id", "binding", "duplicate", "json"])
def test_poisoned_cache_rejected(setup, corruption):
    settings, support, cache = setup
    expected = search(cache)
    key = next(support.client.scan_iter(settings.redis_namespace + ":retrieval:*"))
    packet = json.loads(support.client.get(key))
    if corruption == "binding": packet["binding"]["campus_id"] = "other"
    elif corruption == "duplicate": packet["items"].append(packet["items"][0])
    elif corruption != "json": packet["items"][0][corruption] = "forged"
    support.client.set(key, b"invalid-json" if corruption == "json" else json.dumps(packet))
    assert search(cache) == expected
    assert support.metrics.snapshot()["cache_invalid"] >= 1


def test_metadata_tampering_and_deactivation_invalidates(setup):
    _, _, cache = setup
    first = search(cache)[0]
    with cache.db.engine.begin() as conn:
        conn.execute(text("UPDATE knowledge_documents SET active=0 WHERE document_id=:d"), {"d": first.document_id})
    assert all(i.source_id != first.source_id for i in search(cache))
    with cache.db.engine.begin() as conn:
        conn.execute(text("UPDATE knowledge_chunks SET content_hash='tampered'"))
        conn.execute(text("UPDATE knowledge_documents SET active=1"))
    with pytest.raises(RetrievalUnavailable, match="INTEGRITY"):
        search(cache)


def test_config_model_and_topk_key_separation(setup):
    _, support, cache = setup
    query = "图书馆预约"
    first = asyncio.run(cache.dimensions(query, "fictional-campus-a"))
    cache.inner.top_k = 5
    assert first != asyncio.run(cache.dimensions(query, "fictional-campus-a"))
    dense = CachedKnowledge(SemanticKnowledge(cache.inner, RetrievalConfig(mode="dense"), TestEmbedder()), support)
    a = asyncio.run(dense.dimensions(query, "fictional-campus-a"))
    dense.inner.embedder.identity = "different-model"
    assert a != asyncio.run(dense.dimensions(query, "fictional-campus-a"))


def test_missing_model_keeps_explicit_fallback_policy(setup):
    _, support, cache = setup
    class Missing(TestEmbedder):
        def load(self): raise RetrievalUnavailable("MODEL_MISSING")
        def encode(self, values, *, query=False): raise RetrievalUnavailable("MODEL_MISSING")
    dense = CachedKnowledge(SemanticKnowledge(cache.inner, RetrievalConfig(mode="dense"), Missing()), support)
    with pytest.raises(RetrievalUnavailable): search(dense)
    dense.inner.config = RetrievalConfig(mode="dense", fallback="bm25")
    assert search(dense)


def test_redis_failure_bypass_and_rate_fail_closed_then_restart(server, setup):
    _, support, cache = setup
    expected = search(cache)
    server.stop()
    try:
        assert search(cache) == expected
        with pytest.raises(RateLimitError) as failed:
            support.login("student_lan", "127.0.0.1")
        assert failed.value.status == 503
        assert support.metrics.snapshot()["cache_read_error"] >= 1
    finally:
        server.start()
    support.login("student_lan", "127.0.0.1")
    assert search(cache) == expected
    assert search(cache) == expected


def test_timeout_bypass_and_limiter_fail_closed(setup):
    settings, _, cache = setup
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen()
        port = listener.getsockname()[1]
        stop = threading.Event()
        def stall():
            conn, _ = listener.accept()
            with conn: stop.wait(2)
        worker = threading.Thread(target=stall, daemon=True); worker.start()
        slow = RedisSupport(settings.model_copy(update={"redis_url": __import__('pydantic').SecretStr(f"redis://127.0.0.1:{port}/0"), "redis_timeout": .03}))
        try:
            assert search(CachedKnowledge(cache.inner, slow))
            with pytest.raises(RateLimitError): slow.login("a", "p")
            assert slow.metrics.snapshot()["cache_read_error"] == 1
        finally:
            slow.close(); stop.set(); worker.join(3)


def test_atomic_concurrent_limit_expiry_and_accounts(setup):
    _, support, _ = setup
    def call(_):
        try: support.limit("test", "a", 7, 1); return True
        except RateLimitError as e: assert e.status == 429; return False
    with ThreadPoolExecutor(max_workers=20) as pool:
        assert sum(pool.map(call, range(40))) == 7
    support.limit("test", "b", 7, 1)
    time.sleep(1.1)
    support.limit("test", "a", 7, 1)
    assert support.metrics.snapshot()["limit_blocked"] == 33


def test_api_login_message_and_stream_share_server_identity_budget(setup):
    settings, _, _ = setup
    with TestClient(create_app(settings)) as client:
        def login(name):
            return client.post("/api/auth/login", json={"username": name, "password": DEMO_PASSWORD})
        a, b = login("student_lan"), login("student_he")
        ha = {"Authorization": "Bearer " + a.json()["access_token"]}
        hb = {"Authorization": "Bearer " + b.json()["access_token"]}
        conv = client.post("/api/conversations", headers=ha).json()["conversation_id"]
        first = client.post(f"/api/conversations/{conv}/messages", json={"message": "你好"}, headers={**ha, "Idempotency-Key": "one"})
        assert first.status_code == 200
        blocked = client.post(f"/api/conversations/{conv}/stream", json={"message": "你好"}, headers={**ha, "Idempotency-Key": "two", "X-User-ID": "forged"})
        assert blocked.status_code == 429 and blocked.headers["Retry-After"]
        assert client.post(f"/api/conversations/{conv}/messages", json={"message": "你好", "account_id": "forged"}, headers={**ha, "Idempotency-Key": "three"}).status_code == 422
        convb = client.post("/api/conversations", headers=hb).json()["conversation_id"]
        assert client.post(f"/api/conversations/{convb}/messages", json={"message": "你好"}, headers={**hb, "Idempotency-Key": "one"}).status_code == 200
        # Exact username account limit, regardless of a spoofed proxy header.
        for _ in range(3):
            client.post("/api/auth/login", json={"username": "nobody", "password": "bad"}, headers={"X-Forwarded-For": str(uuid.uuid4())})
        assert client.post("/api/auth/login", json={"username": "nobody", "password": "bad"}).status_code == 429


def test_redis_disabled_has_no_dependency_or_connection(tmp_path):
    config = Settings(environment="test", database_path=tmp_path / "b.sqlite3")
    support = RedisSupport(config)
    assert support.client is None
    support.login("a", "p"); support.message(Identity(account_id="a", campus_id="c", role="student"))
    assert support.metrics.snapshot()["cache_hit_rate"] is None


def test_configuration_secret_redaction_and_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMPUSBRIDGE_REDIS_CACHE_ENABLED", "true")
    monkeypatch.setenv("CAMPUSBRIDGE_REDIS_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("CAMPUSBRIDGE_RATE_LIMIT_STATUS", "503")
    config = Settings.from_environment()
    assert config.redis_cache_enabled and config.redis_rate_limit_enabled and config.rate_limit_status == 503
    secret = config.model_copy(update={"redis_url": __import__('pydantic').SecretStr("redis://:private-password@localhost/0")})
    assert "private-password" not in repr(secret) and "redis_url" not in secret.model_dump()
    with pytest.raises(ValueError): Settings(redis_url="redis://localhost/0?socket_timeout=999")
    assert normalized_query("  图书馆\n预约  ") == "图书馆 预约"


def test_no_result_cache_empty_query_and_revision_reactivation(setup):
    _, support, cache = setup
    assert search(cache, "") == ()
    assert search(cache, "    ") == ()
    assert search(cache, "zzzzunlistedquery") == ()
    assert search(cache, "zzzzunlistedquery") == ()
    assert support.metrics.snapshot()["cache_hit"] == 1
    source = search(cache)[0]
    with cache.db.engine.begin() as conn:
        conn.execute(text("UPDATE knowledge_documents SET active=0 WHERE document_id=:d"), {"d": source.document_id})
    assert source.source_id not in {i.source_id for i in search(cache)}
    with cache.db.engine.begin() as conn:
        conn.execute(text("UPDATE knowledge_documents SET active=1 WHERE document_id=:d"), {"d": source.document_id})
    assert source.source_id in {i.source_id for i in search(cache)}


def test_update_after_fill_before_return_is_rejected_on_next_read(setup, monkeypatch):
    _, support, cache = setup
    original = support.put
    once = []
    def fill(key, payload):
        if not once:
            once.append(True)
            cache.import_documents([bundled_documents()[0].model_copy(update={"revision": "after-validation", "text": "图书馆预约新规定。"})])
        original(key, payload)
    monkeypatch.setattr(support, "put", fill)
    first = search(cache)  # Linearized before the concurrent import.
    second = search(cache)
    assert first[0].revision != second[0].revision
    assert second[0].revision == "after-validation"


def test_repeated_catalog_churn_returns_no_evidence(setup, monkeypatch):
    _, support, cache = setup
    original = support.get
    versions = []
    def changing(key):
        raw = original(key)
        versions.append(len(versions))
        cache.import_documents([bundled_documents()[0].model_copy(update={"revision": "churn-" + str(len(versions))})])
        return raw
    monkeypatch.setattr(support, "get", changing)
    assert search(cache) == ()
    assert support.metrics.snapshot()["cache_catalog_churn"] == 1


def test_peer_login_limit_and_configured_status(setup):
    settings, support, _ = setup
    altered = RedisSupport(settings.model_copy(update={"login_peer_limit": 1, "rate_limit_status": 503}))
    altered.login("a", "peer-one")
    with pytest.raises(RateLimitError) as exc:
        altered.login("b", "peer-one")
    assert exc.value.status == 503
    altered.login("b", "peer-two")
    altered.close()


def test_api_outage_blocks_before_business_mutation(server, setup):
    from sqlalchemy import func, select
    from campusbridge.db import AgentRun
    settings, _, _ = setup
    with TestClient(create_app(settings)) as client:
        access = client.post("/api/auth/login", json={"username": "student_lan", "password": DEMO_PASSWORD}).json()
        headers = {"Authorization": "Bearer " + access["access_token"]}
        conv = client.post("/api/conversations", headers=headers).json()["conversation_id"]
        server.stop()
        try:
            assert client.post("/api/auth/login", json={"username": "student_lan", "password": DEMO_PASSWORD}).status_code == 503
            response = client.post(f"/api/conversations/{conv}/stream", json={"message": "你好"}, headers={**headers, "Idempotency-Key": "closed"})
            assert response.status_code == 503
            assert response.headers["Retry-After"] == "1"
            with client.app.state.database.sessions() as session:
                assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
        finally: server.start()


def test_warm_cache_preserves_citation_rejection(setup):
    from campusbridge.contracts import CandidateOutput, Citation, AgentFailure, digest, grounding_digest
    from campusbridge.grounding import validate_citations
    # Existing downstream verifier remains the final authority; wrapper only
    # supplies exactly the same validated KnowledgeItem as an uncached search.
    _, _, cache = setup
    first = search(cache)
    assert first == search(cache)
    item = first[0]
    citation = Citation(source_id=item.source_id, title=item.title, revision=item.revision,
        document_id=item.document_id, quote=item.text, source_hash=item.source_hash)
    reply = "查看来源 [source:" + item.source_id + "]"
    candidate = CandidateOutput(candidate_id="test", text=reply, content_hash=digest(reply), attempt=1,
        origin="MODEL", citations=(citation,), retrieval_status="HIT",
        citations_hash=grounding_digest((citation,), "HIT"))
    validate_citations(candidate, first)
    with pytest.raises(AgentFailure, match="UNKNOWN_SOURCE"):
        validate_citations(candidate, ())
    bad = citation.model_copy(update={"quote": "forged"})
    with pytest.raises(AgentFailure, match="METADATA_MISMATCH"):
        validate_citations(candidate.model_copy(update={"citations": (bad,)}), first)


def test_actual_redis_pause_causes_timeout_and_recovers(server, setup):
    settings, _, cache = setup
    support = RedisSupport(settings.model_copy(update={"redis_timeout": .03}))
    try:
        support.client.ping()
        server.client.execute_command("CLIENT", "PAUSE", 500, "ALL")
        assert search(CachedKnowledge(cache.inner, support))
        with pytest.raises(RateLimitError) as exc:
            support.message(Identity(account_id="pause-test", campus_id="c", role="student"))
        assert exc.value.status == 503
        time.sleep(.6)
        support.message(Identity(account_id="pause-test", campus_id="c", role="student"))
        assert support.metrics.snapshot()["cache_read_error"] >= 1
    finally: support.close()


def test_cache_does_not_store_query_identity_or_unreviewed_reply(setup):
    settings, support, cache = setup
    query = "图书馆预约 d2-private-query-unique"
    search(cache, query)
    key = next(support.client.scan_iter(settings.redis_namespace + ":retrieval:*"))
    value = support.client.get(key)
    assert query.encode() not in value
    packet = json.loads(value)
    assert set(packet) == {"binding", "items"}
    assert "query_sha256" in packet["binding"]
    assert all("candidate_id" not in item and "account_id" not in item for item in packet["items"])


def test_forged_cross_campus_cached_sources_rejected(setup):
    settings, support, cache = setup
    cache.import_documents([bundled_documents()[0].model_copy(update={"campus_id": "other-campus"})])
    expected = search(cache)
    other = search(cache, campus="other-campus")
    keys = list(support.client.scan_iter(settings.redis_namespace + ":retrieval:*"))
    for key in keys:
        packet = json.loads(support.client.get(key))
        if packet["binding"]["campus_id"] == "fictional-campus-a":
            packet["items"] = [i.model_dump(mode="json") for i in other]
            support.client.set(key, json.dumps(packet))
    assert search(cache) == expected
    assert support.metrics.snapshot()["cache_invalid"] >= 1
