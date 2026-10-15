"""Real, single-instance Compose acceptance. Never substitutes mocks for Docker.

Operate only a dedicated cb-d3-* project. down preserves volumes; no down -v.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import httpx

from campusbridge.security import DEMO_PASSWORD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not args.project.startswith("cb-d3-") or args.output.exists():
        parser.error("Use a dedicated cb-d3-* project and new output directory")
    args.output.mkdir(parents=True)
    executable = str(Path(args.docker).resolve()) if Path(args.docker).is_file() else args.docker
    env = os.environ.copy()
    if Path(executable).is_file(): env["PATH"] = str(Path(executable).parent) + os.pathsep + env.get("PATH", "")
    # Acceptance always uses Mock; explicit environment prevents accidental API cost.
    env.update(CAMPUSBRIDGE_MODEL_PROVIDER="mock", CAMPUSBRIDGE_API_KEY="", CAMPUSBRIDGE_WEB_PORT=str(args.port),
               CAMPUSBRIDGE_IMAGE_TAG=args.project)
    result = {"status": "RUNNING", "project": args.project, "model_provider": "mock", "external_llm_calls": 0,
              "timings_seconds": {}, "checks": {}}
    prefix = [executable, "compose", "--project-name", args.project]
    def command(command, *, timeout=1200, input=None):
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=args.root, env=env, input=input, text=True,
                                   encoding="utf8", capture_output=True, timeout=timeout)
        return completed, time.perf_counter() - started
    def compose(*parts, input=None):
        completed, seconds = command([*prefix, *parts], input=input)
        if completed.returncode:
            # Infrastructure logs contain generated secrets only in private volumes;
            # preserve raw tool output externally and review before public export.
            (args.output / "failed-command.txt").write_text(completed.stdout + completed.stderr, encoding="utf8")
            raise RuntimeError("COMPOSE_COMMAND_FAILED:" + parts[0])
        return completed.stdout, seconds
    def wait_healthy():
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            raw, _ = compose("ps", "--all", "--format", "json")
            try:
                parsed = json.loads(raw)
                rows = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
            indexed = {r["Service"]: r for r in rows}
            services = ("mysql", "redis", "backend", "frontend")
            if all(indexed.get(s, {}).get("Health") == "healthy" for s in services):
                assert all(indexed.get(s, {}).get("ExitCode") == 0 for s in ("bootstrap", "migrate"))
                return indexed
            time.sleep(.5)
        raise RuntimeError("HEALTH_DEADLINE_EXCEEDED")
    def execute(code):
        raw, _ = compose("exec", "-T", "backend", "python", "-c", code)
        return json.loads(raw.strip().splitlines()[-1])
    def diagnose():
        raw, _ = compose("exec", "-T", "backend", "python", "-m", "campusbridge.deployment", "diagnose")
        return json.loads(raw.strip().splitlines()[-1])
    def redis_probe(write=False):
        return execute("from campusbridge.deployment import configure_credentials; configure_credentials(); "
            "from campusbridge.redis_support import RedisSupport; from campusbridge.settings import Settings; "
            "import json; s=RedisSupport(Settings.from_environment()); " +
            ("s.client.set('d3:acceptance:persistence','verified',ex=900); " if write else "") +
            "print(json.dumps({'value':s.client.get('d3:acceptance:persistence').decode(),"
            "'ttl':s.client.ttl('d3:acceptance:persistence')})); s.close()")
    try:
        version, _ = command([executable, "version", "--format", "{{json .}}"], timeout=30)
        if version.returncode:
            result.update(status="BLOCKED", reason="DOCKER_ENGINE_UNAVAILABLE")
            return
        result["docker"] = json.loads(version.stdout)
        info, _ = command([executable, "info", "--format", "{{json .}}"], timeout=30)
        data = json.loads(info.stdout)
        result["engine_resources"] = {k:data.get(k) for k in ("NCPU", "MemTotal", "OSType", "Architecture", "Driver")}
        compose("config", "--quiet")
        existing, _ = compose("ps", "--all", "--quiet")
        if existing.strip(): raise RuntimeError("CLEAN_ACCEPTANCE_PROJECT_NOT_EMPTY")
        started = time.perf_counter()
        log, _ = compose("up", "--build", "-d")
        (args.output / "build-start.txt").write_text(log, encoding="utf8")
        result["containers_initial"] = wait_healthy()
        result["timings_seconds"]["clean_build_to_healthy"] = time.perf_counter() - started
        before = diagnose()
        result["initial_diagnostics"] = before
        assert before["schema"]["verified"] and before["schema"]["revision"] == "d1_0003"
        assert before["redis_persistence"]["aof_enabled"] == 1 and before["knowledge_quick_check"] == "ok"
        result["checks"]["schema_redis_rag"] = True
        with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=90, trust_env=False) as client:
            def get(path, headers=None):
                response = client.get(path, headers=headers);response.raise_for_status();return response.json()
            def post(path, body=None, headers=None):
                response = client.post(path, json=body, headers=headers);response.raise_for_status();return response.json()
            def login(name):
                return {"Authorization": "Bearer " + post("/api/auth/login", {"username":name,"password":DEMO_PASSWORD})["access_token"]}
            assert client.get("/").status_code == client.get("/docs").status_code == 200
            assert "/api/conversations" in get("/openapi.json")["paths"]
            student, other, counselor = login("student_lan"), login("student_he"), login("counselor_yu")
            conv = post("/api/conversations", headers=student)["conversation_id"]
            body = {"message":"图书馆预约规则是什么"}
            request_headers = {**student,"Idempotency-Key":"compose-rag"}
            qa = post(f"/api/conversations/{conv}/messages", body, request_headers)
            assert qa["citations"] and "[source:" in qa["reply"]
            assert qa == post(f"/api/conversations/{conv}/messages", body, request_headers)
            qa2 = post(f"/api/conversations/{conv}/messages", body, {**student,"Idempotency-Key":"compose-rag-cache"})
            assert qa2["citations"]
            old = qa["citations"][0]
            detail = get("/api/knowledge/sources/" + old["source_id"], student)
            assert detail["source_hash"] == hashlib.sha256(old["quote"].encode()).hexdigest() and detail["active_revision"]
            assert client.get(f"/api/conversations/{conv}",headers=other).status_code in (403,404)
            assert client.get("/api/cases",headers=student).status_code == 403
            # Source retrieval does not trust arbitrary citation metadata supplied by client.
            assert client.get("/api/knowledge/sources/forged-source",headers=student).status_code == 404
            chunks=[];events=[];start=time.perf_counter()
            with client.stream("POST",f"/api/conversations/{conv}/stream",headers={**student,"Idempotency-Key":"compose-risk"},
                               json={"message":"我无法保证自己的安全"}) as response:
                response.raise_for_status();run=response.headers["x-run-id"]
                for line in response.iter_lines():
                    if line: events.append({"line":line,"elapsed_ms":(time.perf_counter()-start)*1000})
                    if line.startswith("event: "): chunks.append(line[7:])
            assert "token" in chunks and "done" in chunks and "error" not in chunks
            trace = get(f"/api/runs/{run}/trace",counselor)
            result["risk_trace"] = trace
            result["sse_events"] = events
            cases = get("/api/cases",counselor)["cases"]
            case = next(c for c in cases if get("/api/cases/"+c["case_id"],counselor)["run_id"] == run)
            states=[case["state"]]
            for version,action in enumerate(("claim","process","close")):
                payload={"expected_version":version,"note":"容器验收：已记录虚构学生的人工跟进安排"}
                headers={**counselor,"Idempotency-Key":"compose-"+action}
                updated=post(f"/api/cases/{case['case_id']}/{action}",payload,headers)
                replay=post(f"/api/cases/{case['case_id']}/{action}",payload,headers)
                assert replay["idempotent_replay"] and replay["version"] == updated["version"]
                states.append(updated["state"])
            assert states == ["OPEN","CLAIMED","IN_PROGRESS","CLOSED"]
            result["checks"].update(http_auth_rag_sse=True,permissions=True,idempotency=True,case_states=states,trace=True)
            # Exercise a live immutable revision update without changing bundled data.
            execute("from campusbridge.settings import Settings; from campusbridge.db import KnowledgeDatabase; "
                "from campusbridge.retrieval import SQLiteKnowledge,bundled_documents; import json; "
                "db=KnowledgeDatabase(Settings.from_environment().knowledge_file); "
                "d=bundled_documents()[0].model_copy(update={'revision':'compose-acceptance-v2',"
                "'text':'图书馆预约新规则：请通过校园预约页面登记，携带学生证到馆确认。'}); "
                "print(json.dumps(SQLiteKnowledge(db).import_documents([d]))); db.close()")
            qa3=post(f"/api/conversations/{conv}/messages",body,{**student,"Idempotency-Key":"compose-rag-updated"})
            assert qa3["citations"] and old["source_id"] not in {c["source_id"] for c in qa3["citations"]}
            assert get("/api/knowledge/sources/"+old["source_id"],student)["active_revision"] is False
            result["checks"]["revision_and_cache_invalidation"] = True
            result["persistence_marker_before"] = redis_probe(True)
            durable=diagnose(); result["diagnostics_before_restart"]=durable
            assert durable["retrieval_cache_entries"] >= 1
            for name,parts in [("backend_redis_restart",("restart","backend","redis")),("down_up_preserving_volumes",("down",))]:
                started=time.perf_counter();compose(*parts)
                if name.startswith("down"): compose("up","-d")
                wait_healthy();result["timings_seconds"][name]=time.perf_counter()-started
                restored=diagnose()
                assert restored["business_counts"] == durable["business_counts"]
                assert restored["knowledge_identity"] == durable["knowledge_identity"]
                assert get(f"/api/cases/{case['case_id']}",counselor)["state"] == "CLOSED"
                assert get(f"/api/conversations/{conv}",student)
                marker=redis_probe();assert marker["value"] == "verified" and marker["ttl"] > 0
                result[name]={"diagnostics":restored,"marker":marker,"verified":True}
        images,_=compose("images","--format","json");result["images"]=json.loads(images)
        result["status"]="PASSED"
    except FileNotFoundError:
        result.update(status="BLOCKED",reason="DOCKER_EXECUTABLE_MISSING")
    except Exception as exc:
        result.update(status="FAILED",failure_type=type(exc).__name__,reason=str(exc))
        raise
    finally:
        (args.output / "result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8",newline="\n")
        print(json.dumps({"status":result["status"],"timings_seconds":result["timings_seconds"]}))


if __name__ == "__main__": main()
