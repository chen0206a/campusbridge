import json

import httpx
from fastapi.testclient import TestClient

from campusbridge.api import create_app
from campusbridge.contracts import AgentName
from campusbridge.provider import OpenAICompatibleProvider
from campusbridge.settings import Settings
from campusbridge.security import DEMO_PASSWORD
from tests.test_provider import KEY, envelope


def login(client, username="student_lan"):
    result = client.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD})
    assert result.status_code == 200
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def turn(client, headers, message, conversation=None, key="stage2"):
    conversation = conversation or client.post("/api/conversations", headers=headers).json()["conversation_id"]
    result = client.post(f"/api/conversations/{conversation}/messages", headers={**headers, "Idempotency-Key": key}, json={"message": message})
    assert result.status_code == 200, result.text
    return result.json()


def test_knowledge_full_chain_citation_source_and_multiturn(tmp_path):
    app = create_app(Settings(environment="test", database_path=tmp_path / "api.sqlite3"))
    with TestClient(app) as client:
        student, counselor = login(client), login(client, "counselor_yu")
        result = turn(client, student, "图书馆预约座位需要做什么？")
        assert result["state"] == "ACCEPTED" and result["citations"]
        source = client.get("/api/knowledge/sources/" + result["citations"][0]["source_id"], headers=student)
        assert source.status_code == 200 and source.json()["text"] == result["citations"][0]["quote"]
        assert client.get("/api/knowledge/sources/" + result["citations"][0]["source_id"]).status_code == 401
        trace = client.get("/api/runs/" + result["run_id"] + "/trace", headers=counselor).json()
        assert [e["result"]["agent"] for e in trace["events"] if e["result"]] == [a.value for a in AgentName]
        assert len(trace["calls"]) == 4 and all(c["output_valid"] for c in trace["calls"])
        assert trace["events"][3]["result"]["artifact"]["payload"]["retrieval_status"] == "HIT"
        second = turn(client, student, "那预约失败之后怎么处理？", result["conversation_id"], "stage2-followup")
        assert second["citations"] and second["state"] == "ACCEPTED"


def test_provider_failure_preserves_risk_case_and_safe_delivery(tmp_path):
    seen = []
    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        user = json.loads(body["messages"][-1]["content"])
        if "candidate" in user:
            return httpx.Response(503, text="private provider body " + KEY)
        if "context" in user:
            return httpx.Response(503, text="private provider body " + KEY)
        if "intent" in user:
            return httpx.Response(200, json=envelope('{"level":"LOW","needs_human":false,"reason_codes":["NO_SIGNAL"]}'))
        return httpx.Response(200, json=envelope('{"intent":"URGENT","needs_knowledge":false}'))
    config = Settings(environment="test", database_path=tmp_path / "fault.sqlite3", model_provider="openai-compatible",
        endpoint="https://provider.example/v1", model="test-model", api_key=KEY)
    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(config, client=transport_client)
    app = create_app(config, model=provider)
    with TestClient(app) as client:
        student, counselor = login(client), login(client, "counselor_yu")
        result = turn(client, student, "我现在无法保证自己的安全，需要有人陪着我。")
        assert result["state"] == "SAFE_FALLBACK" and len(seen) == 3
        cases = client.get("/api/cases", headers=counselor).json()["cases"]
        assert len(cases) == 1 and cases[0]["state"] == "OPEN"
        trace = client.get("/api/runs/" + result["run_id"] + "/trace", headers=counselor).json()
        assert trace["calls"][-1]["error_code"] == "PROVIDER_HTTP_ERROR"
        assert trace["calls"][-1]["status"] == "FAILED" and trace["calls"][0]["input_tokens"] is None
        assert KEY not in json.dumps(trace) and "private provider body" not in json.dumps(trace)
        assert any(e["code"] == "RISK_AND_CASE_COMMITTED" for e in trace["events"])


def test_no_hit_is_explicit(tmp_path):
    app = create_app(Settings(environment="test", database_path=tmp_path / "none.sqlite3"))
    with TestClient(app) as client:
        headers = login(client)
        result = turn(client, headers, "校园服务里的量子舰船校准许可")
        assert result["state"] == "ACCEPTED" and result["citations"] == [] and "没有检索到" in result["reply"]
