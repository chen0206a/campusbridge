"""Stage B: freeze -> Dev selection -> one formal comparison -> verify."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from campusbridge.rag_b_evaluation import freeze, run, tune, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "tune", "run", "verify"))
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--model-path", type=Path)
    args = parser.parse_args()
    if args.command == "freeze": result = freeze(ROOT, args.packet)
    elif args.command == "verify": result = verify(ROOT, args.packet)
    elif args.command == "tune": result = tune(ROOT, args.packet, args.output, args.model_path)
    else: result = run(ROOT, args.packet, args.selection, args.output, args.model_path)
    if args.command != "run": print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
