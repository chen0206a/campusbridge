import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from campusbridge.agents import ResponseAgent
from campusbridge.contracts import (AgentFailure, CandidateOutput, ContextOutput, Identity, ModelReply,
    ResponseInput, ReviewOutput, RiskOutput, StateConflict, digest)
from campusbridge.db import Database, KnowledgeChunk, KnowledgeDocument
from campusbridge.policy import valid_binding
from campusbridge.retrieval import DocumentInput, SQLiteKnowledge, bundled_documents, chunk_text


@pytest.fixture
def index(tmp_path):
    db = Database(tmp_path / "rag.sqlite3")
    knowledge = SQLiteKnowledge(db)
    knowledge.import_documents(bundled_documents())
    yield db, knowledge
    db.close()


@pytest.mark.parametrize("query,doc", [("图书馆预约座位", "campus-library"),
    ("助学金申请材料", "campus-aid"), ("宿舍报修水管", "campus-housing"),
    ("选课退课规则", "campus-course"), ("失物招领", "campus-lost"),
    ("无障碍支持", "campus-access"), ("library booking", "campus-library")])
def test_chinese_english_search(index, query, doc):
    db, knowledge = index
    hits = asyncio.run(knowledge.search(query, "fictional-campus-a"))
    assert hits and hits[0].document_id == doc
    assert hits == asyncio.run(knowledge.search(query, "fictional-campus-a"))
    for item in hits:
        with db.sessions() as session:
            chunk = session.get(KnowledgeChunk, item.source_id)
            parent = session.get(KnowledgeDocument, chunk.document_key)
            assert parent.text[item.start:item.end] == item.text
        assert item.source_hash == digest(item.text)


def test_no_hit_and_campus_isolation(index):
    _, knowledge = index
    assert asyncio.run(knowledge.search("量子舰船校准许可", "fictional-campus-a")) == ()
    assert asyncio.run(knowledge.search("图书馆预约", "other-campus")) == ()
    assert asyncio.run(knowledge.search("? AND * \"", "fictional-campus-a")) == ()


def test_unicode_chunking_and_coverage():
    value = ("同学🌿需要支持。\n\n" * 80)
    chunks = chunk_text(value, size=70, overlap=12)
    assert len(chunks) > 1
    assert all(value[start:end] == part for start, end, part in chunks)
    assert chunks[0][0] == 0 and chunks[-1][1] == len(value)
    assert all(chunks[i][1] >= chunks[i + 1][0] for i in range(len(chunks) - 1))
    # Extreme overlap still makes forward progress at paragraph boundaries.
    assert chunk_text(value, size=70, overlap=65)[-1][1] == len(value)


def test_import_idempotency_and_revision_history(index):
    _, knowledge = index
    docs = bundled_documents()
    assert knowledge.import_documents(docs)["documents_unchanged"] == 8
    old = asyncio.run(knowledge.search("图书馆预约", "fictional-campus-a"))[0]
    original = docs[0]
    revised = original.model_copy(update={"revision": "revised-test-v3", "text": "图书馆预约座位请使用新的登记流程。"})
    knowledge.import_documents([revised])
    knowledge.import_documents(docs)
    current = asyncio.run(knowledge.search("图书馆预约", "fictional-campus-a"))[0]
    assert current.revision == "revised-test-v3"
    actor = Identity(account_id="synthetic", role="student", campus_id="fictional-campus-a")
    assert knowledge.source(old.source_id, actor)["active_revision"] is False
    assert knowledge.source(old.source_id, actor)["text"] == old.text
    with pytest.raises(StateConflict, match="IMMUTABLE"):
        knowledge.import_documents([original.model_copy(update={"text": "改变已有版本"})])


def test_import_failure_rolls_back_fts_and_documents(index):
    db, knowledge = index
    with db.sessions() as session:
        before = session.execute(text("SELECT count(*) FROM knowledge_fts")).scalar()
    new = bundled_documents()[0].model_copy(update={"document_id": "new-atomic-test"})
    conflicting = bundled_documents()[1].model_copy(update={"text": "same revision different body"})
    with pytest.raises(StateConflict):
        knowledge.import_documents([new, conflicting])
    with db.sessions() as session:
        assert session.execute(text("SELECT count(*) FROM knowledge_fts")).scalar() == before
        assert session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.document_id == "new-atomic-test")) is None


@pytest.mark.parametrize("update", [{"license": "proprietary"}, {"document_id": "../escape"}, {"unknown": True}])
def test_document_schema_and_provenance(update):
    doc = bundled_documents()[0].model_dump()
    with pytest.raises(ValidationError):
        DocumentInput.model_validate({**doc, **update})


@pytest.mark.parametrize("reply,code", [("虚构来源[source:invented]", "CITATION_UNKNOWN_SOURCE"),
    ("没有给出来源", "CITATION_REQUIRED"), ("不完整[source:broken", "CITATION_INVALID_FORMAT")])
def test_response_rejects_unbound_citations(index, reply, code):
    _, knowledge = index
    items = asyncio.run(knowledge.search("图书馆预约", "fictional-campus-a"))
    data = ResponseInput(message="图书馆预约", context=ContextOutput(route="KNOWLEDGE_SUPPORT", history=(),
        knowledge=items, retrieval_status="HIT"), risk=RiskOutput(level="LOW", needs_human=False, reason_codes=("NO_SIGNAL",)), attempt=1)
    async def invoke(req):
        return ModelReply(content=reply)
    with pytest.raises(AgentFailure, match=code):
        asyncio.run(ResponseAgent().run(data, invoke))


def test_citation_metadata_hash_is_part_of_approval(index):
    _, knowledge = index
    item = asyncio.run(knowledge.search("图书馆预约", "fictional-campus-a"))[0]
    data = ResponseInput(message="图书馆预约", context=ContextOutput(route="KNOWLEDGE_SUPPORT", history=(),
        knowledge=(item,), retrieval_status="HIT"), risk=RiskOutput(level="LOW", needs_human=False, reason_codes=("NO_SIGNAL",)), attempt=1)
    async def invoke(req):
        return ModelReply(content="预约流程[source:" + item.source_id + "]")
    candidate = asyncio.run(ResponseAgent().run(data, invoke))
    approved = ReviewOutput(candidate_id=candidate.candidate_id, content_hash=candidate.content_hash,
        citations_hash=candidate.citations_hash, verdict="PASS")
    assert valid_binding(candidate, approved)
    assert not valid_binding(candidate, approved.model_copy(update={"citations_hash": "0" * 64}))
    body = candidate.model_dump()
    body["citations"][0]["quote"] = "伪造引用"
    with pytest.raises(ValidationError):
        CandidateOutput.model_validate(body)
