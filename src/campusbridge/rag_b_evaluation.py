"""Frozen Stage B retrieval-only comparison. Dev selects; Holdout never selects."""
import asyncio
import importlib.metadata
import json
import platform
import shutil
import statistics
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from campusbridge.agents import ContextAgent
from campusbridge.contracts import ArbitrationInput, ContextInput, IntentOutput, RiskOutput
from campusbridge.db import Database
from campusbridge.dense_retrieval import LocalBGE, RetrievalConfig, SemanticKnowledge, rrf
from campusbridge.orchestration import Arbitrator, retrieval_query
from campusbridge.rag_evaluation import (
    assert_hit_identity, checksums, load_cases, percentile, resolve_labels, safe_child,
    score_hits, sha, source_bindings, write_json, write_jsonl,
)
from campusbridge.retrieval import SQLiteKnowledge

DATASET = "eval/rag/20261015-stage-b-v1"
STAGES = ("ranking_top5", "context_top3", "dynamic_top3")


def code_bindings(root):
    bound = source_bindings(root)
    for name in ("evaluate_rag_b.py", "prepare_rag_b.py", "fetch_embedding.py"):
        bound["scripts/" + name] = sha(Path(root) / "scripts" / name)
    return bound


def validate_checksums(directory):
    directory = Path(directory)
    listed = set()
    for line in (directory / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        if name in listed or sha(safe_child(directory, name)) != expected:
            raise ValueError("FROZEN_HASH_MISMATCH:" + name)
        listed.add(name)
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt"}
    if listed != actual:
        raise ValueError("FROZEN_FILE_SET_MISMATCH")


def freeze(root, packet):
    root, packet = Path(root), Path(packet)
    if packet.exists():
        raise ValueError("PACKET_ALREADY_EXISTS")
    validate_checksums(root / DATASET)
    shutil.copytree(root / DATASET, packet)
    write_json(packet / "source-bindings.json", code_bindings(root))
    checksums(packet)
    return verify(root, packet)


def verify(root, packet):
    packet = Path(packet)
    validate_checksums(packet)
    if code_bindings(root) != json.loads((packet / "source-bindings.json").read_text(encoding="utf-8")):
        raise ValueError("SOURCE_BINDING_MISMATCH")
    return {"passed": True, "packet_sha256": sha(packet / "SHA256SUMS.txt"), "generative_model_calls": 0}


def scores(hits, label, *, requested_depth, error=False):
    """@K is valid only when the retriever requested at least K candidates.

    Fewer than K returned after exhaustion/thresholding is still a valid @K.
    A configured top3 view has no Recall@5 and reports null, never top3 as top5.
    """
    result = score_hits(hits, label, error=error)
    for k in (1, 3, 5):
        if requested_depth < k:
            for prefix in ("recall", "document_recall", "hit"):
                result[f"{prefix}@{k}"] = None
    ids = [h.source_id if hasattr(h, "source_id") else h["source_id"] for h in hits[:3]]
    result["top3_relevant"] = len(set(ids) & set(label["relevant_source_ids"]))
    result["top3_returned"] = len(ids)
    return result


def aggregate(rows, stage):
    entries = [r["scores"][stage] for r in rows]
    answerable = [r["scores"][stage] for r in rows if not r["reference"]["no_answer"]]
    result = {"queries": len(rows), "answerable": len(answerable), "exceptions": sum(bool(r.get("error")) for r in rows)}
    for key in ("recall@1", "recall@3", "recall@5", "document_recall@1", "document_recall@3", "document_recall@5",
                "reciprocal_rank", "top1_source_correct", "evidence_group_coverage@3"):
        values = [s[key] for s in answerable if s[key] is not None]
        result["mrr_at_5" if key == "reciprocal_rank" else key] = statistics.mean(values) if values else None
    denominator = sum(e["top3_returned"] for e in entries)
    result["top3_source_precision_micro_all_queries"] = sum(e["top3_relevant"] for e in entries) / denominator if denominator else None
    for group, kinds in (("no_material", {"outside_scope", "campus_isolation"}), ("insufficient_evidence", {"unsupported_fact"}),
                         ("all_legacy_no_answer", {"outside_scope", "campus_isolation", "unsupported_fact"})):
        subset = [r["scores"][stage] for r in rows if r["no_answer_kind"] in kinds]
        rate = statistics.mean(s["no_answer_correct"] for s in subset) if subset else None
        result[group] = {"count": len(subset), "empty_rejection_rate": rate,
            "returned_material_rate": None if rate is None else 1 - rate}
    return result


def summaries(rows):
    warm = [ms for r in rows for ms in r["warm_ms"]]
    return {"overall": {s: aggregate(rows, s) for s in STAGES},
        "by_group": {g: {s: aggregate([r for r in rows if r["group"] == g], s) for s in STAGES} for g in sorted({r["group"] for r in rows})},
        "warm_latency_ms": {"observations": len(warm), "p50": percentile(warm, .5), "p95": percentile(warm, .95)},
        "source_identity_valid_rate": statistics.mean(not r["error"] for r in rows) if rows else None,
        "insufficient_evidence_detection": "Not inferred from similarity; returned material remains unverified for requested facts",
        "evaluation_kind": "offline_retrieval_no_generation", "generative_model_calls": 0}


def index(packet, temp):
    db = Database(Path(temp) / "index.sqlite3")
    SQLiteKnowledge(db).import_documents(json.loads((Path(packet) / "corpus.json").read_text(encoding="utf-8")))
    return db


def cases_labels(db, packet, split):
    packet = Path(packet)
    cases = load_cases(packet / f"{split}.jsonl")
    labels = [json.loads(line) for line in (packet / f"{split}-references.jsonl").read_text(encoding="utf-8").splitlines()]
    if resolve_labels(db, cases) != labels:
        raise ValueError("REFERENCE_OR_SOURCE_ID_DRIFT")
    return cases, labels


async def tune_async(db, packet, embedder, protocol):
    cases, labels = cases_labels(db, packet, "dev")
    lexical = SQLiteKnowledge(db, top_k=protocol["candidate_k"])
    dense = SemanticKnowledge(lexical, RetrievalConfig(mode="dense", top_k=20, dense_min_score=-1), embedder)
    build = dense.build_index("fictional-campus-a")
    raw = []
    for case, label in zip(cases, labels, strict=True):
        query = retrieval_query(case.query, case.history)
        raw.append({"case_id": case.case_id, "group": case.group, "query": query, "reference": label,
            "no_answer_kind": case.no_answer_kind, "error": None,
            "dense": await dense.search(query, case.campus_id), "bm25": await lexical.search(query, case.campus_id)})
    trials, selected = [], {}
    for mode in ("dense", "hybrid"):
        for threshold in protocol["threshold_grid"]:
            rows = []
            for row in raw:
                candidates = tuple(h for h in row["dense"] if h.score >= threshold)
                if mode == "hybrid":
                    candidates = rrf((row["bm25"], candidates), protocol["rrf_k"])
                rows.append({**row, "scores": {"ranking_top5": scores(candidates[:5], row["reference"], requested_depth=5)}})
            summary = aggregate(rows, "ranking_top5")
            utility = .5 * summary["recall@3"] + .5 * summary["all_legacy_no_answer"]["empty_rejection_rate"]
            trials.append({"mode": mode, "threshold": threshold, "utility": utility, "metrics": summary})
        selected[mode] = max((t for t in trials if t["mode"] == mode), key=lambda t: (t["utility"], t["threshold"]))["threshold"]
    serial = [{**r, "dense": [h.model_dump(mode="json") for h in r["dense"]],
        "bm25": [h.model_dump(mode="json") for h in r["bm25"]]} for r in raw]
    return {"thresholds": selected, "trials": trials, "model_identity": embedder.identity,
            "model_load_seconds": embedder.load_seconds, "index_build": build}, serial


def tune(root, packet, output, model_path):
    verify(root, packet)
    output = Path(output)
    if output.exists():
        raise ValueError("SELECTION_ALREADY_EXISTS")
    embedder = LocalBGE(model_path)
    protocol = json.loads((Path(packet) / "protocol.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as temp:
        db = index(packet, temp)
        try:
            result, rows = asyncio.run(tune_async(db, packet, embedder, protocol))
        finally:
            db.close()
    verify(root, packet)
    output.mkdir(parents=True)
    write_jsonl(output / "dev-candidates.jsonl", rows)
    write_json(output / "selection.json", {**result, "selection_split": "dev_only", "packet_sha256": sha(Path(packet) / "SHA256SUMS.txt"),
        "dev_candidates_sha256": sha(output / "dev-candidates.jsonl"), "formal_sets_not_evaluated": True})
    checksums(output)
    return result


class TopThree:
    def __init__(self, knowledge): self.knowledge = knowledge
    async def search(self, query, campus_id): return (await self.knowledge.search(query, campus_id))[:3]


async def evaluate(db, knowledge, cases, labels, repeats):
    rows = []
    intent = IntentOutput(intent="CAMPUS_SERVICE", needs_knowledge=True)
    risk = RiskOutput(level="LOW", needs_human_support=False, reason_codes=("NO_SIGNAL",))
    context_agent = ContextAgent(TopThree(knowledge))
    for case, label in zip(cases, labels, strict=True):
        query = retrieval_query(case.query, case.history)
        hits, context_hits, arbitrated, latencies, error, decision = (), (), (), [], None, None
        try:
            hits = await knowledge.search(query, case.campus_id)
            for _ in range(repeats):
                started = time.perf_counter()
                repeated = await knowledge.search(query, case.campus_id)
                latencies.append((time.perf_counter() - started) * 1000)
                if tuple(h.source_id for h in repeated) != tuple(h.source_id for h in hits):
                    raise ValueError("UNSTABLE_RETRIEVAL")
            for hit in hits:
                assert_hit_identity(knowledge, hit, case.campus_id)
            context = await context_agent.run(ContextInput(message=case.query, history=case.history,
                intent=intent, risk=risk, campus_id=case.campus_id))
            if tuple(h.source_id for h in context.knowledge) != tuple(h.source_id for h in hits[:3]):
                raise ValueError("CONTEXT_VIEW_DRIFT")
            decision = Arbitrator().decide(ArbitrationInput(message=case.query, history=case.history,
                intent=intent, risk=risk, context=context), dynamic=True)
            context_hits, arbitrated = context.knowledge, decision.context.knowledge
        except Exception as exc:
            error = type(exc).__name__ + ":" + str(exc)[:120]
        stages = dict(zip(STAGES, (hits, context_hits, arbitrated), strict=True))
        rows.append({"case_id": case.case_id, "group": case.group, "query": case.query, "effective_query": query,
            "no_answer_kind": case.no_answer_kind, "reference": label,
            "hits": {s: [h.model_dump(mode="json") for h in values] for s, values in stages.items()},
            "scores": {s: scores(values, label, requested_depth=5 if s == STAGES[0] else 3, error=bool(error)) for s, values in stages.items()},
            "arbitration": decision.model_dump(mode="json") if decision else None, "warm_ms": latencies, "error": error,
            "answerability": "RETRIEVED_UNVERIFIED" if hits else "NO_MATERIAL_RETRIEVED"})
    return rows


def environment():
    result = {"python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(),
              "cpu": platform.processor(), "torch_threads": 2, "embedding_device": "cpu", "dtype": "float32"}
    for name in ("torch", "transformers", "numpy", "psutil"):
        result[name] = importlib.metadata.version(name)
    return result


def run(root, packet, selection_dir, output, model_path):
    verify(root, packet)
    validate_checksums(selection_dir)
    output, packet = Path(output), Path(packet)
    if output.exists():
        raise ValueError("FORMAL_RUN_ALREADY_EXISTS")
    selected = json.loads((Path(selection_dir) / "selection.json").read_text(encoding="utf-8"))
    if selected["packet_sha256"] != sha(packet / "SHA256SUMS.txt"):
        raise ValueError("SELECTION_PACKET_MISMATCH")
    protocol = json.loads((packet / "protocol.json").read_text(encoding="utf-8"))
    import psutil
    process = psutil.Process()
    rss_before = process.memory_info().rss
    started = time.perf_counter()
    embedder = LocalBGE(model_path)
    embedder.load()
    if embedder.identity != selected["model_identity"]:
        raise ValueError("SELECTED_MODEL_IDENTITY_MISMATCH")
    rss_loaded = process.memory_info().rss
    output.mkdir(parents=True)
    summaries_all = {}
    with tempfile.TemporaryDirectory() as temp:
        db = index(packet, temp)
        try:
            build = SemanticKnowledge(SQLiteKnowledge(db, top_k=20), RetrievalConfig(mode="dense"), embedder).build_index("fictional-campus-a")
            for split in ("stage-a", "holdout"):
                cases, labels = cases_labels(db, packet, split)
                summaries_all[split] = {}
                for mode in ("bm25", "dense", "hybrid"):
                    config = RetrievalConfig(mode=mode, top_k=5, candidate_k=protocol["candidate_k"],
                        dense_min_score=selected["thresholds"].get(mode, .65), rrf_k=protocol["rrf_k"])
                    lexical = SQLiteKnowledge(db, top_k=5 if mode == "bm25" else config.candidate_k)
                    knowledge = lexical if mode == "bm25" else SemanticKnowledge(lexical, config, embedder)
                    rows = asyncio.run(evaluate(db, knowledge, cases, labels, protocol["timed_repeats"]))
                    summary = {**summaries(rows), "config": asdict(config), "split": split}
                    target = output / split / mode
                    target.mkdir(parents=True)
                    write_jsonl(target / "results.jsonl", rows)
                    write_json(target / "summary.json", summary)
                    checksums(target)
                    summaries_all[split][mode] = summary
                    print(json.dumps({"split": split, "mode": mode, "recall@3": summary["overall"][STAGES[0]]["recall@3"]}), flush=True)
        finally:
            db.close()
    verify(root, packet)
    validate_checksums(selection_dir)
    result = {"splits": summaries_all, "environment": environment(), "model_identity": embedder.identity,
        "model_load_seconds": embedder.load_seconds, "index_build": build, "rss_before_bytes": rss_before,
        "rss_model_loaded_bytes": rss_loaded, "rss_after_bytes": process.memory_info().rss,
        "process_peak_rss_bytes": process.memory_info().peak_wset if hasattr(process.memory_info(), "peak_wset") else None,
        "elapsed_seconds": time.perf_counter() - started, "packet_sha256": sha(packet / "SHA256SUMS.txt"),
        "selection_sha256": sha(Path(selection_dir) / "selection.json"), "hashes_verified_before_after": True,
        "generative_model_calls": 0, "query_embedding_cache": False}
    write_json(output / "comparison.json", result)
    checksums(output)
    return result
