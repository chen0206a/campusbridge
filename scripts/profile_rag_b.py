"""Isolated latency replay of fixed rankings, with no parameter selection or scoring."""
import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from campusbridge.dense_retrieval import LocalBGE, RetrievalConfig, SemanticKnowledge
from campusbridge.orchestration import retrieval_query
from campusbridge.rag_b_evaluation import cases_labels, index, validate_checksums, verify
from campusbridge.rag_evaluation import checksums, percentile, sha, write_json, write_jsonl
from campusbridge.retrieval import SQLiteKnowledge


async def measure(knowledge, cases, expected):
    rows = []
    for case, reference in zip(cases, expected, strict=True):
        query = retrieval_query(case.query, case.history)
        original = [h["source_id"] for h in reference["hits"]["ranking_top5"]]
        warm = await knowledge.search(query, case.campus_id)
        assert [h.source_id for h in warm] == original, "FROZEN_RANKING_DRIFT"
        times = []
        for _ in range(3):
            started = time.perf_counter()
            hits = await knowledge.search(query, case.campus_id)
            times.append((time.perf_counter() - started) * 1000)
            assert [h.source_id for h in hits] == original, "FROZEN_RANKING_DRIFT"
        rows.append({"case_id": case.case_id, "warm_ms": times, "ranking_matches_formal": True})
    times = [ms for row in rows for ms in row["warm_ms"]]
    return rows, {"p50": percentile(times, .5), "p95": percentile(times, .95), "observations": len(times)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("packet", "selection", "comparison", "model-path", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError("PROFILE_ALREADY_EXISTS")
    verify(ROOT, args.packet)
    validate_checksums(args.selection)
    validate_checksums(args.comparison)
    selected = json.loads((args.selection / "selection.json").read_text(encoding="utf-8"))
    embedder = LocalBGE(args.model_path)
    embedder.load()
    assert embedder.identity == selected["model_identity"]
    args.output.mkdir(parents=True)
    summary = {}
    with tempfile.TemporaryDirectory() as temp:
        db = index(args.packet, temp)
        try:
            SemanticKnowledge(SQLiteKnowledge(db, 20), RetrievalConfig(mode="dense"), embedder).build_index("fictional-campus-a")
            cases, _ = cases_labels(db, args.packet, "stage-a")
            for mode in ("bm25", "dense", "hybrid"):
                config = RetrievalConfig(mode=mode, top_k=5, dense_min_score=selected["thresholds"].get(mode, .65))
                lexical = SQLiteKnowledge(db, 5 if mode == "bm25" else 20)
                knowledge = lexical if mode == "bm25" else SemanticKnowledge(lexical, config, embedder)
                expected = [json.loads(line) for line in (args.comparison / "stage-a" / mode / "results.jsonl").read_text(encoding="utf-8").splitlines()]
                rows, summary[mode] = asyncio.run(measure(knowledge, cases, expected))
                write_jsonl(args.output / (mode + ".jsonl"), rows)
        finally: db.close()
    write_json(args.output / "summary.json", {"modes": summary, "evaluation_kind": "latency_only_replay",
        "rankings_identical_to_formal": True, "full_backend_suite_running": False, "query_cache": False,
        "packet_sha256": sha(args.packet / "SHA256SUMS.txt"), "selection_sha256": sha(args.selection / "selection.json"),
        "script_sha256": sha(Path(__file__)), "generative_model_calls": 0})
    checksums(args.output)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__": main()
