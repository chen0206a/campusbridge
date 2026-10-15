"""Offline, frozen lexical-retrieval evaluation. No model/provider is instantiated."""
import asyncio
import hashlib
import json
import math
import platform
import shutil
import sqlite3
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, model_validator
from sqlalchemy import select

from campusbridge.agents import ContextAgent
from campusbridge.contracts import (
    ArbitrationInput, ContextInput, DataContract, HistoryItem, Identity, IntentOutput,
    RiskOutput, digest,
)
from campusbridge.db import Database, KnowledgeChunk, KnowledgeDocument
from campusbridge.orchestration import Arbitrator, retrieval_query
from campusbridge.retrieval import DocumentInput, SQLiteKnowledge, bundled_documents, terms

SLICES = ("exact", "synonym", "alias", "history", "distractor", "cross_paragraph", "no_answer")
CONFIG = {"protocol": "rag-lexical-stage-a-v1", "retriever": "SQLiteKnowledge/FTS5/BM25",
          "ranking_top_k": 5, "production_top_k": 3, "warmups_per_query": 1,
          "timed_repeats_per_query": 7, "query_character_limit": 500, "term_limit": 64,
          "candidate_limit": 40, "chunk_size": 350, "chunk_overlap": 40,
          "no_answer_decision": "no returned evidence (exceptions are failures)",
          "metric_version": "chunk-set-recall-document-set-recall-v1",
          "citation_metric": "retrieved-source relevance/identity; not generated-answer correctness",
          "model_api_calls": 0}


class EvidenceLabel(DataContract):
    document_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class RetrievalCase(DataContract):
    case_id: str = Field(pattern=r"^rag-a-[a-z0-9-]+$")
    group: Literal["exact", "synonym", "alias", "history", "distractor", "cross_paragraph", "no_answer"]
    campus_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=1000)
    history: tuple[HistoryItem, ...] = ()
    no_answer: StrictBool
    no_answer_kind: Literal["none", "outside_scope", "unsupported_fact", "campus_isolation"] = "none"
    evidence: tuple[EvidenceLabel, ...]
    rationale: str = Field(min_length=1)
    source_generation: Literal["original_synthetic"] = "original_synthetic"

    @model_validator(mode="after")
    def coherent(self):
        if self.no_answer != (not self.evidence):
            raise ValueError("NO_ANSWER_EVIDENCE_INCONSISTENT")
        if self.no_answer != (self.group == "no_answer") or self.no_answer != (self.no_answer_kind != "none"):
            raise ValueError("NO_ANSWER_GROUP_INCONSISTENT")
        if self.group == "history" and not any(h.role == "user" for h in self.history):
            raise ValueError("HISTORY_USER_REQUIRED")
        if len(set((e.document_id, e.quote) for e in self.evidence)) != len(self.evidence):
            raise ValueError("DUPLICATE_EVIDENCE")
        return self


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path, values):
    Path(path).write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8", newline="\n")


def load_cases(path):
    cases = [RetrievalCase.model_validate(json.loads(line)) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    keys = [(c.campus_id, c.query, tuple((h.role, h.text) for h in c.history)) for c in cases]
    if not cases or len({c.case_id for c in cases}) != len(cases) or len(set(keys)) != len(keys):
        raise ValueError("EMPTY_OR_DUPLICATE_CASES")
    return cases


def source_bindings(root):
    root = Path(root)
    paths = sorted((root / "src/campusbridge").rglob("*.py"))
    paths += [root / "scripts/evaluate_rag.py", root / "src/campusbridge/resources/knowledge-v2.json"]
    return {p.relative_to(root).as_posix(): sha(p) for p in paths}


def resolve_labels(db, cases):
    """Bind explicit evidence to chunk IDs by original document coordinates, never by search results."""
    labels = []
    with db.sessions() as session:
        for case in cases:
            groups = []
            for label in case.evidence:
                doc = session.scalar(select(KnowledgeDocument).where(
                    KnowledgeDocument.document_id == label.document_id,
                    KnowledgeDocument.campus_id == case.campus_id, KnowledgeDocument.active.is_(True)))
                if doc is None or doc.text.count(label.quote) != 1:
                    raise ValueError("LABEL_NOT_UNIQUE_IN_DECLARED_DOCUMENT:" + case.case_id)
                start = doc.text.index(label.quote)
                end = start + len(label.quote)
                chunks = list(session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_key == doc.id)))
                ids = sorted(c.source_id for c in chunks if c.start <= start and c.end >= end)
                if not ids:
                    raise ValueError("LABEL_QUOTE_CROSSES_CHUNK_BOUNDARY:" + case.case_id)
                groups.append({**label.model_dump(), "document_revision": doc.revision,
                               "document_hash": doc.content_hash, "start": start, "end": end,
                               "acceptable_source_ids": ids})
            labels.append({"case_id": case.case_id, "no_answer": case.no_answer,
                           "relevant_document_ids": sorted({e.document_id for e in case.evidence}),
                           "relevant_source_ids": sorted({sid for g in groups for sid in g["acceptable_source_ids"]}),
                           "evidence_groups": groups})
    return labels


