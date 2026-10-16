"""Content-free process gauges for local container administration."""
from fastapi import HTTPException, Request


def process_metrics(app):
    pool = app.state.database.engine.pool
    gauges = {}
    for name in ("size", "checkedin", "checkedout", "overflow"):
        method = getattr(pool, name, None)
        if callable(method):
            gauges[name] = method()
    runtime = app.state.runtime
    redis = app.state.redis_support
    memory = None
    if redis.enabled:
        try:
            memory = redis.client.info("memory")["used_memory"]
        except Exception:
            pass  # Metrics collection does not alter cache/limiter failure policy.
    result = {
        "scope": "single_backend_process", "database_pool": gauges,
        "runtime": {"active_jobs": len(runtime.jobs),
                    "conversation_locks": len(runtime.conversation_locks),
                    "locked_conversations": sum(lock.locked() for lock in runtime.conversation_locks.values())},
        "redis": redis.metrics.snapshot(), "redis_memory_bytes": memory,
    }
    profile = getattr(app.state, 'performance', None)
    if profile is not None: result['performance'] = profile.snapshot()
    return result


def register_metrics(app):
    @app.get("/ops/metrics", include_in_schema=False)
    def metrics(request: Request):
        # Not proxied by Nginx. Even a configured frontend peer is not an admin.
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise HTTPException(403, "LOCAL_OPERATIONS_ONLY")
        return process_metrics(app)
