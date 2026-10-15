import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from campusbridge.api import create_app
from campusbridge.contracts import AgentFailure, ContextInput, Identity, IntentOutput, NotFound, ResponseInput, RiskOutput, ModelReply
from campusbridge.agents import ContextAgent, ResponseAgent
from campusbridge.db import Database
from campusbridge.dense_retrieval import LocalBGE, RetrievalConfig, RetrievalUnavailable, SemanticKnowledge, rrf
from campusbridge.retrieval import SQLiteKnowledge, bundled_documents
from campusbridge.settings import Settings


class TinyEmbedder:
    """A deterministic test double, never used in reported retrieval experiments."""
    identity, dimension = "test-only-v1", 3
    def __init__(self): self.calls = []
    def encode(self, values, *, query=False):
        self.calls.append((tuple(values), query))
        return [[float("图书" in v or "读物" in v), float("宿舍" in v or "水龙头" in v), .1] for v in values]


@pytest.fixture
def semantic(tmp_path):
    db = Database(tmp_path / "dense.sqlite3")
    lexical = SQLiteKnowledge(db, top_k=20)
    lexical.import_documents(bundled_documents()[:1] + bundled_documents()[4:5])
    embedder = TinyEmbedder()
    knowledge = SemanticKnowledge(lexical, RetrievalConfig(mode="dense", dense_min_score=.7), embedder)
    yield db, knowledge, embedder
    db.close()


@pytest.mark.parametrize("mode", ["bm25", "dense", "hybrid"])
def test_modes_identity_stability_and_campus_isolation(semantic, mode):
    _, knowledge, _ = semantic
    knowledge.config = replace(knowledge.config, mode=mode)
    async def invoke():
        hits = await knowledge.search("图书馆预约", "fictional-campus-a")
        assert hits and hits[0].document_id == "campus-library"
        assert hits == await knowledge.search("图书馆预约", "fictional-campus-a")
        assert not await knowledge.search("图书馆预约", "different-campus")
        for item in hits:
            original = knowledge.source(item.source_id, Identity(account_id="x", role="student", campus_id="fictional-campus-a"))
            assert item.source_hash == original["source_hash"] and original["active_revision"]
    asyncio.run(invoke())


def test_same_document_id_in_two_campuses_never_merges(semantic):
    _, knowledge, _ = semantic
    knowledge.import_documents([bundled_documents()[0].model_copy(update={"campus_id": "other", "text": "图书馆其他校园流程。"})])
    a = asyncio.run(knowledge.search("读物", "fictional-campus-a"))
    b = asyncio.run(knowledge.search("读物", "other"))
    assert a and b and set(h.source_id for h in a).isdisjoint(h.source_id for h in b)
    with pytest.raises(NotFound):
        knowledge.source(b[0].source_id, Identity(account_id="x", role="student", campus_id="fictional-campus-a"))


def test_batched_document_cache_and_query_not_cached(semantic):
    _, knowledge, embedder = semantic
    assert knowledge.build_index("fictional-campus-a")["new_embeddings"] == 2
    assert len(embedder.calls) == 1 and len(embedder.calls[0][0]) == 2
    assert knowledge.build_index("fictional-campus-a")["cache_hits"] == 2
    asyncio.run(knowledge.search("读物", "fictional-campus-a"))
    asyncio.run(knowledge.search("读物", "fictional-campus-a"))
    assert sum(not q for _, q in embedder.calls) == 1
    assert sum(q for _, q in embedder.calls) == 2


def test_revision_invalidation_and_historical_lookup(semantic):
    _, knowledge, _ = semantic
    old = asyncio.run(knowledge.search("读物", "fictional-campus-a"))[0]
    updated = bundled_documents()[0].model_copy(update={"revision": "new-v3", "text": "图书馆现在采用新的登记手续。"})
    knowledge.import_documents([updated])
    knowledge.import_documents(bundled_documents()[:1])
    current = asyncio.run(knowledge.search("读物", "fictional-campus-a"))
    assert current and all(h.source_id != old.source_id for h in current) and current[0].revision == "new-v3"
    actor = Identity(account_id="x", role="student", campus_id="fictional-campus-a")
    assert knowledge.source(old.source_id, actor)["active_revision"] is False


def test_revision_change_during_encode_is_filtered(semantic):
    _, knowledge, embedder = semantic
    original = embedder.encode
    def changed(values, *, query=False):
        result = original(values, query=query)
        if query:
            knowledge.import_documents([bundled_documents()[0].model_copy(update={"revision": "race", "text": "图书馆变更。"})])
        return result
    embedder.encode = changed
    assert not asyncio.run(knowledge.search("读物", "fictional-campus-a"))
    current = asyncio.run(knowledge.search("读物", "fictional-campus-a"))
    assert current[0].revision == "race"


def test_title_change_and_model_identity_change_invalidate_cache(semantic):
    _, knowledge, embedder = semantic
    knowledge.build_index("fictional-campus-a")
    embedder.identity = "test-only-v2"
    assert knowledge.build_index("fictional-campus-a")["new_embeddings"] == 2
    knowledge.import_documents([bundled_documents()[0].model_copy(update={"revision": "title-v3", "title": "新版图书"})])
    assert knowledge.build_index("fictional-campus-a")["new_embeddings"] == 1


@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_corrupt_index_explicit_error_or_fallback(semantic, mode, caplog):
    db, knowledge, _ = semantic
    knowledge.build_index("fictional-campus-a")
    with db.engine.begin() as connection:
        connection.execute(text("UPDATE embedding_cache SET vector_hash='corrupt'"))
    knowledge.config = replace(knowledge.config, mode=mode)
    with pytest.raises(RetrievalUnavailable, match="CORRUPT"):
        asyncio.run(knowledge.search("图书馆预约", "fictional-campus-a"))
    knowledge.config = replace(knowledge.config, fallback="bm25")
    hits = asyncio.run(knowledge.search("图书馆预约", "fictional-campus-a"))
    assert hits[0].document_id == "campus-library" and knowledge.fallback_count == 1
    assert "EXPLICIT_BM25_FALLBACK" in caplog.text


