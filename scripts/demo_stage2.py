"""Two complete offline API traces, including a real adapter with an in-memory HTTP transport."""
import argparse
import asyncio
import json
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from campusbridge.api import create_app
from campusbridge.evaluation import sha, write_json
from campusbridge.provider import OpenAICompatibleProvider
from campusbridge.security import DEMO_PASSWORD
from campusbridge.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output directory")
    args.output.mkdir(parents=True)
    summaries = []
    with tempfile.TemporaryDirectory(prefix="campusbridge-stage2-demo-") as temp:
        for scenario in ("knowledge-citation", "provider-failure"):
            config = Settings(environment="test", database_path=Path(temp) / (scenario + ".sqlite3"))
            client_transport = None
            requests = []
            if scenario == "provider-failure":
                config = config.model_copy(update={"model_provider": "openai-compatible", "endpoint": "https://offline.example/v1",
                    "model": "offline-wire-fixture"})
                # Construct a validated fixture with a clearly synthetic credential. No outbound sockets.
                config = Settings(**config.model_dump(), api_key="offline-demonstration-not-a-real-key")
                def handler(request):
                    body = json.loads(request.content)
                    data = json.loads(body["messages"][-1]["content"])
                    requests.append({"model": body["model"], "generation_temperature": body["temperature"]})
                    if "context" in data:
                        return httpx.Response(503, text="in-memory failure fixture")
                    content = '{"level":"LOW","needs_human":false,"reason_codes":["NO_SIGNAL"]}' if "intent" in data else '{"intent":"URGENT","needs_knowledge":false}'
                    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 40, "completion_tokens": 15}})
                client_transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                model = OpenAICompatibleProvider(config, client=client_transport)
            else:
                model = None
            app = create_app(config, model=model)
            with TestClient(app) as api:
                def login(username):
                    logged = api.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD})
                    logged.raise_for_status()
                    return {"Authorization": "Bearer " + logged.json()["access_token"]}
                student, counselor = login("student_lan"), login("counselor_yu")
                conversation = api.post("/api/conversations", headers=student).json()["conversation_id"]
                question = "图书馆预约座位需要做什么？" if scenario == "knowledge-citation" else "我现在无法保证自己的安全，需要有人陪着我。"
                stream = api.post(f"/api/conversations/{conversation}/stream", headers={**student, "Idempotency-Key": scenario}, json={"message": question})
                stream.raise_for_status()
                run_id = stream.headers["x-run-id"]
                reply = api.get(f"/api/runs/{run_id}", headers=student).json()
                trace = api.get(f"/api/runs/{run_id}/trace", headers=counselor).json()
                cases = api.get("/api/cases", headers=counselor).json()
                if scenario == "knowledge-citation":
                    assert reply["state"] == "ACCEPTED" and reply["citations"]
                    source = api.get("/api/knowledge/sources/" + reply["citations"][0]["source_id"], headers=student).json()
                    assert source["text"] == reply["citations"][0]["quote"]
                    write_json(args.output / "knowledge-source.json", source)
                else:
                    assert reply["state"] == "SAFE_FALLBACK" and len(cases["cases"]) == 1
                    assert trace["calls"][-1]["error_code"] == "PROVIDER_HTTP_ERROR"
                    assert "RISK_AND_CASE_COMMITTED" in [e["code"] for e in trace["events"]]
                    write_json(args.output / "provider-failure-case.json", api.get("/api/cases/" + cases["cases"][0]["case_id"], headers=counselor).json())
                write_json(args.output / (scenario + "-reply.json"), reply)
                write_json(args.output / (scenario + "-trace.json"), trace)
                (args.output / (scenario + "-sse.txt")).write_text(stream.text, encoding="utf-8", newline="\n")
                summaries.append({"scenario": scenario, "state": reply["state"], "model_calls": trace["model_calls"],
                    "agents": [e["result"]["agent"] for e in trace["events"] if e["result"]], "citations": len(reply["citations"]),
                    "open_cases": len(cases["cases"]), "wire_requests_in_memory": len(requests), "external_model_requests": 0})
            if client_transport:
                asyncio.run(client_transport.aclose())
    write_json(args.output / "summary.json", {"scenarios": summaries, "external_model_requests": 0})
    (args.output / "SHA256SUMS.txt").write_text("".join(sha(file) + "  " + file.name + "\n" for file in sorted(args.output.iterdir())
        if file.is_file()), encoding="utf-8", newline="\n")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
