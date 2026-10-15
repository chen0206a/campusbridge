"""Optional CPU embeddings and version-filtered SQLite cache behind KnowledgePort.

Imports of ML dependencies are deliberately lazy. No network access occurs here.
Cache entries are immutable content-addressed vectors, never evidence membership.
"""
import asyncio
import hashlib
import json
import logging
import math
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from sqlalchemy import select, text

from campusbridge.contracts import digest
from campusbridge.db import KnowledgeChunk, KnowledgeDocument
from campusbridge.retrieval import SQLiteKnowledge

MODEL_ID = "BAAI/bge-small-zh-v1.5"
MODEL_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
logger = logging.getLogger(__name__)


class RetrievalUnavailable(RuntimeError):
    pass


class Embedder(Protocol):
    identity: str
    dimension: int

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]: ...


@dataclass(frozen=True)
class RetrievalConfig:
    mode: Literal["bm25", "dense", "hybrid"] = "bm25"
    top_k: int = 3
    candidate_k: int = 20
    dense_min_score: float = 0.65
    rrf_k: int = 60
    fallback: Literal["error", "bm25"] = "error"

    def __post_init__(self):
        if self.mode not in {"bm25", "dense", "hybrid"} or self.fallback not in {"error", "bm25"}:
            raise ValueError("INVALID_RETRIEVAL_MODE_OR_FALLBACK")
        if not 1 <= self.top_k <= self.candidate_k <= 40 or self.rrf_k < 1 or not -1 <= self.dense_min_score <= 1:
            raise ValueError("INVALID_RETRIEVAL_LIMITS")


class LocalBGE:
    dimension = 512

    def __init__(self, model_path, *, threads=2, batch_size=8):
        self.path = Path(model_path)
        self.threads, self.batch_size = threads, batch_size
        if threads < 1 or batch_size < 1:
            raise ValueError("INVALID_EMBEDDING_BATCH_OR_THREADS")
        self.lock = threading.Lock()
        self.model = None
        self.load_seconds = None
        self.identity = None

    def load(self):
        if self.model is not None:
            return
        with self.lock:
            if self.model is not None:
                return
            started = time.perf_counter()
            try:
                import torch
                from transformers import AutoModel, AutoTokenizer
                required = ("config.json", "tokenizer.json", "tokenizer_config.json", "vocab.txt", "model.safetensors")
                hashes = {}
                for name in required:
                    with (self.path / name).open("rb") as stream:
                        hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
                self.identity = digest(json.dumps({"model": MODEL_ID, "files": hashes, "pooling": "cls-l2",
                    "instruction": QUERY_INSTRUCTION, "max_length": 512}, sort_keys=True, ensure_ascii=False))
                torch.set_num_threads(self.threads)
                self.tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True)
                loaded_model = AutoModel.from_pretrained(self.path, local_files_only=True,
                    use_safetensors=True).to("cpu").eval()
                self.torch = torch
                self.load_seconds = time.perf_counter() - started
                self.model = loaded_model
            except (ImportError, OSError, ValueError, RuntimeError) as exc:
                raise RetrievalUnavailable("EMBEDDING_MODEL_UNAVAILABLE:" + type(exc).__name__) from exc

    def encode(self, texts, *, query=False):
        self.load()
        if not texts:
            return []
        values = [QUERY_INSTRUCTION + value if query else value for value in texts]
        result = []
        with self.lock, self.torch.inference_mode():
            for start in range(0, len(values), self.batch_size):
                batch = self.tokenizer(values[start:start + self.batch_size], padding=True,
                    truncation=True, max_length=512, return_tensors="pt")
                vectors = self.model(**batch).last_hidden_state[:, 0]
                vectors = self.torch.nn.functional.normalize(vectors, p=2, dim=1)
                result.extend(vectors.tolist())
        return result


def unit(vector, dimension):
    if len(vector) != dimension or not all(math.isfinite(v) for v in vector):
        raise RetrievalUnavailable("INVALID_VECTOR_DIMENSION_OR_VALUES")
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        raise RetrievalUnavailable("ZERO_VECTOR")
    return [v / norm for v in vector]


def rrf(rankings, k=60):
    """Equal-weight RRF; one vote per source per list; stable source-ID tie break."""
    scores, items = {}, {}
    for ranking in rankings:
        seen = set()
        for position, item in enumerate(ranking, 1):
            if item.source_id in seen:
                continue
            seen.add(item.source_id)
            items[item.source_id] = item
            scores[item.source_id] = scores.get(item.source_id, 0) + 1 / (k + position)
    return tuple(items[sid].model_copy(update={"score": scores[sid]})
                 for sid in sorted(scores, key=lambda sid: (-scores[sid], sid)))


