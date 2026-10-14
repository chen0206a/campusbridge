from fastapi.testclient import TestClient
from sqlalchemy import select

from campusbridge.db import Account, Student
from campusbridge.security import DEMO_PASSWORD, password_hash
from campusbridge.settings import Settings
from campusbridge.web_api import create_web_app


def login(client, username="student_lan"):
    result = client.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD})
    assert result.status_code == 200
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def test_session_profile_logout_and_invalid_token(tmp_path):
    app = create_web_app(Settings(environment="test", database_path=tmp_path / "session.sqlite3"))
    with TestClient(app) as client:
        headers = login(client)
        result = client.get("/api/auth/me", headers=headers)
        assert result.json()["display_name"] == "学生澜" and result.json()["role"] == "student"
        assert "password" not in result.text and "token" not in result.text
        assert client.post("/api/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/auth/me", headers=headers).status_code == 401
        assert client.post("/api/auth/logout", headers=headers).status_code == 401


def test_list_conversations_scoped_to_authenticated_student(tmp_path):
    app = create_web_app(Settings(environment="test", database_path=tmp_path / "list.sqlite3"))
    with TestClient(app) as client:
        first, second, counselor = login(client), login(client, "student_he"), login(client, "counselor_yu")
        conv = client.post("/api/conversations", headers=first).json()["conversation_id"]
        response = client.post(f"/api/conversations/{conv}/messages", headers={**first, "Idempotency-Key": "original"}, json={"message": "图书馆预约座位"})
        assert response.status_code == 200
        items = client.get("/api/conversations", headers=first).json()["conversations"]
        assert len(items) == 1 and items[0]["title"] == "图书馆预约座位" and items[0]["message_count"] == 2
        assert client.get("/api/conversations", headers=second).json()["conversations"] == []
        assert client.get("/api/conversations", headers=counselor).status_code == 403
        assert client.get("/api/conversations").status_code == 401
        assert client.get(f"/api/conversations/{conv}", headers=second).status_code == 404


def test_case_student_context_is_counselor_and_campus_scoped(tmp_path):
    app = create_web_app(Settings(environment="test", database_path=tmp_path / "case.sqlite3"))
    with TestClient(app) as client:
        student, counselor = login(client), login(client, "counselor_yu")
        conv = client.post("/api/conversations", headers=student).json()["conversation_id"]
        client.post(f"/api/conversations/{conv}/messages", headers={**student, "Idempotency-Key": "attention"}, json={"message": "我需要有人帮我整理支持安排"})
        case = client.get("/api/cases", headers=counselor).json()["cases"][0]
        path = f"/api/cases/{case['case_id']}/student-context"
        result = client.get(path, headers=counselor)
        assert result.status_code == 200 and result.json()["student_display_name"] == "学生澜"
        assert result.json()["request_text"] == "我需要有人帮我整理支持安排"
        assert client.get(path, headers=student).status_code == 403
        with app.state.database.sessions.begin() as session:
            session.add(Account(id="synthetic-foreign", username="foreign_counselor", role="counselor", campus_id="other-campus", password_hash=password_hash(DEMO_PASSWORD)))
        assert client.get(path, headers=login(client, "foreign_counselor")).status_code == 404


def test_existing_sse_references_cases_and_conflict_still_work(tmp_path):
    app = create_web_app(Settings(environment="test", database_path=tmp_path / "sse.sqlite3"))
    with TestClient(app) as client:
        student, counselor = login(client), login(client, "counselor_yu")
        conv = client.post("/api/conversations", headers=student).json()["conversation_id"]
        response = client.post(f"/api/conversations/{conv}/stream", headers={**student, "Idempotency-Key": "web-citation"}, json={"message": "图书馆预约流程"})
        assert response.status_code == 200 and "event: token" in response.text and "event: done" in response.text
        run = response.headers["x-run-id"]
        assert client.get(f"/api/runs/{run}", headers=student).json()["citations"]
        client.post(f"/api/conversations/{conv}/stream", headers={**student, "Idempotency-Key": "web-risk"}, json={"message": "我想找辅导员讨论校园生活"})
        case = client.get("/api/cases", headers=counselor).json()["cases"][0]
        claimed = client.post(f"/api/cases/{case['case_id']}/claim", headers={**counselor, "Idempotency-Key": "claim-web"}, json={"expected_version": 0, "note": ""})
        assert claimed.status_code == 200
        stale = client.post(f"/api/cases/{case['case_id']}/process", headers={**counselor, "Idempotency-Key": "stale-web"}, json={"expected_version": 0, "note": "联系学生"})
        assert stale.status_code == 409 and stale.json()["detail"] == "STALE_CASE_VERSION"


def test_static_build_and_api_routing(tmp_path):
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html><title>CampusBridge web fixture</title></html>", encoding="utf-8")
    app = create_web_app(Settings(environment="test", database_path=tmp_path / "static.sqlite3"), static_directory=static)
    with TestClient(app) as client:
        assert "CampusBridge web fixture" in client.get("/").text
        assert client.get("/health").status_code == 200 and client.get("/docs").status_code == 200
        assert client.get("/api/auth/me").status_code == 401