@pytest.mark.parametrize("value", ["", "   ", "!?🌿"])
def test_empty_query_never_invokes_embedder(semantic, value):
    _, knowledge, embedder = semantic
    assert asyncio.run(knowledge.search(value, "fictional-campus-a")) == ()
    assert embedder.calls == []


@pytest.mark.parametrize("vector", [[0, 0, 0], [1, 2], [float("nan"), 1, 0], [float("inf"), 1, 1]])
def test_invalid_model_vector_is_not_a_false_empty_success(semantic, vector):
    _, knowledge, embedder = semantic
    embedder.encode = lambda values, query=False: [vector for v in values]
    with pytest.raises(RetrievalUnavailable): knowledge.build_index("fictional-campus-a")


def test_rrf_deduplicates_and_stable_ties(semantic):
    _, knowledge, _ = semantic
    items = knowledge._active("fictional-campus-a")
    combined = rrf(((items[0], items[0], items[1]), (items[1], items[0])))
    assert len(combined) == 2
    assert combined[0].score == pytest.approx(1/61 + 1/62)


def test_missing_model_never_downloads_and_explicit_fallback(semantic, tmp_path):
    _, knowledge, _ = semantic
    knowledge.embedder = LocalBGE(tmp_path / "missing")
    with pytest.raises(RetrievalUnavailable):
        asyncio.run(knowledge.search("图书馆预约", "fictional-campus-a"))
    knowledge.config = replace(knowledge.config, fallback="bm25")
    assert asyncio.run(knowledge.search("图书馆预约", "fictional-campus-a"))


def test_parallel_search_uses_independent_sessions(semantic):
    _, knowledge, _ = semantic
    async def invoke():
        results = await asyncio.gather(*(knowledge.search("读物", "fictional-campus-a") for _ in range(5)))
        assert all(hits == results[0] for hits in results)
    asyncio.run(invoke())


def test_context_response_citation_constraints_remain_in_effect(semantic):
    _, knowledge, _ = semantic
    async def invoke():
        risk = RiskOutput(level="LOW", needs_human_support=False, reason_codes=("NO_SIGNAL",))
        context = await ContextAgent(knowledge).run(ContextInput(message="读物", history=(),
            intent=IntentOutput(intent="CAMPUS_SERVICE", needs_knowledge=True), risk=risk, campus_id="fictional-campus-a"))
        assert context.knowledge and context.knowledge[0].document_id == "campus-library"
        async def forged(request): return ModelReply(content="不属于本轮的引用[source:fake-other-session]")
        with pytest.raises(AgentFailure, match="UNKNOWN_SOURCE"):
            await ResponseAgent().run(ResponseInput(message="读物", risk=risk, context=context, attempt=1), forged)
        async def legitimate(request): return ModelReply(content="核对流程[source:" + context.knowledge[0].source_id + "]")
        candidate = await ResponseAgent().run(ResponseInput(message="读物", risk=risk, context=context, attempt=1), legitimate)
        assert candidate.citations[0].source_hash == context.knowledge[0].source_hash
    asyncio.run(invoke())


@pytest.mark.parametrize("mode", ["bm25", "dense", "hybrid"])
def test_configured_api_boots_without_ml_downloads(tmp_path, mode):
    settings = Settings(environment="test", database_path=tmp_path / "app.sqlite3", retrieval_mode=mode,
        embedding_model_path=tmp_path / "absent", retrieval_fallback="bm25")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert (isinstance(client.app.state.knowledge, SQLiteKnowledge)) == (mode == "bm25")


def test_settings_environment_switch(monkeypatch):
    monkeypatch.setenv("CAMPUSBRIDGE_RETRIEVAL_MODE", "hybrid")
    monkeypatch.setenv("CAMPUSBRIDGE_DENSE_MIN_SCORE", ".7")
    config = Settings.from_environment()
    assert config.retrieval_mode == "hybrid" and config.dense_min_score == .7


@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_api_sse_delivery_and_source_details(semantic, tmp_path, mode):
    from campusbridge.security import DEMO_PASSWORD
    _, knowledge, _ = semantic
    knowledge.config = replace(knowledge.config, mode=mode)
    app = create_app(Settings(environment="test", database_path=tmp_path / "api.sqlite3", orchestration="dynamic"), knowledge=knowledge)
    with TestClient(app) as client:
        token = client.post("/api/auth/login", json={"username": "student_lan", "password": DEMO_PASSWORD}).json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        conversation = client.post("/api/conversations", headers=headers).json()["conversation_id"]
        streamed = client.post(f"/api/conversations/{conversation}/stream", headers={**headers, "Idempotency-Key": mode},
            json={"message": "图书馆座位预约需要准备什么？"})
        assert streamed.status_code == 200 and "event: token" in streamed.text
        reply = client.get("/api/runs/" + streamed.headers["x-run-id"], headers=headers).json()
        assert reply["state"] == "ACCEPTED" and reply["citations"]
        source = client.get("/api/knowledge/sources/" + reply["citations"][0]["source_id"], headers=headers).json()
        assert source["source_hash"] == reply["citations"][0]["source_hash"]


@pytest.mark.parametrize("changes", [{"mode": "invented"}, {"fallback": "silent"}, {"top_k": 6, "candidate_k": 5}, {"dense_min_score": 1.1}])
def test_retrieval_config_rejects_invalid(changes):
    with pytest.raises(ValueError): RetrievalConfig(**changes)