class SemanticKnowledge:
    def __init__(self, lexical, config, embedder):
        self.lexical, self.db, self.config, self.embedder = lexical, lexical.db, config, embedder
        self.index_lock = threading.Lock()
        self.last_index = None
        self.fallback_count = 0
        with self.db.engine.begin() as connection:
            connection.exec_driver_sql("""CREATE TABLE IF NOT EXISTS embedding_cache (
                cache_key TEXT PRIMARY KEY, model_identity TEXT NOT NULL, payload_hash TEXT NOT NULL,
                dimension INTEGER NOT NULL, vector BLOB NOT NULL, vector_hash TEXT NOT NULL)""")

    def import_documents(self, documents):
        return self.lexical.import_documents(documents)

    def source(self, source_id, actor):
        return self.lexical.source(source_id, actor)

    def _active(self, campus):
        with self.db.sessions() as session:
            rows = session.execute(select(KnowledgeDocument, KnowledgeChunk).join(
                KnowledgeChunk, KnowledgeChunk.document_key == KnowledgeDocument.id).where(
                KnowledgeDocument.active.is_(True), KnowledgeDocument.campus_id == campus).order_by(KnowledgeChunk.source_id)).all()
            return [self.lexical._item(doc, chunk) for doc, chunk in rows]

    def _vectors(self, items):
        # All model work happens outside database sessions/transactions.
        if hasattr(self.embedder, "load"):
            self.embedder.load()
        if not self.embedder.identity:
            raise RetrievalUnavailable("MISSING_MODEL_IDENTITY")
        payloads = [item.title + "\n" + item.text for item in items]
        hashes = [digest(value) for value in payloads]
        keys = [digest(self.embedder.identity + "\0" + value) for value in hashes]
        vectors, missing = {}, []
        with self.db.engine.connect() as connection:
            for index, key in enumerate(keys):
                row = connection.execute(text("SELECT * FROM embedding_cache WHERE cache_key=:key"), {"key": key}).mappings().first()
                if row:
                    if (row["model_identity"] != self.embedder.identity or row["payload_hash"] != hashes[index]
                            or row["dimension"] != self.embedder.dimension or len(row["vector"]) != 4 * row["dimension"]
                            or hashlib.sha256(row["vector"]).hexdigest() != row["vector_hash"]):
                        raise RetrievalUnavailable("CORRUPT_EMBEDDING_CACHE")
                    vectors[key] = unit(struct.unpack("<" + "f" * row["dimension"], row["vector"]), self.embedder.dimension)
                else:
                    missing.append(index)
        if missing:
            encoded = self.embedder.encode([payloads[i] for i in missing])
            if len(encoded) != len(missing):
                raise RetrievalUnavailable("EMBEDDING_BATCH_LENGTH_MISMATCH")
            pending = []
            for index, vector in zip(missing, encoded, strict=True):
                vector = unit(vector, self.embedder.dimension)
                blob = struct.pack("<" + "f" * len(vector), *vector)
                # Use persisted float32 precision in both cold and warm searches.
                vectors[keys[index]] = unit(struct.unpack("<" + "f" * len(vector), blob), len(vector))
                pending.append({"key": keys[index], "model": self.embedder.identity, "payload": hashes[index],
                    "dimension": len(vector), "vector": blob, "hash": hashlib.sha256(blob).hexdigest()})
            with self.db.engine.begin() as connection:
                connection.execute(text("INSERT OR IGNORE INTO embedding_cache VALUES(:key,:model,:payload,:dimension,:vector,:hash)"), pending)
        self.last_index = {"active_chunks": len(items), "new_embeddings": len(missing), "cache_hits": len(items) - len(missing)}
        return [vectors[key] for key in keys]

    def build_index(self, campus):
        started = time.perf_counter()
        with self.index_lock:
            items = self._active(campus)
            if items:
                self._vectors(items)
            else:
                self.last_index = {"active_chunks": 0, "new_embeddings": 0, "cache_hits": 0}
        return {**self.last_index, "seconds": time.perf_counter() - started}

    def _dense(self, query, campus):
        with self.index_lock:
            items = self._active(campus)
            if not items:
                return ()
            vectors = self._vectors(items)
            query_vectors = self.embedder.encode([query[:500]], query=True)
            if len(query_vectors) != 1:
                raise RetrievalUnavailable("QUERY_EMBEDDING_LENGTH_MISMATCH")
            query_vector = unit(query_vectors[0], self.embedder.dimension)
            hits = [item.model_copy(update={"score": sum(x*y for x, y in zip(vector, query_vector, strict=True))})
                    for item, vector in zip(items, vectors, strict=True)]
            return tuple(sorted((h for h in hits if h.score >= self.config.dense_min_score),
                         key=lambda h: (-h.score, h.source_id))[:self.config.candidate_k])

    async def search(self, query, campus_id):
        if not query.strip() or not any(c.isalnum() for c in query):
            return ()
        if self.config.mode == "bm25":
            return (await self.lexical.search(query, campus_id))[:self.config.top_k]
        try:
            dense = await asyncio.to_thread(self._dense, query, campus_id)
        except RetrievalUnavailable:
            if self.config.fallback != "bm25":
                raise
            self.fallback_count += 1
            logger.warning("DENSE_UNAVAILABLE_EXPLICIT_BM25_FALLBACK")
            return (await self.lexical.search(query, campus_id))[:self.config.top_k]
        # Re-read active identities after encoding; a concurrent import cannot expose retired sources.
        current = {h.source_id: h for h in self._active(campus_id)}
        dense = tuple(h for h in dense if h.source_id in current and h.source_hash == current[h.source_id].source_hash)
        if self.config.mode == "hybrid":
            lexical = tuple(h for h in await self.lexical.search(query, campus_id) if h.source_id in current)
            dense = rrf((lexical, dense), self.config.rrf_k)
        return dense[:self.config.top_k]


def configured_knowledge(database, settings):
    lexical = SQLiteKnowledge(database, top_k=settings.knowledge_top_k if settings.retrieval_mode == "bm25" else settings.retrieval_candidate_k)
    if settings.retrieval_mode == "bm25":
        return lexical
    return SemanticKnowledge(lexical, RetrievalConfig(mode=settings.retrieval_mode, top_k=settings.knowledge_top_k,
        candidate_k=settings.retrieval_candidate_k, dense_min_score=settings.dense_min_score, fallback=settings.retrieval_fallback),
        LocalBGE(settings.embedding_model_path, threads=settings.embedding_threads))
