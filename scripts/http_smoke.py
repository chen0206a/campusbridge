"""Actual localhost server smoke: Swagger, agents, SSE and human case workflow."""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from campusbridge.security import DEMO_PASSWORD


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a new smoke result path.")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="campusbridge-http-") as work:
        env = {**os.environ, "CAMPUSBRIDGE_ENV": "test", "CAMPUSBRIDGE_DATABASE_PATH": str(Path(work) / "smoke.sqlite3"),
               "CAMPUSBRIDGE_MODEL_PROVIDER": "mock", "CAMPUSBRIDGE_SEED_DEMO": "true", "PYTHONUTF8": "1"}
        process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "campusbridge", "--port", str(port)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15, trust_env=False) as api:
                until = time.monotonic() + 15
                while True:
                    try:
                        health = api.get("/health")
                        health.raise_for_status()
                        break
                    except httpx.TransportError:
                        if process.poll() is not None or time.monotonic() > until:
                            raise RuntimeError("CampusBridge server did not start")
                        time.sleep(0.05)
                docs = api.get("/docs")
                spec = api.get("/openapi.json")
                assert docs.status_code == spec.status_code == 200 and "SwaggerUIBundle" in docs.text
                identities = {}
                for role, username in [("student", "student_lan"), ("counselor", "counselor_yu")]:
                    logged = api.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD})
                    logged.raise_for_status()
                    identities[role] = {"Authorization": "Bearer " + logged.json()["access_token"]}
                student, counselor = identities["student"], identities["counselor"]
                conversation = api.post("/api/conversations", headers=student).json()["conversation_id"]
                reply = api.post(f"/api/conversations/{conversation}/stream", headers={**student, "Idempotency-Key": "wire-risk"},
                                 json={"message": "我无法保证自己的安全"})
                reply.raise_for_status()
                assert "event: done" in reply.text and "event: error" not in reply.text
                run_id = reply.headers["x-run-id"]
                trace = api.get(f"/api/runs/{run_id}/trace", headers=counselor).json()
                agents = [event["result"]["agent"] for event in trace["events"] if event["result"]]
                assert agents == ["IntentAgent", "RiskAgent", "ContextAgent", "ResponseAgent", "OutputReviewer", "Delivery"]
                case = api.get("/api/cases", headers=counselor).json()["cases"][0]
                states = [case["state"]]
                for version, action in enumerate(("claim", "process", "close")):
                    response = api.post(f"/api/cases/{case['case_id']}/{action}",
                        headers={**counselor, "Idempotency-Key": "wire-" + action},
                        json={"expected_version": version, "note": "模拟工作人员记录本轮跟进安排"})
                    response.raise_for_status()
                    states.append(response.json()["state"])
                assert states == ["OPEN", "CLAIMED", "IN_PROGRESS", "CLOSED"]
                result = {"passed": True, "transport": "actual HTTP / uvicorn subprocess", "health": health.json(),
                          "swagger_http_status": docs.status_code, "openapi_http_status": spec.status_code,
                          "agents": agents, "reply_state": trace["state"], "case_states": states, "external_model_calls": 0}
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
        result["own_server_stopped"] = process.poll() is not None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