def prepare(root, dataset, output):
    root, dataset, output = Path(root).resolve(), Path(dataset).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("PACKET_ALREADY_EXISTS")
    cases = load_cases(dataset / "queries.jsonl")
    supplemental = [DocumentInput.model_validate(v) for v in json.loads((dataset / "documents.json").read_text(encoding="utf-8"))]
    originals = bundled_documents()
    if {d.document_id for d in originals} & {d.document_id for d in supplemental}:
        raise ValueError("SUPPLEMENT_MUST_NOT_REPLACE_BUNDLED_DOCUMENT")
    documents = originals + supplemental
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "index.sqlite3")
        try:
            knowledge = SQLiteKnowledge(db, top_k=5)
            imported = knowledge.import_documents(documents)
            labels = resolve_labels(db, cases)
        finally:
            db.close()
    # Everything above validates before creating the immutable packet.
    output.mkdir(parents=True)
    for name in ("queries.jsonl", "documents.json", "provenance.json", "DATASET.md"):
        shutil.copyfile(dataset / name, output / name)
    write_json(output / "corpus.json", [d.model_dump() for d in documents])
    write_jsonl(output / "references.jsonl", labels)
    write_json(output / "config.json", CONFIG)
    write_json(output / "source-bindings.json", source_bindings(root))
    payloads = {p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file()}
    write_json(output / "manifest.json", {"protocol": CONFIG["protocol"], "cases": len(cases),
               "groups": dict(Counter(c.group for c in cases)), "bundled_documents": len(originals),
               "supplemental_documents": len(supplemental), "import": imported,
               "annotation": "developer-authored synthetic labels; no independent human adjudication",
               "labels_bound_before_retrieval": True, "files": payloads, "model_api_calls": 0})
    checksums(output)
    return verify(root, output)


def safe_child(root, name):
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()) or path == Path(root).resolve():
        raise ValueError("UNSAFE_MANIFEST_PATH")
    return path


def checksums(directory):
    directory = Path(directory)
    entries = sorted(p for p in directory.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt")
    (directory / "SHA256SUMS.txt").write_text("".join(sha(p) + "  " + p.relative_to(directory).as_posix() + "\n" for p in entries), encoding="utf-8", newline="\n")


def verify(root, packet):
    root, packet = Path(root).resolve(), Path(packet).resolve()
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if sha(safe_child(packet, name)) != expected:
            raise ValueError("PACKET_HASH_MISMATCH:" + name)
    for line in (packet / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        if sha(safe_child(packet, name)) != expected:
            raise ValueError("CHECKSUM_MISMATCH:" + name)
    bound = json.loads((packet / "source-bindings.json").read_text(encoding="utf-8"))
    if source_bindings(root) != bound:
        raise ValueError("SOURCE_BINDING_MISMATCH")
    cases = load_cases(packet / "queries.jsonl")
    if len(cases) != manifest["cases"]:
        raise ValueError("CASE_COUNT_MISMATCH")
    config = json.loads((packet / "config.json").read_text(encoding="utf-8"))
    if config != CONFIG:
        raise ValueError("CONFIG_SEMANTIC_MISMATCH")
    return {"passed": True, "cases": len(cases), "model_api_calls": 0,
            "frozen_packet_and_source_hashes_verified": True}


def score_hits(hits, label, *, error=False):
    ids = [h.source_id if hasattr(h, "source_id") else h["source_id"] for h in hits]
    docs = [h.document_id if hasattr(h, "document_id") else h["document_id"] for h in hits]
    sources, relevant_docs = set(label["relevant_source_ids"]), set(label["relevant_document_ids"])
    scores = {"returned": len(ids), "relevant_returned": len(set(ids) & sources),
              "no_answer_correct": (not error and not ids) if label["no_answer"] else None,
              "reciprocal_rank": next((1 / n for n, sid in enumerate(ids, 1) if sid in sources), 0.0),
              "top1_source_correct": bool(ids and ids[0] in sources)}
    for k in (1, 3, 5):
        scores[f"recall@{k}"] = len(set(ids[:k]) & sources) / len(sources) if sources else None
        scores[f"document_recall@{k}"] = len(set(docs[:k]) & relevant_docs) / len(relevant_docs) if relevant_docs else None
        scores[f"hit@{k}"] = bool(set(ids[:k]) & sources) if sources else None
    groups = label["evidence_groups"]
    scores["evidence_group_coverage@3"] = sum(bool(set(g["acceptable_source_ids"]) & set(ids[:3])) for g in groups) / len(groups) if groups else None
    return scores


def percentile(values, quantile):
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)] if values else None


