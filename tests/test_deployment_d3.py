import json
from pathlib import Path
import socket

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from campusbridge.api import create_app
from campusbridge.deployment import bootstrap, configure_credentials, read_secret, ready
from campusbridge.settings import Settings
from campusbridge.security import DEMO_PASSWORD
from campusbridge.web_api import create_web_app


@pytest.fixture
def secret_paths(tmp_path):
    return {role: tmp_path / role / "password" for role in ("root", "app", "migrator", "redis")}


def test_bootstrap_reuses_credentials_and_independent_roles(secret_paths):
    assert bootstrap(secret_paths)["status"] == "ready"
    first = {r: p.read_bytes() for r, p in secret_paths.items()}
    assert len(set(first.values())) == 4
    assert bootstrap(secret_paths)["credentials_created_or_reused"] == 4
    assert first == {r: p.read_bytes() for r, p in secret_paths.items()}
    assert all(len(read_secret(p)) >= 32 for p in secret_paths.values())


def test_partial_bootstrap_recovers_without_rotating_existing(secret_paths):
    pending = secret_paths["root"].with_suffix(".new")
    pending.parent.mkdir(parents=True)
    pending.write_text("incomplete"); pending.chmod(0o444)
    bootstrap(secret_paths)
    original = secret_paths["app"].read_bytes()
    secret_paths["redis"].chmod(0o600); secret_paths["redis"].unlink()
    bootstrap(secret_paths)
    assert secret_paths["app"].read_bytes() == original
    assert read_secret(secret_paths["redis"])


@pytest.mark.parametrize("value", ["", "short", "x" * 31, "x" * 129, "x" * 32 + "\nCONFIG", "x" * 32 + "'"])
def test_secret_validation_rejects_injection(secret_paths, value):
    path = secret_paths["root"];path.parent.mkdir();path.write_text(value, encoding="ascii")
    with pytest.raises(RuntimeError, match="SECRET_INVALID"):
        bootstrap(secret_paths)


def test_app_does_not_load_root_or_migrator_credentials(secret_paths, monkeypatch):
    bootstrap(secret_paths)
    # If configuration accidentally reads the privileged files, it fails.
    for role in ("root", "migrator"):
        secret_paths[role].chmod(0o600);secret_paths[role].unlink()
    monkeypatch.setenv("CAMPUSBRIDGE_DATABASE_URL", "previous")
    monkeypatch.setenv("CAMPUSBRIDGE_REDIS_URL", "previous")
    configure_credentials(paths=secret_paths)
    import os
    parsed = make_url(os.environ["CAMPUSBRIDGE_DATABASE_URL"])
    assert parsed.username == "campus_app" and parsed.host == "mysql" and parsed.database == "campusbridge"
    assert parsed.password == read_secret(secret_paths["app"])
    assert os.environ["CAMPUSBRIDGE_REDIS_URL"].endswith("@redis:6379/0")


def proxy_settings(tmp_path):
    return Settings(environment="test", database_path=tmp_path / "business.sqlite3", trusted_proxy_host="frontend")


def dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.28.0.7", 0))])


@pytest.mark.parametrize("web", [False, True])
def test_only_configured_proxy_can_reach_authenticated_routes(tmp_path, monkeypatch, web):
    dns(monkeypatch)
    factory = create_web_app if web else create_app
    app = factory(proxy_settings(tmp_path))
    with TestClient(app, client=("172.28.0.7", 123)) as client:
        login = client.post("/api/auth/login", json={"username": "student_lan", "password": DEMO_PASSWORD}, headers={"X-Real-IP": "127.0.0.1"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        path = "/api/auth/me" if web else "/api/conversations"
        call = client.get if web else client.post
        assert call(path, headers={"Authorization": "Bearer " + token}).status_code in (200, 201)
    # A forged forwarding header never grants transport access.
    with TestClient(factory(proxy_settings(tmp_path)), client=("172.28.0.9", 123)) as rogue:
        assert rogue.post("/api/auth/login", json={"username": "student_lan", "password": DEMO_PASSWORD},
                          headers={"X-Real-IP": "127.0.0.1", "X-Forwarded-For": "172.28.0.7"}).status_code == 403


def test_real_ip_only_from_trusted_peer_and_invalid_header_rejected(tmp_path, monkeypatch):
    from starlette.requests import Request
    from campusbridge.transport import rate_peer
    dns(monkeypatch)
    config = proxy_settings(tmp_path)
    def request(peer, value):
        return Request({"type": "http", "client": (peer, 123), "headers": [(b"x-real-ip", value.encode())]})
    assert rate_peer(request("172.28.0.7", "192.0.2.3"), config) == "192.0.2.3"
    assert rate_peer(request("172.28.0.9", "192.0.2.3"), config) == "172.28.0.9"
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc: rate_peer(request("172.28.0.7", "a,b"), config)
    assert exc.value.status_code == 400


def test_proxy_dns_failure_is_closed(tmp_path, monkeypatch):
    def unavailable(*a, **k): raise socket.gaierror()
    monkeypatch.setattr(socket, "getaddrinfo", unavailable)
    with TestClient(create_app(proxy_settings(tmp_path)), client=("172.28.0.7", 123)) as client:
        assert client.post("/api/auth/login", json={"username": "student_lan", "password": DEMO_PASSWORD}).status_code == 503


def test_default_loopback_access_stays_private(tmp_path):
    config = Settings(environment="test", database_path=tmp_path / "local.sqlite3")
    with TestClient(create_app(config), client=("172.28.0.7", 123)) as client:
        assert client.post("/api/auth/login", json={"username": "student_lan", "password": DEMO_PASSWORD}).status_code == 403
    with pytest.raises(ValidationError): Settings(trusted_proxy_host="frontend/../../")


def test_readiness_checks_real_databases_and_redis_policy(tmp_path):
    config = Settings(environment="test", database_path=tmp_path / "ready.sqlite3")
    app = create_app(config)
    with TestClient(app):
        assert ready(app)
        # Simulated dependency outage checks readiness only, not a container claim.
        app.state.redis_support.settings = config.model_copy(update={"redis_rate_limit_enabled": True})
        assert not ready(app)


def test_compose_security_dependency_and_storage_contracts():
    import yaml
    root = Path(__file__).parents[1]
    config = yaml.safe_load((root / "compose.yaml").read_text(encoding="utf8"))
    services = config["services"]
    assert {"mysql", "redis", "backend", "frontend", "migrate", "bootstrap"} == set(services)
    assert [name for name,s in services.items() if "ports" in s] == ["frontend"]
    assert services["frontend"]["ports"][0].startswith("127.0.0.1:")
    assert services["backend"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["migrate"]["depends_on"]["mysql"]["condition"] == "service_healthy"
    assert services["backend"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert all("healthcheck" in services[s] or s == "frontend" for s in ("mysql", "redis", "backend", "frontend"))
    assert not any("root_credentials" in v or "migrator_credentials" in v for v in services["backend"]["volumes"])
    assert config["networks"]["data"]["internal"]
    assert all(mount.startswith("/") for service in services.values() for mount in service.get("tmpfs", []))
    nginx = (root / "deploy/nginx.conf").read_text()
    assert "proxy_buffering off" in nginx and "proxy_request_buffering off" in nginx
    assert "proxy_set_header X-Real-IP $remote_addr;" in nginx
    assert "workers=1" in (root / "src/campusbridge/deployment.py").read_text()
