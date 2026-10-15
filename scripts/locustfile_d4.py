"""Real HTTP MockModel workload. Tokens live only in each virtual user's memory."""
import itertools
import json
import os
import time
import uuid
from pathlib import Path

from locust import HttpUser, between, events, task
from locust.exception import StopUser

from d4_contracts import checked_sse, delivered, local_target, summarize

PROFILES = json.loads(Path(os.environ["CB_D4_PROFILES"]).read_text(encoding="utf8"))
OUTPUT = Path(os.environ["CB_D4_OUTPUT"])
COUNTER = itertools.count()
SAMPLES = []
STARTED = time.perf_counter()
COMPLETIONS = 0
INVARIANT_FAILURES = []
QUERIES = ("图书馆预约规则是什么", "宿舍报修怎么登记", "奖助申请需要什么材料", "校园无障碍支持怎么联系")


@events.request.add_listener
def record(request_type, name, response_time, response_length, response=None, exception=None, **kwargs):
    error_type = type(exception).__name__ if exception else None
    row = {"kind": "request", "name": name, "method": request_type, "elapsed_ms": response_time,
           "bytes": response_length, "status": getattr(response, "status_code", 0),
           "failed": exception is not None, "error_type": error_type,
           "timeout": bool(error_type and "timeout" in error_type.lower()),
           "seconds_since_start": time.perf_counter()-STARTED}
    SAMPLES.append(row)


@events.quitting.add_listener
def finish(environment, **kwargs):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    elapsed = time.perf_counter()-STARTED
    (OUTPUT/"requests.jsonl").write_text("".join(json.dumps(r)+"\n" for r in SAMPLES), encoding="utf8")
    summary = {**summarize(SAMPLES, elapsed), "completed_turns_including_replays": COMPLETIONS,
               "turns_per_second_including_replays": COMPLETIONS/elapsed,
               "invariant_failures": INVARIANT_FAILURES, "external_llm_calls": 0}
    (OUTPUT/"summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf8")
    if INVARIANT_FAILURES: environment.process_exit_code = 2


class CampusUser(HttpUser):
    wait_time = between(.1, .3)

    def call(self, method, path, name, body=None, key=None, check=None):
        headers = dict(getattr(self, "auth", {}))
        if key: headers["Idempotency-Key"] = key
        with self.client.request(method, path, json=body, headers=headers, name=name,
                                 timeout=30, catch_response=True) as response:
            try:
                if response.status_code not in {200, 201}: raise ValueError("HTTP_STATUS_"+str(response.status_code))
                value = checked_sse(response.text) if name == "chat_sse" else response.json()
                if check: check(value)
                return value
            except (ValueError, KeyError, TypeError) as exc:
                code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                response.failure(code)
                if response.status_code in {200, 201}: INVARIANT_FAILURES.append({"request":name,"reason":code})
                return None

    def on_start(self):
        local_target(self.host)
        index = next(COUNTER)
        if index >= len(PROFILES): raise StopUser()
        self.profile, self.index, self.step = PROFILES[index], index, 0
        value = self.call("POST", "/api/auth/login", "login", {"username":self.profile["username"], "password":"CampusDemo-2026!"})
        if not value: raise StopUser()
        self.auth = {"Authorization":"Bearer "+value["access_token"]}
        value = self.call("POST", "/api/conversations", "create_conversation")
        if not value: raise StopUser()
        self.conversation = value["conversation_id"]
        self.last, self.last_key, self.last_body = None, None, None

    @task
    def cycle(self):
        global COMPLETIONS
        step = self.step % 6; self.step += 1
        base = "/api/conversations/"+self.conversation
        if step == 0:
            self.call("GET", "/api/conversations", "list_conversations")
        elif step == 1:
            self.last_body = {"message":QUERIES[(self.index+self.step//6)%len(QUERIES)]}
            self.last_key = "d4-"+uuid.uuid4().hex
            self.last = self.call("POST",base+"/messages","chat_rag",self.last_body,self.last_key,delivered)
            if self.last: COMPLETIONS += 1
        elif step == 2 and self.last:
            def replay(value):
                delivered(value)
                if value != self.last: raise ValueError("IDEMPOTENCY_CHANGED")
            if self.call("POST",base+"/messages","idempotent_replay",self.last_body,self.last_key,replay): COMPLETIONS += 1
        elif step == 3:
            self.call("GET",base,"conversation_detail")
        elif step == 4 and self.last and self.last.get("citations"):
            source = self.last["citations"][0]
            def citation(value):
                if value["source_id"] != source["source_id"] or value["source_hash"] != source["source_hash"]:
                    raise ValueError("SOURCE_BINDING_CHANGED")
            self.call("GET","/api/knowledge/sources/"+source["source_id"],"source_detail",check=citation)
        elif step == 5:
            if self.call("POST",base+"/stream","chat_sse",{"message":"最近学习安排有点忙"},"d4-"+uuid.uuid4().hex): COMPLETIONS += 1
        else:
            self.call("GET",base,"conversation_detail")
