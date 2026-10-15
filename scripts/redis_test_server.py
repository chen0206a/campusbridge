"""Owned, loopback-only ephemeral Redis for D2 tests (no installed services).

Set CAMPUSBRIDGE_TEST_REDIS_COMMAND to a JSON argv prefix, e.g. ["redis-server"].
Passwords exist only in process memory and Redis stdin; persistence is disabled.
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from urllib.parse import quote


class PrivateRedis:
    def __init__(self):
        import redis
        prefix = os.environ.get("CAMPUSBRIDGE_TEST_REDIS_COMMAND")
        self.command = json.loads(prefix) if prefix else [shutil.which("redis-server") or "redis-server"]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.password = secrets.token_urlsafe(32)
        self.client = redis.Redis(host="127.0.0.1", port=self.port, password=self.password,
                                  socket_connect_timeout=.15, socket_timeout=.15,
                                  retry=redis.retry.Retry(redis.backoff.NoBackoff(), 0))
        self.url = "redis://:" + quote(self.password, safe="") + "@127.0.0.1:" + str(self.port) + "/0"
        self.process = None

    def start(self):
        config = f'bind 127.0.0.1\nport {self.port}\nsave ""\nappendonly no\nprotected-mode yes\nrequirepass {self.password}\nmaxmemory 64mb\nmaxmemory-policy noeviction\n'
        self.process = subprocess.Popen([*self.command, "-"], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.process.stdin.write(config.encode())
        self.process.stdin.close()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("PRIVATE_REDIS_START_FAILED")
            try:
                if self.client.ping():
                    return self
            except Exception:
                time.sleep(.1)
        self.stop()
        raise RuntimeError("PRIVATE_REDIS_START_TIMEOUT")

    def stop(self):
        if self.process is None:
            return
        try:
            self.client.shutdown(nosave=True)
        except Exception:
            pass
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            # Terminate only the process created by this object, never a service.
            self.process.terminate()
            self.process.wait(timeout=5)
        self.process = None
        self.client.connection_pool.disconnect()

    def close(self):
        self.stop()
        self.client.close()
