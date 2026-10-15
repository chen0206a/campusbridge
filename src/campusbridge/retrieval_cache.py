"""Candidate-only cache with a SQLite snapshot as the validity authority.

The second snapshot is the read linearization point. No Redis I/O is performed
inside a SQLite transaction. A changed catalog retries; repeated churn returns
no evidence rather than exposing retired sources.
"""
import asyncio
import hashlib
import json
import math
import unicodedata
import threading
from dataclasses import asdict

from sqlalchemy import text

from campusbridge.contracts import KnowledgeItem, digest
from campusbridge.dense_retrieval import RetrievalUnavailable, SemanticKnowledge


def normalized_query(query):
    # Apply the same normalization to both key and retrieval, preserving case.
    return " ".join(unicodedata.normalize("NFC", query[:500]).split())


class CachedKnowledge:
    def __init__(self, inner, support):
        self.inner, self.support, self.db = inner, support, inner.db
        self._last_version, self._version_lock = None, threading.Lock()

    def import_documents(self, documents):
        result = self.inner.import_documents(documents)
        self.support.metrics.add("catalog_import")
        return result

    def source(self, source_id, actor):
        return self.inner.source(source_id, actor)

    def snapshot(self, campus):
        # Include the global FTS corpus: BM25 IDF can change after an import into
        # another campus. A content digest also distinguishes copied/forked DBs.
        with self.db.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            docs = [dict(r) for r in connection.execute(text("SELECT * FROM knowledge_documents ORDER BY id")).mappings()]
            chunks = [dict(r) for r in connection.execute(text("SELECT * FROM knowledge_chunks ORDER BY source_id")).mappings()]
            fts = [tuple(r) for r in connection.execute(text("SELECT source_id,terms FROM knowledge_fts ORDER BY source_id,terms"))]
        version = hashlib.sha256(json.dumps([docs, chunks, fts], ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        with self._version_lock:
            if self._last_version is not None and self._last_version != version:
                self.support.metrics.add("cache_catalog_change")
            self._last_version = version
        active = {d["id"]: d for d in docs if d["campus_id"] == campus and d["active"]}
        items = {}
        for c in chunks:
            d = active.get(c["document_key"])
            if d is None:
                continue
            if (digest(c["text"]) != c["content_hash"] or digest(d["text"]) != d["content_hash"]
                    or d["text"][c["start"]:c["end"]] != c["text"]):
                raise RetrievalUnavailable("KNOWLEDGE_INTEGRITY_FAILED")
            items[c["source_id"]] = KnowledgeItem(source_id=c["source_id"], title=d["title"], text=c["text"],
                revision=d["revision"], document_id=d["document_id"], source_hash=c["content_hash"],
                chunk_index=c["chunk_index"], start=c["start"], end=c["end"])
        return version, items

    async def dimensions(self, query, campus):
        config = {"mode": "bm25", "top_k": self.inner.top_k} if not isinstance(self.inner, SemanticKnowledge) else asdict(self.inner.config)
        model = None
        if isinstance(self.inner, SemanticKnowledge):
            try:
                if hasattr(self.inner.embedder, "load"):
                    await asyncio.to_thread(self.inner.embedder.load)
                model = self.inner.embedder.identity
                if not model:
                    raise RetrievalUnavailable("EMBEDDING_IDENTITY_REQUIRED")
            except RetrievalUnavailable:
                # Preserve the existing explicit fallback/error policy. Do not
                # make a missing embedding installation appear healthy via Redis.
                return None
        return {"protocol": "candidate-cache-v1", "retrieval_config_version": "stage-b-v1",
                "campus_id": campus, "query_sha256": digest(query), "config": config, "embedding": model}

    @staticmethod
    def valid(items, current):
        ids = [item.source_id for item in items]
        return len(ids) == len(set(ids)) and all(
            item.source_id in current and
            item.model_dump(exclude={"score"}) == current[item.source_id].model_dump(exclude={"score"}) and
            (item.score is None or math.isfinite(item.score)) for item in items)

    async def search(self, query, campus_id):
        query = normalized_query(query)
        if not query:
            return ()
        dimensions = await self.dimensions(query, campus_id)
        if dimensions is None:
            self.support.metrics.add("cache_model_bypass")
            return await self.inner.search(query, campus_id)
        for _ in range(3):
            version, _ = await asyncio.to_thread(self.snapshot, campus_id)
            binding = {**dimensions, "catalog_sha256": version}
            key = self.support.key("retrieval", binding)
            raw = await asyncio.to_thread(self.support.get, key)
            items = None
            if raw is not None:
                try:
                    packet = json.loads(raw)
                    if packet["binding"] != binding or len(packet["items"]) > dimensions["config"]["top_k"]:
                        raise ValueError("CACHE_BINDING_MISMATCH")
                    items = tuple(KnowledgeItem.model_validate(i) for i in packet["items"])
                except (ValueError, TypeError, KeyError):
                    self.support.metrics.add("cache_invalid")
            if items is not None:
                now, current = await asyncio.to_thread(self.snapshot, campus_id)
                if now != version:
                    self.support.metrics.add("cache_version_retry")
                    continue
                if self.valid(items, current):
                    self.support.metrics.add("cache_hit")
                    return items
                self.support.metrics.add("cache_invalid")
            self.support.metrics.add("cache_miss")
            items = tuple(await self.inner.search(query, campus_id))
            now, current = await asyncio.to_thread(self.snapshot, campus_id)
            if now != version:
                self.support.metrics.add("cache_version_retry")
                continue
            if not self.valid(items, current):
                self.support.metrics.add("cache_origin_invalid")
                return ()
            payload = json.dumps({"binding": binding, "items": [i.model_dump(mode="json") for i in items]},
                                 ensure_ascii=False, allow_nan=False).encode()
            await asyncio.to_thread(self.support.put, key, payload)
            return items
        self.support.metrics.add("cache_catalog_churn")
        return ()


def cached_knowledge(knowledge, support):
    if support.settings.redis_cache_enabled and hasattr(knowledge, "db"):
        return CachedKnowledge(knowledge, support)
    return knowledge
