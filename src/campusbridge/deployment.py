"""Single-instance container bootstrap, credential loading, migration and readiness."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import sys

from sqlalchemy.engine import URL
from sqlalchemy import text

from campusbridge.db import Database, KnowledgeDatabase
from campusbridge.schema import migrate, verify_schema
from campusbridge.settings import Settings

ROLES = ("root", "app", "migrator", "redis")


def secret_path(role):
    return Path("/run/cb-" + role) / "password"


def read_secret(path):
    value = Path(path).read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
        raise RuntimeError("DEPLOYMENT_SECRET_INVALID")
    return value


@contextmanager
def bootstrap_lock(path):
    with open(path, "a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if not stream.read(1): stream.write(b"1"); stream.flush()
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def bootstrap(paths=None):
    paths = paths or {role: secret_path(role) for role in ROLES}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o755)
    with bootstrap_lock(paths["root"].parent / "bootstrap.lock"):
        for role, path in paths.items():
            if path.exists():
                read_secret(path)
                continue
            temporary = path.with_suffix(".new")
            if temporary.exists():
                temporary.chmod(0o600)
                temporary.unlink()
            temporary.write_text(secrets.token_urlsafe(36), encoding="ascii")
            temporary.chmod(0o444)
            os.replace(temporary, path)
    return {"status": "ready", "credentials_created_or_reused": len(paths)}


def configure_credentials(role="app", paths=None):
    paths = paths or {name: secret_path(name) for name in ROLES}
    user = "campus_app" if role == "app" else "campus_migrator"
    url = URL.create("mysql+pymysql", username=user, password=read_secret(paths[role]),
                     host="mysql", port=3306, database="campusbridge", query={"charset": "utf8mb4"})
    name = "CAMPUSBRIDGE_DATABASE_URL" if role == "app" else "CAMPUSBRIDGE_MIGRATION_DATABASE_URL"
    os.environ[name] = url.render_as_string(hide_password=False)
    if role == "app":
        os.environ["CAMPUSBRIDGE_REDIS_URL"] = "redis://:" + read_secret(paths["redis"]) + "@redis:6379/0"


def migrate_business():
    configure_credentials("migrator")
    db = Database(url=os.environ["CAMPUSBRIDGE_MIGRATION_DATABASE_URL"], scope="business", schema="none")
    try:
        # Same database-level advisory lock as D1 Runtime: no migration can race
        # another migrator or a live backend. Upgrade deployments stop backend first.
        db.acquire_runtime_owner()
        migrate(db.engine)
        return verify_schema(db.engine)
    finally:
        db.close()


def ready(app):
    try:
        app.state.database.assert_runtime_owner()
        with app.state.database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        knowledge = app.state.knowledge_database
        if knowledge is not None:
            with knowledge.engine.connect() as connection:
                if connection.exec_driver_sql("PRAGMA quick_check").scalar() != "ok":
                    raise RuntimeError("KNOWLEDGE_DATABASE_INTEGRITY_FAILED")
        if app.state.redis_support.settings.redis_rate_limit_enabled:
            app.state.redis_support.client.ping()
        return True
    except Exception:
        return False


def diagnose():
    """Read-only deployment evidence; contains no credentials or user content."""
    import hashlib
    from campusbridge.redis_support import RedisSupport
    configure_credentials()
    config = Settings.from_environment()
    if not config.knowledge_file.is_file(): raise RuntimeError("KNOWLEDGE_DATABASE_MISSING")
    business = Database.business(config)
    knowledge = KnowledgeDatabase(config.knowledge_file)
    support = RedisSupport(config)
    try:
        result = {"schema": verify_schema(business.engine)}
        with business.engine.connect() as conn:
            result["mysql_version"] = conn.exec_driver_sql("SELECT VERSION()").scalar()
            result["business_counts"] = {name: conn.exec_driver_sql("SELECT COUNT(*) FROM " + name).scalar()
                for name in ("accounts", "conversations", "agent_runs", "risk_events", "support_cases", "case_events")}
        with knowledge.engine.connect() as conn:
            result["knowledge_quick_check"] = conn.exec_driver_sql("PRAGMA quick_check").scalar()
            rows = [tuple(r) for r in conn.exec_driver_sql("SELECT source_id,content_hash FROM knowledge_chunks ORDER BY source_id")]
            result["knowledge_chunks"] = len(rows)
            result["knowledge_identity"] = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
            result["active_documents"] = [dict(r) for r in conn.exec_driver_sql(
                "SELECT campus_id,document_id,revision,content_hash FROM knowledge_documents WHERE active=1 ORDER BY id").mappings()]
        client = support.client
        result["redis_version"] = client.info("server")["redis_version"]
        result["redis_memory_bytes"] = client.info("memory")["used_memory"]
        result["redis_persistence"] = {k:client.info("persistence")[k] for k in ("aof_enabled", "aof_last_write_status")}
        result["retrieval_cache_entries"] = sum(1 for _ in client.scan_iter(config.redis_namespace + ":retrieval:*"))
        return result
    finally:
        support.close(); knowledge.close(); business.close()


def create_deployment_app():
    from fastapi.responses import JSONResponse
    from campusbridge.web_api import create_web_app
    configure_credentials()
    config = Settings.from_environment()
    app = create_web_app(config)
    # Dense initialization happens before accepting requests; default BM25 loads
    # neither ML packages nor weights.
    from campusbridge.retrieval_cache import CachedKnowledge
    from campusbridge.dense_retrieval import SemanticKnowledge, RetrievalUnavailable
    from campusbridge.db import KnowledgeDocument
    from sqlalchemy import select
    knowledge = app.state.knowledge
    inner = knowledge.inner if isinstance(knowledge, CachedKnowledge) else knowledge
    if isinstance(inner, SemanticKnowledge):
        with inner.db.sessions() as session:
            campuses = list(session.scalars(select(KnowledgeDocument.campus_id).where(KnowledgeDocument.active.is_(True)).distinct()))
        for campus in campuses:
            try: inner.build_index(campus)
            except RetrievalUnavailable:
                if inner.config.fallback != "bm25": raise

    @app.get("/readyz")
    def readiness():
        ok = ready(app)
        return JSONResponse({"ready": ok}, status_code=200 if ok else 503)
    from campusbridge.diagnostics import register_metrics
    register_metrics(app)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("bootstrap", "migrate", "serve", "verify", "diagnose", "import-knowledge"))
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            result = bootstrap()
        elif args.command == "migrate":
            result = migrate_business()
        elif args.command == "verify":
            configure_credentials()
            db = Database.business(Settings.from_environment())
            try: result = verify_schema(db.engine)
            finally: db.close()
        elif args.command == "diagnose":
            result = diagnose()
        elif args.command == "import-knowledge":
            # Import remains a single-instance maintenance operation. No model or
            # business state change; original immutable revision rules apply.
            if args.file is None: raise ValueError("KNOWLEDGE_FILE_REQUIRED")
            from campusbridge.retrieval import SQLiteKnowledge
            config = Settings.from_environment()
            db = KnowledgeDatabase(config.knowledge_file)
            try:
                result = SQLiteKnowledge(db).import_documents(json.loads(args.file.read_text(encoding="utf8")))
            finally: db.close()
        else:
            import uvicorn
            uvicorn.run("campusbridge.deployment:create_deployment_app", factory=True, host="0.0.0.0", port=8000,
                        workers=1, proxy_headers=False, log_level="info", access_log=False)
            return
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        # Never serialize URL/env/secret values or arbitrary third-party errors.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__": main()
