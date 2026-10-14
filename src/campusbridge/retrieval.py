"""Versioned imports and Chinese-bigram/English-word lexical retrieval with SQLite FTS5."""
import json
import re
from importlib.resources import files

from pydantic import Field
from sqlalchemy import select, text

from campusbridge.contracts import DataContract, KnowledgeItem, NotFound, StateConflict, digest
from campusbridge.db import KnowledgeChunk, KnowledgeDocument

STOP = {"什么", "怎么", "如何", "可以", "需要", "想要", "请问", "一下", "今天", "我的", "校园", "服务", "哪个", "我要", "怎样", "问题", "自己", "我们", "学校"}


def terms(value):
    result = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]*|[\u3400-\u9fff]+", value.lower()):
        if word.isascii():
            if len(word) >= 3 and word not in {"the", "and", "how", "what", "can", "for"}:
                result.append(word)
        else:
            result.extend(word[i:i + 2] for i in range(len(word) - 1) if word[i:i + 2] not in STOP)
    return tuple(dict.fromkeys(result))


def chunk_text(content, size=350, overlap=40):
    if size < 40 or not 0 <= overlap < size:
        raise ValueError("Invalid chunk settings")
    result, start = [], 0
    while start < len(content):
        end = min(start + size, len(content))
        paragraph = content.rfind("\n\n", start + size // 2, end)
        if end < len(content) and paragraph >= 0:
            end = paragraph + 2
        result.append((start, end, content[start:end]))
        if end == len(content):
            break
        start = max(start + 1, end - overlap)
    return result


class DocumentInput(DataContract):
    document_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    campus_id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=1, max_length=80)
    license: str = Field(pattern=r"^(MIT|CC0-1.0|CC-BY-4.0)$")
    author: str = Field(min_length=1, max_length=160)
    origin: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=50000)


def bundled_documents():
    return [DocumentInput.model_validate(doc) for doc in json.loads(
        files("campusbridge").joinpath("resources/knowledge-v2.json").read_text(encoding="utf-8"))]


class SQLiteKnowledge:
    def __init__(self, database, top_k=3):
        self.db, self.top_k = database, top_k
        with self.db.engine.begin() as connection:
            connection.exec_driver_sql("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(source_id UNINDEXED, terms, tokenize='unicode61')")

    def import_documents(self, documents):
        documents = [DocumentInput.model_validate(doc) for doc in documents]
        if len(documents) > 100:
            raise ValueError("Import batch limit is 100 documents")
        identities = [(doc.document_id, doc.campus_id, doc.revision) for doc in documents]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate document revision in batch")
        added, chunks, unchanged = 0, 0, 0
        with self.db.sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            for doc in documents:
                key = digest("\0".join((doc.campus_id, doc.document_id, doc.revision)))
                old = session.get(KnowledgeDocument, key)
                if old:
                    if any(getattr(old, field) != getattr(doc, field) for field in ("title", "license", "author", "origin", "text")):
                        raise StateConflict("REVISION_IS_IMMUTABLE")
                    unchanged += 1
                    continue  # Bundled older revisions never reactivate over a newer import.
                for prior in session.scalars(select(KnowledgeDocument).where(
                        KnowledgeDocument.document_id == doc.document_id, KnowledgeDocument.campus_id == doc.campus_id)):
                    prior.active = False
                row = KnowledgeDocument(id=key, **doc.model_dump(), content_hash=digest(doc.text), active=True)
                session.add(row)
                session.flush()
                for index, (start, end, value) in enumerate(chunk_text(doc.text)):
                    source_id = doc.document_id + "-" + key[:12] + "-c" + str(index + 1).zfill(3)
                    session.add(KnowledgeChunk(source_id=source_id, document_key=key, chunk_index=index,
                                              start=start, end=end, text=value, content_hash=digest(value)))
                    session.execute(text("INSERT INTO knowledge_fts(source_id,terms) VALUES(:source,:terms)"),
                                    {"source": source_id, "terms": " ".join(terms(doc.title + " " + value))})
                    chunks += 1
                added += 1
        return {"documents_added": added, "chunks_added": chunks, "documents_unchanged": unchanged}

    async def search(self, query, campus_id):
        query_terms = terms(query[:500])[:64]
        if not query_terms:
            return ()
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in query_terms)
        with self.db.sessions() as session:
            rows = session.execute(text("""SELECT knowledge_fts.source_id, bm25(knowledge_fts) AS rank
                FROM knowledge_fts JOIN knowledge_chunks c ON c.source_id=knowledge_fts.source_id
                JOIN knowledge_documents d ON d.id=c.document_key
                WHERE knowledge_fts MATCH :expression AND d.campus_id=:campus AND d.active=1
                ORDER BY rank,knowledge_fts.source_id LIMIT 40"""), {"expression": expression, "campus": campus_id}).all()
            hits = []
            for source_id, rank in rows:
                chunk = session.get(KnowledgeChunk, source_id)
                doc = session.get(KnowledgeDocument, chunk.document_key)
                overlap = set(query_terms) & set(terms(doc.title + " " + chunk.text))
                sufficient = len(overlap) >= 2 or any(term.isascii() for term in overlap) or (
                    len(query_terms) == 1 and query_terms[0] in terms(doc.title))
                if sufficient:
                    hits.append(self._item(doc, chunk, -rank))
                if len(hits) == self.top_k:
                    break
            return tuple(hits)

    @staticmethod
    def _item(doc, chunk, score=None):
        return KnowledgeItem(source_id=chunk.source_id, title=doc.title, text=chunk.text, revision=doc.revision,
            document_id=doc.document_id, source_hash=chunk.content_hash, chunk_index=chunk.chunk_index,
            start=chunk.start, end=chunk.end, score=score)

    def source(self, source_id, actor):
        with self.db.sessions() as session:
            chunk = session.get(KnowledgeChunk, source_id)
            doc = session.get(KnowledgeDocument, chunk.document_key) if chunk else None
            if doc is None or doc.campus_id != actor.campus_id:
                raise NotFound("SOURCE_NOT_FOUND")
            return {**self._item(doc, chunk).model_dump(mode="json"), "license": doc.license, "author": doc.author,
                    "origin": doc.origin, "document_hash": doc.content_hash, "active_revision": doc.active}
