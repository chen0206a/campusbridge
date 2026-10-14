"""Save original synthetic normal, rejection and human-follow-up traces without external models."""
import argparse
import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from campusbridge.api import create_app
from campusbridge.contracts import AgentName, ModelReply
from campusbridge.models import MockModel
from campusbridge.security import DEMO_PASSWORD
from campusbridge.settings import Settings


class RejectionDemoModel(MockModel):
    async def complete(self, request):
        if request.task == AgentName.RESPONSE:
            return ModelReply(content="这是拒审演示的候选文本，最终交付应当替换成安全模板。")
        reply = await super().complete(request)
        if request.task == AgentName.REVIEW:
            return ModelReply(content={**reply.content, "verdict": "RETRY", "reason_codes": ["MOCK_REJECTION"]})
        return reply


def login(api, username):
    response = api.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD})
    response.raise_for_status()
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit("Use a new output directory to preserve previous evidence.")
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"model_provider": "MockModel", "external_model_calls": 0, "scenarios": []}
    with tempfile.TemporaryDirectory(prefix="campusbridge-demo-") as work:
        for mode in ("normal", "rejected", "risk-followup"):
            app = create_app(Settings(environment="test", database_path=Path(work) / (mode + ".sqlite3")),
                             RejectionDemoModel() if mode == "rejected" else MockModel())
            with TestClient(app) as api:
                student, counselor = login(api, "student_lan"), login(api, "counselor_yu")
                conversation = api.post("/api/conversations", headers=student).json()["conversation_id"]
                messages = ["我想把接下来一周的学习安排梳理清楚", "我们继续聊刚才的安排"] if mode == "normal" else [
                    "我无法保证自己的安全" if mode == "risk-followup" else "请演示一次审核失败后的流程"]
                for index, message in enumerate(messages):
                    response = api.post(f"/api/conversations/{conversation}/stream",
                        headers={**student, "Idempotency-Key": mode + "-" + str(index)}, json={"message": message})
                    response.raise_for_status()
                    run_id = response.headers["x-run-id"]
                    reply = api.get(f"/api/runs/{run_id}", headers=student).json()
                    trace = api.get(f"/api/runs/{run_id}/trace", headers=counselor).json()
                    name = mode + ("-turn2" if index else "")
                    save(args.output / (name + "-trace.json"), trace)
                    save(args.output / (name + "-reply.json"), reply)
                    (args.output / (name + "-sse.txt")).write_text(response.text, encoding="utf-8", newline="\n")
                    if mode == "rejected":
                        assert "这是拒审演示的候选文本" not in response.text and reply["state"] == "SAFE_FALLBACK"
                    summary["scenarios"].append({"name": name, "run_id": run_id, "state": reply["state"],
                        "model_calls": trace["model_calls"], "agents": [event["result"]["agent"] for event in trace["events"] if event["result"]],
                        "content_hash": reply["content_hash"]})
                if mode == "risk-followup":
                    case = api.get("/api/cases", headers=counselor).json()["cases"][0]
                    for version, action, note in [(0, "claim", "演示辅导员接手"), (1, "process", "已人工联系并记录校内支持安排"),
                                                  (2, "close", "演示跟进完成，记录后续支持渠道")]:
                        acted = api.post(f"/api/cases/{case['case_id']}/{action}",
                            headers={**counselor, "Idempotency-Key": "demo-" + action}, json={"expected_version": version, "note": note})
                        acted.raise_for_status()
                    detail = api.get(f"/api/cases/{case['case_id']}", headers=counselor).json()
                    assert detail["state"] == "CLOSED" and len(detail["events"]) == 4
                    save(args.output / "human-case-closed.json", detail)
                    summary["human_case_final_state"] = detail["state"]
    save(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
