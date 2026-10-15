"""Explicit offline copies. Sources are read-only; targets must be new/empty."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from sqlalchemy import create_engine, func, select, text
from campusbridge.contracts import digest
from campusbridge.db import Base, Database, KnowledgeDatabase, KnowledgeChunk, KnowledgeDocument
from campusbridge.retrieval import SQLiteKnowledge, terms
from campusbridge.schema import verify_schema
from campusbridge.schema_v1 import metadata


@contextmanager
def readonly_source(path):
    path = Path(path).resolve()
    if not path.is_file(): raise ValueError("SOURCE_FILE_REQUIRED")
    guard = Database(path, scope="business", schema="none")
    engine = None
    try:
        guard.acquire_runtime_owner()  # Refuse a copy while this application's SQLite runtime is active.
        engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(path.as_uri()+"?mode=ro", uri=True))
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            yield connection
    finally:
        if engine is not None: engine.dispose()
        guard.close()


def split_knowledge(source_path, target_path):
    target_path = Path(target_path)
    if target_path.exists(): raise ValueError("KNOWLEDGE_TARGET_MUST_BE_NEW")
    with readonly_source(source_path) as source:
        documents = [dict(row) for row in source.execute(select(KnowledgeDocument.__table__)).mappings()]
        chunks = [dict(row) for row in source.execute(select(KnowledgeChunk.__table__)).mappings()]
        parents = {doc["id"]: doc for doc in documents}
        for doc in documents:
            if digest(doc["text"]) != doc["content_hash"]: raise ValueError("SOURCE_DOCUMENT_HASH_INVALID")
        for chunk in chunks:
            doc = parents[chunk["document_key"]]
            if doc["text"][chunk["start"]:chunk["end"]] != chunk["text"] or digest(chunk["text"]) != chunk["content_hash"]:
                raise ValueError("SOURCE_CHUNK_HASH_OR_SPAN_INVALID")
        db = KnowledgeDatabase(target_path)
        try:
            SQLiteKnowledge(db)
            with db.engine.begin() as connection:
                if documents: connection.execute(KnowledgeDocument.__table__.insert(), documents)
                if chunks: connection.execute(KnowledgeChunk.__table__.insert(), chunks)
                for chunk in chunks:
                    connection.execute(text("INSERT INTO knowledge_fts(source_id,terms) VALUES(:sid,:terms)"),
                        {"sid": chunk["source_id"], "terms": " ".join(terms(parents[chunk["document_key"]]["title"]+" "+chunk["text"]))})
                connection.exec_driver_sql("CREATE TABLE knowledge_split_origin(origin_hash TEXT NOT NULL)")
                connection.execute(text("INSERT INTO knowledge_split_origin VALUES(:hash)"),
                    {"hash": digest(fingerprint(documents)+fingerprint(chunks))})
            return {"documents": len(documents), "chunks": len(chunks), "all_revisions_preserved": True,
                "embedding_cache": "rebuild from immutable contents", "source_read_only": True}
        finally: db.close()


def fingerprint(rows):
    values = sorted(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows)
    return digest(json.dumps(values, ensure_ascii=False, separators=(",", ":")))


def mismatch_path(left, right, path="row"):
    """Schema diagnostics only; never return source values or personal identifiers."""
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right): return path+":OBJECT_KEYS"
        for key in sorted(left):
            found = mismatch_path(left[key], right[key], path+"."+key)
            if found: return found
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right): return path+":ARRAY_LENGTH"
        for a,b in zip(left,right):
            found = mismatch_path(a,b,path+"[]")
            if found: return found
    elif left != right: return path+":"+type(left).__name__+"_VALUE_DIFFERENCE"
    elif type(left) is not type(right): return path+":NUMERIC_REPRESENTATION"
    return None


def verify_legacy_knowledge_split(business_engine, knowledge_path):
    if not Path(knowledge_path).is_file(): raise RuntimeError("LEGACY_KNOWLEDGE_SPLIT_REQUIRED")
    with business_engine.connect() as connection:
        documents = [dict(r) for r in connection.execute(select(KnowledgeDocument.__table__)).mappings()]
        chunks = [dict(r) for r in connection.execute(select(KnowledgeChunk.__table__)).mappings()]
    expected = digest(fingerprint(documents)+fingerprint(chunks))
    try:
        with sqlite3.connect(Path(knowledge_path).resolve().as_uri()+"?mode=ro", uri=True) as connection:
            found = connection.execute("SELECT origin_hash FROM knowledge_split_origin").fetchone()
            if found is None or found[0] != expected: raise RuntimeError("LEGACY_KNOWLEDGE_SPLIT_MISMATCH")
    except sqlite3.Error:
        raise RuntimeError("LEGACY_KNOWLEDGE_SPLIT_REQUIRED") from None


def copy_business(source_path, target):
    if not target.is_mysql: raise ValueError("MYSQL_TARGET_REQUIRED")
    verify_schema(target.engine)
    target.acquire_runtime_owner()
    try:
        with readonly_source(source_path) as source, target.engine.begin() as connection:
            tables = metadata().sorted_tables
            for table in tables:
                if connection.scalar(select(func.count()).select_from(table)):
                    raise ValueError("BUSINESS_TARGET_MUST_BE_EMPTY")
            counts, hashes = {}, {}
            for table in tables:
                # Use current type codecs for the HEAD target; immutable migration metadata
                # describes historical DDL, not today's exact JSON bind/result adapters.
                table = Base.metadata.tables[table.name]
                rows = [dict(row) for row in source.execute(select(table)).mappings()]
                counts[table.name] = len(rows)
                if rows: connection.execute(table.insert(), rows)
                copied = [dict(row) for row in connection.execute(select(table)).mappings()]
                if fingerprint(rows) != fingerprint(copied):
                    primary = list(table.primary_key.columns)[0].name
                    original = {r[primary]:r for r in rows}
                    diagnostic = next((mismatch_path(original[r[primary]],r) for r in copied if mismatch_path(original[r[primary]],r)), "SERIALIZATION")
                    raise RuntimeError("COPIED_DATA_MISMATCH:"+table.name+":"+diagnostic)
                hashes[table.name] = fingerprint(rows)
            return {"counts": counts, "table_content_sha256": hashes, "transaction_verified": True,
                "source_read_only": True, "knowledge_transferred_to_mysql": False}
    finally: target.release_runtime_owner()
