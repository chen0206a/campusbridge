"""Actual MySQL + actual Redis coexistence. Opt-in via the private MySQL driver."""
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from campusbridge.api import create_app
from campusbridge.db import RiskEvent, SupportCase
from campusbridge.security import DEMO_PASSWORD
from tests.test_mysql_d1 import mysql_db
from tests.test_redis_d2 import server


def test_mysql_redis_rag_and_risk_business_persistence(mysql_db, server):
    db, _, settings, _, _, _, _ = mysql_db
    config = settings.model_copy(update={"redis_cache_enabled": True, "redis_rate_limit_enabled": True,
        "redis_url": __import__('pydantic').SecretStr(server.url), "redis_namespace": "cb:mysql:d2"})
    with TestClient(create_app(config)) as client:
        token = client.post("/api/auth/login", json={"username": "student_lan", "password": DEMO_PASSWORD}).json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        conv = client.post("/api/conversations", headers=headers).json()["conversation_id"]
        for index in range(2):
            reply = client.post(f"/api/conversations/{conv}/messages", json={"message": "图书馆预约规则是什么"},
                                headers={**headers, "Idempotency-Key": "rag-" + str(index)})
            assert reply.status_code == 200
            assert "[source:" in reply.json()["reply"]
        assert client.app.state.redis_support.metrics.snapshot()["cache_hit"] >= 1
        response = client.post(f"/api/conversations/{conv}/messages", json={"message": "我无法保证自己的安全"},
                               headers={**headers, "Idempotency-Key": "attention"})
        assert response.status_code == 200
        with db.sessions() as session:
            assert session.scalar(select(func.count()).select_from(RiskEvent)) == 1
            assert session.scalar(select(func.count()).select_from(SupportCase)) == 1
