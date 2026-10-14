"""Prepare/verify/run an offline BM25 benchmark; never loads a model or API key."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from campusbridge.rag_evaluation import prepare, run, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--dataset", type=Path, default=ROOT / "eval/rag/20261014-stage-a-v1")
    prep.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("verify")
    check.add_argument("--packet", type=Path, required=True)
    execution = commands.add_parser("run")
    execution.add_argument("--packet", type=Path, required=True)
    execution.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(ROOT, args.dataset, args.output)
    elif args.command == "verify":
        result = verify(ROOT, args.packet)
    else:
        summary = run(ROOT, args.packet, args.output)
        result = {"model_api_calls": 0, "overall": summary["overall"],
                  "warm_retrieval_latency_ms": summary["warm_retrieval_latency_ms"],
                  "elapsed_seconds": summary["elapsed_seconds"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
