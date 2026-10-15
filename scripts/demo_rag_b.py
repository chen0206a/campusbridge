"""Real local embeddings + MockModel API/SSE/citation demo; no generative API."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fastapi.testclient import TestClient
from campusbridge.api import create_app
from campusbridge.dense_retrieval import LocalBGE
from campusbridge.rag_evaluation import checksums, write_json
from campusbridge.security import DEMO_PASSWORD
from campusbridge.settings import Settings
from serve_rag import warm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError("DEMO_ALREADY_EXISTS")
    args.output.mkdir(parents=True)
    embedder = LocalBGE(args.model_path)
    summary = []
    with tempfile.TemporaryDirectory() as temp:
        for mode in ("dense", "hybrid"):
            app = create_app(Settings(environment="test", database_path=Path(temp) / f"{mode}.sqlite3",
                orchestration="dynamic", retrieval_mode=mode, dense_min_score=args.threshold))
            app.state.knowledge.embedder = embedder
            warmed = warm(app)
            with TestClient(app) as client:
                def login(username):
                    token = client.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD}).json()["access_token"]
                    return {"Authorization": "Bearer " + token}
                student, counselor = login("student_lan"), login("counselor_yu")
                conversation = client.post("/api/conversations", headers=student).json()["conversation_id"]
                response = client.post(f"/api/conversations/{conversation}/stream", headers={**student, "Idempotency-Key": mode},
                    json={"message": "图书馆座位预约需要准备哪些信息？"})
                response.raise_for_status()
                run_id = response.headers["x-run-id"]
                reply = client.get(f"/api/runs/{run_id}", headers=student).json()
                trace = client.get(f"/api/runs/{run_id}/trace", headers=counselor).json()
                assert reply["state"] == "ACCEPTED" and reply["citations"], reply["state"]
                for citation in reply["citations"]:
                    source = client.get("/api/knowledge/sources/" + citation["source_id"], headers=student).json()
                    assert source["text"] == citation["quote"] and source["active_revision"]
                target = args.output / mode
                target.mkdir()
                write_json(target / "reply.json", reply)
                write_json(target / "trace.json", trace)
                (target / "sse.txt").write_text(response.text, encoding="utf-8", newline="\n")
                summary.append({"mode": mode, "state": reply["state"], "citations": len(reply["citations"]),
                    "warmed": warmed, "model_provider": "mock", "embedding": "real_local_bge_cpu", "external_model_calls": 0})
    write_json(args.output / "summary.json", summary)
    checksums(args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
