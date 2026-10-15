"""D2 local retrieval microbenchmark; no LLM calls and no load/stress test."""
import argparse
import asyncio
import hashlib
import json
import math
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from campusbridge.db import KnowledgeDatabase
from campusbridge.dense_retrieval import LocalBGE, RetrievalConfig, SemanticKnowledge
from campusbridge.redis_support import RedisSupport
from campusbridge.retrieval import SQLiteKnowledge, bundled_documents
from campusbridge.retrieval_cache import CachedKnowledge
from campusbridge.settings import Settings
from redis_test_server import PrivateRedis

QUERIES = ("图书馆预约", "宿舍维修", "心理咨询预约", "奖学金申请", "学业支持", "zzzzunlistedquery")


def percentiles(rows):
    values = sorted(r["milliseconds"] for r in rows)
    return {"samples": len(values), "p50_ms": statistics.median(values),
            "p95_ms": values[math.ceil(.95 * len(values)) - 1]}


async def measure(knowledge, repetitions):
    rows = []
    cpu = time.process_time()
    for index in range(repetitions):
        for query in QUERIES:
            start = time.perf_counter()
            hits = await knowledge.search(query, "fictional-campus-a")
            rows.append({"iteration": index, "query": query, "milliseconds": (time.perf_counter() - start) * 1000,
                         "sources": [h.source_id for h in hits]})
    return {**percentiles(rows), "process_cpu_seconds": time.process_time() - cpu, "rows": rows}


def main():
    import psutil
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True, help="Existing local BGE model; never downloads")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=10)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    protected = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for folder in (ROOT / "src", ROOT / "tests", ROOT / "scripts") for p in folder.rglob("*.py")}
    server = PrivateRedis().start()
    process = psutil.Process()
    before = process.memory_info().rss
    result = {"protocol": "d2-retrieval-microbenchmark-v1", "external_llm_calls": 0,
              "platform": platform.platform(), "python": platform.python_version(),
              "redis_version": server.client.info("server")["redis_version"], "queries": list(QUERIES),
              "timing": "sequential local SQLite + real Redis over loopback; CPU embeddings; warm model/index",
              "source_hashes": protected, "modes": {}}
    try:
        with tempfile.TemporaryDirectory(prefix="campusbridge-d2-bench-") as work:
            db = KnowledgeDatabase(Path(work) / "knowledge.sqlite3")
            try:
                lexical = SQLiteKnowledge(db)
                documents = bundled_documents()
                lexical.import_documents(documents)
                result["knowledge_documents"] = len(documents)
                result["knowledge_hash"] = hashlib.sha256(json.dumps([d.model_dump() for d in documents],
                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                start = time.perf_counter()
                embedder = LocalBGE(args.model, threads=2)
                embedder.load()
                result["embedding_load_seconds"] = time.perf_counter() - start
                result["embedding_identity"] = embedder.identity
                result["embedding_rss_delta_bytes"] = process.memory_info().rss - before
                for mode in ("bm25", "dense", "hybrid"):
                    settings = Settings(database_path=Path(work) / "business.sqlite3", redis_cache_enabled=True,
                        redis_url=server.url, redis_namespace="cb:bench:" + mode, redis_cache_ttl=120)
                    support = RedisSupport(settings)
                    retrieval = lexical if mode == "bm25" else SemanticKnowledge(SQLiteKnowledge(db, top_k=20),
                        RetrievalConfig(mode=mode, dense_min_score=.5), embedder)
                    build = retrieval.build_index("fictional-campus-a") if mode != "bm25" else None
                    # All paths share a warmed model/index; the first network connection
                    # is established before timing to isolate candidate caching cost.
                    server.client.ping(); support.client.ping()
                    asyncio.run(measure(retrieval, 1))
                    origin = asyncio.run(measure(retrieval, args.repetitions))
                    cache = CachedKnowledge(retrieval, support)
                    memory_before = server.client.info("memory")["used_memory"]
                    rss_before = process.memory_info().rss
                    cold = asyncio.run(measure(cache, 1))
                    warm = asyncio.run(measure(cache, args.repetitions))
                    for rows in (cold["rows"], warm["rows"]):
                        assert all(row["sources"] == origin["rows"][index % len(QUERIES)]["sources"] for index, row in enumerate(rows))
                    keys = list(support.client.scan_iter(settings.redis_namespace + ":retrieval:*"))
                    cache_bytes = sum(support.client.memory_usage(key) or 0 for key in keys)
                    result["modes"][mode] = {"origin": origin, "cold_cache": cold, "warm_cache": warm,
                        "metrics": support.metrics.snapshot(), "index_build": build, "cache_keys": len(keys),
                        "redis_key_memory_bytes": cache_bytes,
                        "redis_used_memory_delta_bytes": server.client.info("memory")["used_memory"] - memory_before,
                        "process_rss_delta_during_cache_bytes": process.memory_info().rss - rss_before,
                        "candidate_identity_equal": True}
                    support.close()
                result["redis_memory"] = {k: server.client.info("memory")[k] for k in ("used_memory", "used_memory_peak", "maxmemory")}
                result["redis_cpu"] = server.client.info("cpu")
                result["process_rss_bytes"] = process.memory_info().rss
                result["process_peak_rss_bytes"] = process.memory_info().peak_wset if sys.platform == "win32" else None
            finally:
                db.close()
        result["source_hashes_unchanged"] = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in protected.items())
        if not result["source_hashes_unchanged"]: raise RuntimeError("SOURCE_CHANGED_DURING_BENCHMARK")
        (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({mode: {name: {k: v for k, v in data[name].items() if k != "rows"} for name in ("origin", "cold_cache", "warm_cache")}
                          for mode, data in result["modes"].items()}, indent=2))
    finally:
        server.close()


if __name__ == "__main__": main()
