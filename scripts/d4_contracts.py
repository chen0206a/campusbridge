"""Pure checks shared by the HTTP workload and offline regression tests."""
import hashlib
import json
from urllib.parse import urlsplit


def local_target(value):
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password:
        raise ValueError("LOAD_REQUIRES_LOCAL_HTTP_TARGET")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("LOAD_TARGET_MUST_BE_ORIGIN")
    return value.rstrip("/")


def delivered(value):
    if value.get("state") not in {"ACCEPTED", "SAFE_FALLBACK"}:
        raise ValueError("NONFINAL_DELIVERY")
    if hashlib.sha256(value["reply"].encode()).hexdigest() != value["content_hash"]:
        raise ValueError("DELIVERY_HASH_MISMATCH")
    if not value.get("run_id") or not value.get("message_id"):
        raise ValueError("MISSING_DELIVERY_ID")
    return value


def checked_sse(text):
    events = []
    for frame in text.replace("\r\n", "\n").split("\n\n"):
        kind, data = None, []
        for line in frame.splitlines():
            if line.startswith("event: "): kind = line[7:]
            if line.startswith("data: "): data.append(line[6:])
        if kind: events.append((kind, json.loads("\n".join(data))))
    if not events or events[-1][0] != "done" or any(k == "error" for k, _ in events):
        raise ValueError("SSE_NOT_COMPLETED")
    done = events[-1][1]
    reply = "".join(v["text"] for k, v in events if k == "token")
    return delivered({**done, "reply": reply})


def percentile(values, fraction):
    import math
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def summarize(samples, elapsed):
    valid = [row for row in samples if row.get("kind") == "request"]
    latencies = [row["elapsed_ms"] for row in valid]
    failures = sum(row["failed"] for row in valid)
    return {"requests": len(valid), "failures": failures,
            "error_rate": failures / len(valid) if valid else None,
            "timeouts": sum(row.get("timeout", False) for row in valid),
            "timeout_rate": sum(row.get("timeout", False) for row in valid) / len(valid) if valid else None,
            "observed_seconds": elapsed, "rps": len(valid) / elapsed if elapsed else None,
            "p50_ms": percentile(latencies, .5), "p95_ms": percentile(latencies, .95)}
