import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from campusbridge.api import create_app
from campusbridge.contracts import AgentName, Identity, StateConflict
from campusbridge.db import Account, AgentRun, CaseEvent, Message, RiskEvent, SupportCase
from campusbridge.policy import SAFE_REPLY
from campusbridge.security import DEMO_PASSWORD, password_hash
from campusbridge.settings import Settings
from tests.helpers import ControlledModel


@pytest.fixture
def api(tmp_path):
    app = create_app(Settings(environment="test", database_path=tmp_path / "api.sqlite3"))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def login(api, username="student_lan"):
    response = api.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def begin(api, headers):
    response = api.post("/api/conversations", headers=headers)
    assert response.status_code == 201
    return response.json()["conversation_id"]


def post(api, conversation_id, headers, message="想聊聊校园生活", key="api-turn", stream=False):
    return api.post(f"/api/conversations/{conversation_id}/{'stream' if stream else 'messages'}",
                    headers={**headers, "Idempotency-Key": key}, json={"message": message})


def decoded(text):
    events = []
    for block in text.strip().split("\n\n"):
        values = dict(line.split(": ", 1) for line in block.splitlines())
        events.append({"event": values["event"], "data": json.loads(values["data"])})
    return events


def risk_case(api):
    student, counselor = login(api), login(api, "counselor_yu")
    conv = begin(api, student)
    response = post(api, conv, student, "我无法保证自己的安全")
    assert response.status_code == 200
    case = api.get("/api/cases", headers=counselor).json()["cases"][0]
    return student, counselor, conv, response.json()["run_id"], case


def test_health_swagger_and_complete_dialogue(api):
    assert api.get("/health").json()["model_provider"] == "mock"
    assert api.get("/docs").status_code == 200
    schema = api.get("/openapi.json").json()
    assert "/api/cases/{case_id}/{action}" in schema["paths"]
    student = login(api)
    conv = begin(api, student)
    first = post(api, conv, student)
    assert first.status_code == 200 and first.json()["state"] == "ACCEPTED"
    second = post(api, conv, student, "继续上一轮的话题", key="second")
    assert second.status_code == 200 and "上一轮" in second.json()["reply"]
    view = api.get(f"/api/conversations/{conv}", headers=student)
    assert len(view.json()["messages"]) == 4
    trace = api.get(f"/api/runs/{first.json()['run_id']}/trace", headers=student).json()
    assert [e["result"]["agent"] for e in trace["events"] if e["result"]] == [agent.value for agent in AgentName]


def test_sse_only_emits_committed_text_and_replay_does_not_call_model(api):
    student = login(api)
    conv = begin(api, student)
    response = post(api, conv, student, stream=True)
    events = decoded(response.text)
    assert [e["event"] for e in events[:2]] == ["meta", "status"]
    assert events[-1]["event"] == "done"
    reply = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    run_id = response.headers["x-run-id"]
    assert api.get(f"/api/runs/{run_id}", headers=student).json()["reply"] == reply
    before = api.get(f"/api/runs/{run_id}/trace", headers=student).json()["model_calls"]
    replay = api.get(f"/api/runs/{run_id}/stream", headers=student)
    assert decoded(replay.text)[-1]["event"] == "done"
    assert api.get(f"/api/runs/{run_id}/trace", headers=student).json()["model_calls"] == before


@pytest.mark.parametrize("review_mode", ["twice_rejected", "exception", "timeout", "mismatch"])
def test_rejected_candidate_absent_from_every_student_response(tmp_path, review_mode):
    rejected = "这段被拒绝的学生候选内容不应交付。"
    model = ControlledModel(texts=[rejected], verdicts=["RETRY"] if review_mode == "twice_rejected" else [],
                            stage=AgentName.REVIEW, mode=review_mode if review_mode != "twice_rejected" else None)
    app = create_app(Settings(environment="test", database_path=tmp_path / "deny.sqlite3", model_timeout=0.01), model)
    with TestClient(app) as api:
        student = login(api)
        conv = begin(api, student)
        response = post(api, conv, student, stream=True)
        run_id = response.headers["x-run-id"]
        user_responses = [response.text, api.get(f"/api/runs/{run_id}", headers=student).text,
                          api.get(f"/api/runs/{run_id}/trace", headers=student).text,
                          api.get(f"/api/conversations/{conv}", headers=student).text]
        assert all(rejected not in text for text in user_responses)
        assert decoded(response.text)[-1]["data"]["state"] == "SAFE_FALLBACK"
        assert "".join(e["data"]["text"] for e in decoded(response.text) if e["event"] == "token") == SAFE_REPLY