def aggregate(rows, stage):
    scores = [r["scores"][stage] for r in rows]
    answerable = [s for s in scores if s["recall@1"] is not None]
    negatives = [s for s in scores if s["no_answer_correct"] is not None]
    result = {"queries": len(rows), "answerable": len(answerable), "no_answer": len(negatives)}
    keys = [f"{prefix}@{k}" for prefix in ("recall", "document_recall", "hit") for k in (1, 3, 5)]
    keys += ["reciprocal_rank", "top1_source_correct", "evidence_group_coverage@3"]
    for key in keys:
        values = [s[key] for s in answerable]
        result["mrr" if key == "reciprocal_rank" else key] = statistics.mean(values) if values else None
    result["no_answer_rejection_accuracy"] = statistics.mean(s["no_answer_correct"] for s in negatives) if negatives else None
    returned = sum(s["returned"] for s in scores)
    result["retrieved_source_precision_micro"] = sum(s["relevant_returned"] for s in scores) / returned if returned else None
    result["retrieved_sources"] = returned
    result["exceptions"] = sum(bool(r.get("error")) for r in rows)
    return result


def assert_hit_identity(knowledge, hit, campus):
    source = knowledge.source(hit.source_id, Identity(account_id="rag-offline", role="student", campus_id=campus))
    if not source["active_revision"] or source["source_hash"] != digest(hit.text) or source["text"] != hit.text or source["revision"] != hit.revision:
        raise ValueError("RETRIEVAL_IDENTITY_MISMATCH")


async def evaluate_cases(db, knowledge, cases, labels, config):
    context_agent = ContextAgent(SQLiteKnowledge(db, top_k=config["production_top_k"]))
    intent = IntentOutput(intent="CAMPUS_SERVICE", needs_knowledge=True)
    risk = RiskOutput(level="LOW", needs_human_support=False, reason_codes=("NO_SIGNAL",))
    rows = []
    for case, label in zip(cases, labels, strict=True):
        query = retrieval_query(case.query, case.history)
        error, hits, latencies, cold_ms = None, (), [], None
        started = time.perf_counter()
        try:
            hits = await knowledge.search(query, case.campus_id)
            cold_ms = (time.perf_counter() - started) * 1000
            original_ids = tuple(h.source_id for h in hits)
            for _ in range(config["warmups_per_query"]):
                await knowledge.search(query, case.campus_id)
            for _ in range(config["timed_repeats_per_query"]):
                started = time.perf_counter()
                repeated = await knowledge.search(query, case.campus_id)
                latencies.append((time.perf_counter() - started) * 1000)
                if tuple(h.source_id for h in repeated) != original_ids:
                    raise ValueError("UNSTABLE_RETRIEVAL")
            for hit in hits:
                assert_hit_identity(knowledge, hit, case.campus_id)
            started = time.perf_counter()
            context = await context_agent.run(ContextInput(message=case.query, history=case.history,
                intent=intent, risk=risk, campus_id=case.campus_id))
            context_ms = (time.perf_counter() - started) * 1000
            if context.query_hash != digest(query):
                raise ValueError("CONTEXT_QUERY_DRIFT")
            decision = Arbitrator().decide(ArbitrationInput(message=case.query, history=case.history,
                intent=intent, risk=risk, context=context), dynamic=True)
            context_hits, arbitrated_hits = context.knowledge, decision.context.knowledge
        except Exception as exc:
            error = type(exc).__name__  # No credentials, remote requests or raw private error text.
            context_hits, arbitrated_hits, context_ms, decision = (), (), None, None
        stages = {"ranking_top5": hits, "production_context_top3": context_hits,
                  "dynamic_evidence_top3": arbitrated_hits}
        scores = {name: score_hits(items, label, error=bool(error)) for name, items in stages.items()}
        flags = []
        if error:
            flags.append("EXECUTION_ERROR")
        elif case.no_answer:
            if hits:
                flags.append("UNSUPPORTED_FACT_RETURNED" if case.no_answer_kind == "unsupported_fact" else "NO_ANSWER_FALSE_HIT")
        else:
            if not scores["ranking_top5"]["hit@5"]:
                flags.append("NO_RELEVANT_CANDIDATE_AT_5")
            elif not scores["ranking_top5"]["hit@1"]:
                flags.append("RELEVANT_CANDIDATE_RANKED_BELOW_1")
            if scores["ranking_top5"]["evidence_group_coverage@3"] < 1:
                flags.append("INCOMPLETE_EVIDENCE_AT_3")
            if scores["dynamic_evidence_top3"]["recall@3"] < scores["production_context_top3"]["recall@3"]:
                flags.append("ARBITRATOR_REMOVED_RELEVANT_EVIDENCE")
        rows.append({"case_id": case.case_id, "group": case.group, "query": case.query,
            "history": [h.model_dump() for h in case.history], "effective_query": query,
            "query_terms": terms(query[:500])[:64], "campus_id": case.campus_id,
            "no_answer_kind": case.no_answer_kind, "reference": label,
            "hits": {name: [h.model_dump(mode="json") for h in items] for name, items in stages.items()},
            "scores": scores, "latency": {"first_observation_ms": cold_ms, "warm_ms": latencies,
                                           "context_agent_ms": context_ms},
            "source_identity_valid": not error,
            "arbitration": decision.model_dump(mode="json") if decision else None,
            "failure_flags": flags, "error": error})
    return rows


