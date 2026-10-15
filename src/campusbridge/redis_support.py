"""Optional Redis infrastructure. No business state or authorization lives here."""
import hashlib
import json
import math
import threading
import logging
from collections import Counter


class Metrics:
    def __init__(self):
        self._counts, self._lock = Counter(), threading.Lock()

    def add(self, name):
        with self._lock:
            self._counts[name] += 1

    def snapshot(self):
        with self._lock:
            counts = dict(self._counts)
        total = counts.get("cache_hit", 0) + counts.get("cache_miss", 0)
        return {**counts, "cache_hit_rate": counts.get("cache_hit", 0) / total if total else None}


class RateLimitError(Exception):
    def __init__(self, code, status, retry_after):
        self.code, self.status, self.retry_after = code, status, retry_after
        super().__init__(code)


# Increment, first expiry and decision are one server-side operation. Never retry
# this operation after an ambiguous timeout: the first execution may have succeeded.
LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('PTTL', KEYS[1])
if count == 1 or ttl < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
  ttl = tonumber(ARGV[2])
end
return {count, ttl}
"""


class RedisSupport:
    def __init__(self, settings, client=None):
        self.settings, self.metrics = settings, Metrics()
        self.enabled = settings.redis_cache_enabled or settings.redis_rate_limit_enabled
        self.client = client
        if self.enabled and client is None:
            import redis
            from redis.backoff import NoBackoff
            from redis.retry import Retry
            self.client = redis.Redis.from_url(settings.redis_url.get_secret_value(),
                socket_timeout=settings.redis_timeout, socket_connect_timeout=settings.redis_timeout,
                max_connections=settings.redis_max_connections, decode_responses=False,
                retry=Retry(NoBackoff(), 0), health_check_interval=30)

    def key(self, kind, dimensions):
        payload = json.dumps(dimensions, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return self.settings.redis_namespace + ":" + kind + ":" + hashlib.sha256(payload.encode()).hexdigest()

    def get(self, key):
        try:
            value = self.client.get(key)
            if value is not None and len(value) > 2_000_000:
                self.metrics.add("cache_invalid")
                return None
            return value
        except Exception:
            self.metrics.add("cache_read_error")
            return None

    def put(self, key, value):
        if len(value) > 2_000_000:
            self.metrics.add("cache_oversized")
            return
        try:
            self.client.set(key, value, ex=self.settings.redis_cache_ttl)
            self.metrics.add("cache_write")
        except Exception:
            self.metrics.add("cache_write_error")

    def limit(self, operation, identity, maximum, window_seconds):
        if not self.settings.redis_rate_limit_enabled:
            return
        key = self.key("limit", {"policy": "fixed-window-v1", "operation": operation, "identity": identity,
                                 "maximum": maximum, "window_seconds": window_seconds})
        try:
            count, ttl = self.client.eval(LIMIT_SCRIPT, 1, key, maximum, window_seconds * 1000)
            if int(ttl) < 0 or int(count) < 1:
                raise ValueError("INVALID_LIMIT_REPLY")
        except Exception:
            self.metrics.add("limit_unavailable")
            raise RateLimitError("RATE_LIMIT_UNAVAILABLE", 503, 1) from None
        if int(count) > maximum:
            self.metrics.add("limit_blocked")
            raise RateLimitError("RATE_LIMIT_EXCEEDED", self.settings.rate_limit_status, max(1, math.ceil(int(ttl) / 1000)))
        self.metrics.add("limit_allowed")

    def login(self, username, peer):
        self.limit("login-peer", peer, self.settings.login_peer_limit, self.settings.login_window_seconds)
        self.limit("login-account", username, self.settings.login_limit, self.settings.login_window_seconds)

    def message(self, identity):
        self.limit("message", [identity.campus_id, identity.account_id], self.settings.message_limit,
                   self.settings.message_window_seconds)

    def close(self):
        if self.enabled:
            logging.getLogger(__name__).info("REDIS_METRICS %s", json.dumps(self.metrics.snapshot(), sort_keys=True))
        if self.client is not None:
            self.client.close()