def test_risk_case_workflow_and_original_actor_audit(api):
    student, counselor, conv, run_id, case = risk_case(api)
    case_id = case["case_id"]
    assert case["state"] == "OPEN" and case["version"] == 0
    detail = api.get(f"/api/cases/{case_id}", headers=counselor).json()
    assert detail["risk"]["level"] == "HIGH" and detail["run_id"] == run_id
    version = 0
    for action, note, target in [("claim", "我来跟进", "CLAIMED"), ("process", "已联系学生并讨论校内支持安排", "IN_PROGRESS"),
                                 ("close", "已完成人工确认，记录后续联系安排", "CLOSED")]:
        body = {"expected_version": version, "note": note}
        headers = {**counselor, "Idempotency-Key": action + "-once"}
        response = api.post(f"/api/cases/{case_id}/{action}", headers=headers, json=body)
        assert response.status_code == 200
        assert response.json()["state"] == target and response.json()["version"] == version + 1
        replay = api.post(f"/api/cases/{case_id}/{action}", headers=headers, json=body)
        assert replay.json()["idempotent_replay"] and replay.json()["version"] == version + 1
        version += 1
    detail = api.get(f"/api/cases/{case_id}", headers=counselor).json()
    assert len(detail["events"]) == 4 and detail["state"] == "CLOSED"
    assert all(e["actor_id"] == detail["owner_id"] for e in detail["events"][1:])
    assert api.get("/api/cases", headers=student).status_code == 403


def test_case_transition_version_and_key_conflicts(api):
    _, counselor, _, _, case = risk_case(api)
    case_id = case["case_id"]
    assert api.post(f"/api/cases/{case_id}/close", headers={**counselor, "Idempotency-Key": "skip"},
                    json={"expected_version": 0, "note": "跳过处理关闭"}).status_code in {403, 409}
    claimed = api.post(f"/api/cases/{case_id}/claim", headers={**counselor, "Idempotency-Key": "claim"},
                      json={"expected_version": 0})
    assert claimed.status_code == 200
    assert api.post(f"/api/cases/{case_id}/claim", headers={**counselor, "Idempotency-Key": "claim-again"},
                    json={"expected_version": 0}).status_code == 409
    assert api.post(f"/api/cases/{case_id}/process", headers={**counselor, "Idempotency-Key": "empty"},
                    json={"expected_version": 1, "note": " "}).status_code == 409
    assert api.post(f"/api/cases/{case_id}/claim", headers={**counselor, "Idempotency-Key": "claim"},
                    json={"expected_version": 0, "note": "同键不同载荷"}).status_code == 409


def test_no_authentication_or_client_identity_injection(api):
    assert api.post("/api/conversations").status_code == 401
    assert api.post("/api/conversations", headers={"Authorization": "Bearer forged"}).status_code == 401
    assert api.post("/api/auth/login", json={"username": "student_lan", "password": "wrong"}).status_code == 401
    response = api.post("/api/auth/login", json={"username": "student_lan", "password": "private-value", "role": "counselor"})
    assert response.status_code == 422 and "private-value" not in response.text
    student, counselor, conv, run_id, case = risk_case(api)
    forged = api.post(f"/api/cases/{case['case_id']}/claim", headers={**counselor, "Idempotency-Key": "fake-actor"},
                     json={"expected_version": 0, "actor_id": "someone-else"})
    assert forged.status_code == 422
    assert api.post(f"/api/cases/{case['case_id']}/claim", headers={**student, "Idempotency-Key": "student-claim"},
                    json={"expected_version": 0}).status_code == 403
    assert api.post("/api/conversations", headers=counselor).status_code == 403