def run(root, packet, output):
    root, packet, output = Path(root).resolve(), Path(packet).resolve(), Path(output).resolve()
    verify(root, packet)
    if output.exists():
        raise ValueError("RUN_ALREADY_EXISTS")
    cases = load_cases(packet / "queries.jsonl")
    labels = [json.loads(line) for line in (packet / "references.jsonl").read_text(encoding="utf-8").splitlines()]
    config = json.loads((packet / "config.json").read_text(encoding="utf-8"))
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "rag.sqlite3")
        try:
            knowledge = SQLiteKnowledge(db, top_k=config["ranking_top_k"])
            imported = knowledge.import_documents(json.loads((packet / "corpus.json").read_text(encoding="utf-8")))
            if resolve_labels(db, cases) != labels:
                raise ValueError("REFERENCE_COORDINATE_OR_SOURCE_ID_DRIFT")
            rows = asyncio.run(evaluate_cases(db, knowledge, cases, labels, config))
        finally:
            db.close()
    verify(root, packet)
    stages = ("ranking_top5", "production_context_top3", "dynamic_evidence_top3")
    warm = [ms for r in rows for ms in r["latency"]["warm_ms"]]
    summary = {"protocol": config["protocol"], "evaluation_kind": "offline_retrieval_no_generation",
        "model_api_calls": 0, "config": config, "import": imported,
        "environment": {"python": platform.python_version(), "os": platform.system(),
                        "machine": platform.machine(), "sqlite": sqlite3.sqlite_version},
        "elapsed_seconds": time.perf_counter() - started,
        "overall": {stage: aggregate(rows, stage) for stage in stages},
        "by_group": {g: {stage: aggregate([r for r in rows if r["group"] == g], stage) for stage in stages} for g in SLICES},
        "no_answer_by_kind": {g: {stage: aggregate([r for r in rows if r["no_answer_kind"] == g], stage) for stage in stages}
                              for g in ("outside_scope", "unsupported_fact", "campus_isolation")},
        "warm_retrieval_latency_ms": {"observations": len(warm), "p50": percentile(warm, .5), "p95": percentile(warm, .95)},
        "first_observation_latency_ms": {"p50": percentile([r["latency"]["first_observation_ms"] for r in rows if r["latency"]["first_observation_ms"] is not None], .5),
                                        "p95": percentile([r["latency"]["first_observation_ms"] for r in rows if r["latency"]["first_observation_ms"] is not None], .95)},
        "source_identity_valid_rate": statistics.mean(r["source_identity_valid"] for r in rows),
        "failure_flags": dict(Counter(f for r in rows for f in r["failure_flags"])),
        "failed_cases": [r["case_id"] for r in rows if r["failure_flags"]],
        "packet_manifest_sha256": sha(packet / "manifest.json"), "source_bindings_sha256": sha(packet / "source-bindings.json")}
    output.mkdir(parents=True)
    write_jsonl(output / "results.jsonl", rows)
    write_json(output / "summary.json", summary)
    write_json(output / "config.json", config)
    write_json(output / "run-manifest.json", {"packet_manifest_sha256": summary["packet_manifest_sha256"],
                "source_bindings_sha256": summary["source_bindings_sha256"],
                "results_sha256": sha(output / "results.jsonl"), "summary_sha256": sha(output / "summary.json"),
                "model_api_calls": 0, "frozen_verified_before_and_after": True})
    checksums(output)
    return summary