def test_other_student_cannot_read_or_write_conversation_or_trace(api):
    student, other = login(api), login(api, "student_he")
    conv = begin(api, student)
    response = post(api, conv, student)
    run_id = response.json()["run_id"]
    assert api.get(f"/api/conversations/{conv}", headers=other).status_code == 404
    assert post(api, conv, other).status_code == 404
    assert api.get(f"/api/runs/{run_id}/trace", headers=other).status_code == 404
    assert api.get(f"/api/runs/{run_id}/stream", headers=other).status_code == 404


def add_counselor(api, username, campus="fictional-campus-a"):
    from campusbridge.contracts import identifier
    with api.app.state.database.sessions.begin() as session:
        account = Account(id=identifier(), username=username, password_hash=password_hash(DEMO_PASSWORD),
                          role="counselor", campus_id=campus)
        session.add(account)
    return login(api, username)


def test_other_campus_and_non_owner_counselor(api):
    _, owner, _, run_id, case = risk_case(api)
    outsider = add_counselor(api, "counselor_outside", "fictional-campus-b")
    another = add_counselor(api, "counselor_another")
    assert api.get("/api/cases", headers=outsider).json()["cases"] == []
    assert api.get(f"/api/cases/{case['case_id']}", headers=outsider).status_code == 404
    assert api.get(f"/api/runs/{run_id}/trace", headers=outsider).status_code == 404
    api.post(f"/api/cases/{case['case_id']}/claim", headers={**owner, "Idempotency-Key": "owner-claim"}, json={"expected_version": 0})
    assert api.post(f"/api/cases/{case['case_id']}/process", headers={**another, "Idempotency-Key": "not-owner"},
                    json={"expected_version": 1, "note": "尝试代替负责人员处理"}).status_code == 403


def test_concurrent_claims_have_one_winner(api):
    _, counselor, _, _, case = risk_case(api)
    other = add_counselor(api, "counselor_competing")
    def claim(headers, key):
        return api.post(f"/api/cases/{case['case_id']}/claim", headers={**headers, "Idempotency-Key": key},
                        json={"expected_version": 0}).status_code
    with ThreadPoolExecutor(max_workers=2) as threads:
        futures = [threads.submit(claim, counselor, "claim-one"), threads.submit(claim, other, "claim-two")]
        assert sorted(future.result() for future in futures) == [200, 409]
    assert len(api.get(f"/api/cases/{case['case_id']}", headers=counselor).json()["events"]) == 2


def test_message_idempotency_and_different_input_conflict(api):
    student = login(api)
    conv = begin(api, student)
    first = post(api, conv, student)
    again = post(api, conv, student)
    assert first.json()["run_id"] == again.json()["run_id"]
    assert len(api.get(f"/api/conversations/{conv}", headers=student).json()["messages"]) == 2
    assert post(api, conv, student, "不同输入").status_code == 409
    assert api.post(f"/api/conversations/{conv}/messages", headers=student, json={"message": "少幂等键"}).status_code == 422


def test_tampered_approval_never_replayed_or_returned_in_conversation(api):
    student = login(api)
    conv = begin(api, student)
    response = post(api, conv, student)
    run_id = response.json()["run_id"]
    with api.app.state.database.sessions.begin() as session:
        run = session.get(AgentRun, run_id)
        run.approval = {**run.approval, "content_hash": "0" * 64}
    replay = api.get(f"/api/runs/{run_id}/stream", headers=student)
    assert decoded(replay.text)[-1]["event"] == "error" and "event: token" not in replay.text
    assert api.get(f"/api/conversations/{conv}", headers=student).status_code == 503


def test_sse_commit_failure_has_no_tokens_or_done(tmp_path, monkeypatch):
    app = create_app(Settings(environment="test", database_path=tmp_path / "broken.sqlite3"))
    def fail(*args, **kwargs):
        raise OSError("cannot commit assistant message")
    monkeypatch.setattr(app.state.store, "commit_reply", fail)
    with TestClient(app) as api:
        student = login(api)
        conv = begin(api, student)
        response = post(api, conv, student, stream=True)
        events = decoded(response.text)
        assert events[-1]["event"] == "error" and all(e["event"] not in {"token", "done"} for e in events)
        assert len(api.get(f"/api/conversations/{conv}", headers=student).json()["messages"]) == 1
