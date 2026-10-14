"""Freeze first, evaluate once into a new directory. Mock is always offline."""
import argparse
import json
import os
import statistics
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi.testclient import TestClient
from pydantic import ValidationError

from campusbridge.api import create_app
from campusbridge.evaluation import (GROUPS, ScenarioModel, load_cases, prepare_packet, score_case,
    sha, summarize, verify_packet, write_json)
from campusbridge.contracts import AgentFailure
from campusbridge.contracts import CandidateOutput, ReviewOutput, ExecutionPlan
from campusbridge.policy import permit_delivery
from campusbridge.models import MockModel
from campusbridge.provider import OpenAICompatibleProvider
from campusbridge.security import DEMO_PASSWORD
from campusbridge.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
DEEPESEEK_FLASH_INPUT_PEAK_USD_PER_MILLION = 0.30
DEEPESEEK_FLASH_OUTPUT_PEAK_USD_PER_MILLION = 1.20


class BudgetedProvider:
    """Conservatively reserve worst-case peak cost before each remote call."""
    provider_name = "openai-compatible"

    def __init__(self, delegate, *, max_cost_usd, max_input_bytes=32768, max_output_tokens=1024):
        self.delegate = delegate
        self.max_cost_usd = max_cost_usd
        self.max_input_bytes = max_input_bytes
        self.max_output_tokens = max_output_tokens
        self.reserved_cost_usd = 0.0
        self.model_name = delegate.model_name

    @property
    def requests_sent(self):
        return self.delegate.requests_sent

    async def complete(self, request):
        if request.max_output_tokens > self.max_output_tokens:
            raise AgentFailure("PROVIDER_TOKEN_BUDGET")
        body = json.dumps([message.model_dump(mode="json") for message in request.messages],
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # UTF-8 bytes conservatively bound text tokens; add per-message and request framing.
        input_token_bound = len(body) + 256 * len(request.messages) + 128
        if input_token_bound > self.max_input_bytes:
            raise AgentFailure("PROVIDER_INPUT_BUDGET")
        reserve = (input_token_bound * DEEPESEEK_FLASH_INPUT_PEAK_USD_PER_MILLION
                   + self.max_output_tokens * DEEPESEEK_FLASH_OUTPUT_PEAK_USD_PER_MILLION) / 1_000_000
        if self.reserved_cost_usd + reserve > self.max_cost_usd:
            raise AgentFailure("PROVIDER_COST_BUDGET")
        self.reserved_cost_usd += reserve
        return await self.delegate.complete(request)

    async def aclose(self):
        await self.delegate.aclose()


def all_turns(records):
    for record in records:
        yield record["trace"]
        for turn in record["preparation_turns"]:
            yield turn["trace"]


def quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * q)]


def call_metrics(records):
    calls = [call for trace in all_turns(records) for call in trace.get("calls", [])]
    return {
        "calls": len(calls),
        "reported_input_tokens": sum(call["input_tokens"] or 0 for call in calls),
        "reported_output_tokens": sum(call["output_tokens"] or 0 for call in calls),
        "calls_with_unknown_usage": sum(call["input_tokens"] is None or call["output_tokens"] is None for call in calls),
        "latency_ms_mean": statistics.mean([call["elapsed_ms"] for call in calls]) if calls else None,
        "latency_ms_p50": quantile([call["elapsed_ms"] for call in calls], .50),
        "latency_ms_p95": quantile([call["elapsed_ms"] for call in calls], .95),
        "case_latency_ms_mean": statistics.mean([r["elapsed_ms"] for r in records]) if records else None,
        "case_latency_ms_p50": quantile([r["elapsed_ms"] for r in records], .50),
        "case_latency_ms_p95": quantile([r["elapsed_ms"] for r in records], .95),
        "citation_error_events": sum(str((event.get("result") or {}).get("failure_code") or "").startswith("CITATION_")
            for trace in all_turns(records) for event in trace.get("events", [])),
        "by_agent": {
            agent: {"calls": sum(call["agent"] == agent for call in calls),
                    "failures": sum(call["agent"] == agent and call["status"] != "SUCCESS" for call in calls),
                    "output_valid_count": sum(call["agent"] == agent and call.get("output_valid") is True for call in calls),
                    "output_valid_rate": sum(call["agent"] == agent and call.get("output_valid") is True for call in calls)
                    / sum(call["agent"] == agent for call in calls),
                    "latency_ms_mean": statistics.mean([call["elapsed_ms"] for call in calls if call["agent"] == agent])
                    if any(call["agent"] == agent for call in calls) else None}
            for agent in sorted({call["agent"] for call in calls})
        },
    }


def matching_call(item, trace_calls, index):
    if item.get("call_id"):
        return next((call for call in trace_calls if call.get("call_id")==item["call_id"]), {})
    return trace_calls[index] if index < len(trace_calls) else {}  # Historical records have no correlation ID.


def actual_api_metrics(records):
    calls = []
    for record in records:
        previous = 0
        turns = list(record.get("preparation_turns", []))
        for turn in turns:
            outputs = turn.get("raw_model_outputs", [])
            delta = outputs[previous:]
            previous = len(outputs)
            trace_calls = turn.get("trace", {}).get("calls", [])
            for index, item in enumerate(delta):
                if item.get("controlled_injection", False):
                    continue
                call = matching_call(item,trace_calls,index)
                reply = item.get("reply") or {}
                calls.append({"agent": item.get("agent", call.get("agent")),
                              "input_tokens": reply.get("input_tokens", call.get("input_tokens")),
                              "output_tokens": reply.get("output_tokens", call.get("output_tokens")),
                              "elapsed_ms": call.get("elapsed_ms"),
                              "error_code": item.get("error_code") or call.get("error_code")})
        # The evaluator resets ScenarioModel records at the final turn, so that list starts at zero.
        turn = {"trace": record.get("trace", {}), "raw_model_outputs": record.get("raw_model_outputs", [])}
        for index, item in enumerate(turn["raw_model_outputs"]):
            if item.get("controlled_injection", False):
                continue
            trace_calls = turn["trace"].get("calls", [])
            call = matching_call(item,trace_calls,index)
            reply = item.get("reply") or {}
            calls.append({"agent": item.get("agent", call.get("agent")),
                          "input_tokens": reply.get("input_tokens", call.get("input_tokens")),
                          "output_tokens": reply.get("output_tokens", call.get("output_tokens")),
                          "elapsed_ms": call.get("elapsed_ms"),
                          "error_code": item.get("error_code") or call.get("error_code")})
    unknown = sum(call["input_tokens"] is None or call["output_tokens"] is None for call in calls)
    return {"actual_api_requests": len(calls),
            "actual_input_tokens_reported": sum(call["input_tokens"] or 0 for call in calls),
            "actual_output_tokens_reported": sum(call["output_tokens"] or 0 for call in calls),
            "actual_calls_with_unknown_usage": unknown,
            "actual_api_latency_ms_mean": statistics.mean([call["elapsed_ms"] for call in calls if call["elapsed_ms"] is not None])
            if any(call["elapsed_ms"] is not None for call in calls) else None,
            "actual_api_calls_by_agent": {
                agent: {"calls": sum(call["agent"] == agent for call in calls),
                        "failures": sum(call["agent"] == agent and call["error_code"] is not None for call in calls),
                        "input_tokens_reported": sum(call["input_tokens"] or 0 for call in calls if call["agent"] == agent),
                        "output_tokens_reported": sum(call["output_tokens"] or 0 for call in calls if call["agent"] == agent)}
                for agent in sorted({call["agent"] for call in calls})}}


def review_metrics(records):
    events = [event for trace in all_turns(records) for event in trace.get("events", [])]
    verdicts = []
    for event in events:
        result = event.get("result") or {}
        artifact = result.get("artifact") or {}
        payload = artifact.get("payload") or {}
        if payload.get("kind") == "review":
            verdicts.append(payload.get("verdict"))
    states = [record.get("reply", {}).get("state") for record in records]
    retry_turns = sum(event.get("code") == "ONE_REGENERATION_ALLOWED" for event in events)
    return {"review_verdicts": {name: verdicts.count(name) for name in ("PASS", "RETRY", "REJECT")},
            "review_decisions": len(verdicts),
            "review_pass_rate": verdicts.count("PASS") / len(verdicts) if verdicts else None,
            "regeneration_count": retry_turns,
            "fallback_cases": sum(state == "SAFE_FALLBACK" for state in states),
            "fallback_rate": sum(state == "SAFE_FALLBACK" for state in states) / len(states) if states else None}


def actual_peak_cost(usage):
    input_tokens, output_tokens = usage["reported_input_tokens"], usage["reported_output_tokens"]
    if usage["calls_with_unknown_usage"]:
        return None
    return (input_tokens * DEEPESEEK_FLASH_INPUT_PEAK_USD_PER_MILLION
            + output_tokens * DEEPESEEK_FLASH_OUTPUT_PEAK_USD_PER_MILLION) / 1_000_000


def actual_request_count(records):
    total = 0
    for record in records:
        previous = 0
        for turn in record.get("preparation_turns", []):
            outputs = turn.get("raw_model_outputs", [])
            total += sum(not item.get("controlled_injection", False) for item in outputs[previous:])
            previous = len(outputs)
        total += sum(not item.get("controlled_injection", False)
                     for item in record.get("raw_model_outputs", []))
    return total


def verify_task_trace(trace):
    """Offline dependency/order/approval checks, independent of scheduler execution functions."""
    final=next((event.get("plan") for event in reversed(trace.get("events",[])) if event.get("code")=="PLAN_TERMINAL"),None)
    if final is None:return {"valid":False,"issues":["NO_TERMINAL_PLAN"]}
    issues=[]
    try:ExecutionPlan.model_validate(final)
    except ValueError:return {"valid":False,"issues":["INVALID_PLAN_GRAPH"]}
    states={}; endings={}
    for event in trace["events"]:
        if event.get("plan"):
            for task in event["plan"]["tasks"]:states.setdefault(task["task_id"],task["status"])
        item=event.get("task_event")
        if not item:continue
        task=item["task"];name=task["task_id"]
        if event["code"]=="TASK_STARTED":
            declared=[edge for edge in final["dependencies"] if edge["task_id"]==name]
            if item["dependencies"]!=declared:issues.append("DEPENDENCY_TRACE_MISMATCH:"+name)
            for edge in declared:
                if states.get(edge["depends_on"]) not in edge["allowed_statuses"]:issues.append("DEPENDENCY_NOT_READY:"+name)
                if endings.get(edge["depends_on"],0) > task["started_ms"]:issues.append("DEPENDENCY_TIME_ORDER:"+name)
            if not task["enabled"]:issues.append("DISABLED_TASK_STARTED:"+name)
        states[name]=task["status"]
        if task.get("finished_ms") is not None:endings[name]=task["finished_ms"]
    if any(t["status"] in {"PENDING","RUNNING"} for t in final["tasks"]):issues.append("NONTERMINAL_TASK")
    if any(t["status"] in {"SKIPPED","BLOCKED"} and not t["reason"] for t in final["tasks"]):issues.append("SKIP_REASON_MISSING")
    if trace.get("state") in {"ACCEPTED","SAFE_FALLBACK"}:
        payloads=[((e.get("result") or {}).get("artifact") or {}).get("payload") or {} for e in trace["events"]]
        candidate=next((p for p in reversed(payloads) if p.get("kind")=="candidate"),None)
        approval=next((p for p in reversed(payloads) if p.get("kind")=="review"),None)
        try:permit_delivery(CandidateOutput.model_validate(candidate),ReviewOutput.model_validate(approval))
        except (ValueError,TypeError):issues.append("UNBOUND_DELIVERY")
        if not any(t["kind"]=="delivery" and t["status"]=="SUCCEEDED" for t in final["tasks"]):issues.append("DELIVERY_TASK_MISSING")
    return {"valid":not issues,"issues":issues}


def orchestration_metrics(records):
    summaries=[]
    agent_executions=0;overlaps=0;eligible=0;decision_count=0;conflicts=0;consistent=0
    for trace in all_turns(records):
        summaries.append(verify_task_trace(trace))
        final=next((e.get("plan") for e in reversed(trace.get("events",[])) if e.get("code")=="PLAN_TERMINAL"),None)
        if not final:continue
        graph={t["task_id"]:t for t in final["tasks"]}
        agent_executions+=sum(e.get("code")=="TASK_STARTED" and (e.get("task_event") or {}).get("task",{}).get("kind")=="agent" for e in trace["events"])
        i,r=graph["intent"],graph["risk"]
        if final["independent_analysis"] and all(t.get("started_ms") is not None and t.get("finished_ms") is not None for t in (i,r)):
            eligible+=1
            overlaps+=max(i["started_ms"],r["started_ms"])<min(i["finished_ms"],r["finished_ms"])
        decisions=[e["arbitration"] for e in trace["events"] if e.get("arbitration")]
        decision_count+=len(decisions);conflicts+=sum(bool(d["conflicts"]) for d in decisions)
        if decisions:
            ctx=next((((e.get("result") or {}).get("artifact") or {}).get("payload") for e in reversed(trace["events"])
                if (((e.get("result") or {}).get("artifact") or {}).get("payload") or {}).get("kind")=="context"),{})
            consistent+=decisions[-1]["final_route"]==ctx.get("route")
    n=len(summaries)
    return {"agent_executions":agent_executions,"planned_turns":n,"plan_structure_valid_count":sum(s["valid"] for s in summaries),
        "plan_structure_valid_rate":sum(s["valid"] for s in summaries)/n if n else None,
        "plan_integrity_issues":[s["issues"] for s in summaries if not s["valid"]],
        "independent_turns":eligible,"parallel_analysis_turns":overlaps,
        "arbitration_decisions":decision_count,"arbitration_conflict_decisions":conflicts,
        "arbitration_route_consistency_rate":consistent/n if n else None}


def finalize_run(output):
    """Rebuild summaries from saved case files without making model calls."""
    output = Path(output)
    freeze_path = output / "FREEZE.json"
    if not freeze_path.is_file():
        raise ValueError("Run freeze record is missing")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    selected = freeze.get("selected_case_ids", [])
    records = [json.loads((output / (case_id + ".json")).read_text(encoding="utf-8")) for case_id in selected]
    if len(records) != len(selected) or {record["case_id"] for record in records} != set(selected):
        raise ValueError("Per-case records do not match frozen selection")
    usage = call_metrics(records)
    real = freeze.get("provider") == "openai-compatible"
    summary = {
        "evaluation_type": "real_provider_development" if real else "offline_mock_behavior",
        "provider": freeze.get("provider"), "model": freeze.get("model"),
        "external_model_requests": actual_request_count(records),
        "all_cases": summarize(records),
        "natural_cases": summarize([record for record in records if not record["score"]["controlled_fault"]]),
        "controlled_fault_cases": summarize([record for record in records if record["score"]["controlled_fault"]]),
        "by_group": {group: summarize([record for record in records if record["group"] == group]) for group in sorted(GROUPS)},
        **usage, **actual_api_metrics(records), **review_metrics(records),
        "review_by_case_type": {
            "natural": review_metrics([record for record in records if not record["score"]["controlled_fault"]]),
            "controlled_fault": review_metrics([record for record in records if record["score"]["controlled_fault"]]),
            "note": "Controlled review scenarios use deterministic fault injection; natural cases measure the model OutputReviewer.",
        },
        "estimated_peak_cost_usd": actual_peak_cost({"reported_input_tokens": actual_api_metrics(records)["actual_input_tokens_reported"],
                                                       "reported_output_tokens": actual_api_metrics(records)["actual_output_tokens_reported"],
                                                       "calls_with_unknown_usage": actual_api_metrics(records)["actual_calls_with_unknown_usage"]}) if real else None,
        "cost_budget_upper_bound_usd": freeze.get("estimated_peak_cost_budget_usd"),
        "cost_rates_usd_per_million_tokens": {"input_peak_noncached": DEEPESEEK_FLASH_INPUT_PEAK_USD_PER_MILLION,
                                              "output_peak": DEEPESEEK_FLASH_OUTPUT_PEAK_USD_PER_MILLION} if real else None,
        "token_totals_scope": "all final and preparation turns; unknown usage not imputed",
        "completed_case_count": len(records),
        "case_elapsed_seconds": sum(record["elapsed_ms"] for record in records) / 1000,
        "integrity_before": {"valid": True, "manifest_sha256": freeze.get("manifest_sha256")},
    }
    write_json(output / "evaluation.json", summary)
    failures = [record for record in records if not record["score"]["case_pass"]]
    lines = ["# Stage 4 固定评测结果", "", "所有逐条原始输出和 Trace 保存在同目录 JSON。", "",
             f"选中 {summary['all_cases']['cases']} 条：通过 {summary['all_cases']['passed']}；自然用例 {summary['natural_cases']['passed']}/{summary['natural_cases']['cases']}；受控故障 {summary['controlled_fault_cases']['passed']}/{summary['controlled_fault_cases']['cases']}。",
             f"外部请求 {summary['external_model_requests']}；按峰值未缓存费率估算成本 {summary['estimated_peak_cost_usd'] if summary['estimated_peak_cost_usd'] is not None else 'unknown'} USD。", "", "## 失败案例", ""]
    lines += [record["case_id"] + "：" + ", ".join(record["score"]["mismatch_fields"]) for record in failures] or ["本次固定用例无失败。"]
    (output / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (output / "SHA256SUMS.txt").write_text("".join(sha(path) + "  " + path.name + "\n"
        for path in sorted(output.iterdir()) if path.is_file() and path.name != "SHA256SUMS.txt"), encoding="utf-8", newline="\n")
    return summary


def login(client, username):
    result = client.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD})
    result.raise_for_status()
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def run_evaluation(packet, output, provider_name, allow_real=False, *, case_ids=None,
                   max_cost_usd=3.0, max_input_bytes=32768, orchestration="sequential", mock_delay_ms=0):
    verified = verify_packet(ROOT, packet)
    packet_manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    cases = load_cases(packet / "cases.jsonl", packet_manifest.get("dataset_kind", "fixed30"))
    if output.exists():
        raise ValueError("Run directory already exists; previous results are preserved")
    real = provider_name == "openai-compatible"
    if real and not allow_real:
        raise ValueError("Real evaluation requires --allow-real-models")
    config = Settings()
    if real:
        names = ["CAMPUSBRIDGE_ENDPOINT", "CAMPUSBRIDGE_MODEL", "CAMPUSBRIDGE_API_KEY"]
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            return {"status": "PENDING_REAL_CONFIGURATION", "missing_environment_variables": missing, "external_model_requests": 0}
        try:
            config = Settings.from_environment()
        except ValidationError:
            raise ValueError("Invalid provider environment; no credentials printed") from None
        if config.model_provider != "openai-compatible":
            raise ValueError("Set CAMPUSBRIDGE_MODEL_PROVIDER=openai-compatible explicitly")
    if not 0<=mock_delay_ms<=1000:
        raise ValueError("Invalid mock latency fixture")
    config=config.model_copy(update={"orchestration":orchestration})
    output.mkdir(parents=True)
    if case_ids:
        requested = list(case_ids)
        if len(set(requested)) != len(requested):
            raise ValueError("Duplicate case id in selection")
        indexed = {case["case_id"]: case for case in cases}
        unknown = sorted(set(requested) - indexed.keys())
        if unknown:
            raise ValueError("Unknown case id in selection")
        cases = [indexed[case_id] for case_id in requested]
    request_budget = sum(len(case["turns"]) * config.max_model_calls for case in cases)
    if real and max_cost_usd <= 0:
        raise ValueError("A positive estimated-cost budget is required")
    safe_endpoint = None
    if real and config.endpoint:
        parts = urlsplit(config.endpoint)
        safe_endpoint = urlunsplit((parts.scheme, parts.hostname + (":" + str(parts.port) if parts.port else ""),
                                    parts.path.rstrip("/"), "", ""))
    write_json(output / "FREEZE.json", {**verified, "packet_sha256s": {
        file.name: sha(file) for file in sorted(packet.iterdir()) if file.is_file()},
        "provider": provider_name, "model": config.model if real else MockModel.model_name,
        "endpoint": safe_endpoint,
        "provider_json_mode": config.provider_json_mode if real else None,
        "provider_max_tokens_parameter": config.provider_max_tokens_parameter if real else None,
        "provider_thinking_mode": config.provider_thinking_mode if real else None,
        "selected_case_ids": [case["case_id"] for case in cases],
        "dataset_kind": packet_manifest.get("dataset_kind", "fixed30"),
        "orchestration":config.orchestration,"max_parallel_agents":config.max_parallel_agents,
        "max_task_executions":config.max_task_executions,"max_turn_token_reservation":config.max_turn_token_reservation,
        "analysis_input_policy":"Same independent RiskInput(intent=null) in both modes unless current request depends on prior-history referents.",
        "mock_model_latency_ms":mock_delay_ms if not real else None,
        "risk_support_semantics": "Severity and human-support need are independent; fixed30 legacy gold is unchanged, including MODERATE labels on cb2-r03/r04.",
        "max_model_calls_per_turn": config.max_model_calls, "provider_request_budget": request_budget,
        "temperature": 0, "max_output_tokens": 1024,
        "max_input_bytes_per_request": max_input_bytes if real else None,
        "estimated_peak_cost_budget_usd": max_cost_usd if real else None,
        "cost_reservation_policy": "reserve per request at DeepSeek Flash peak noncached input/output rates before HTTP; no calls after budget exhausted",
        "automatic_retries": False,
        "model_timeout": config.model_timeout, "run_timeout": config.run_timeout,
        "controlled_timeout_seconds": 0.05, "timeout_scope": "ResponseAgent call 1 only; other calls use normal deadline",
        "knowledge_backend": "fts5", "frozen_before_model_calls": True})
    records, elapsed = [], time.monotonic()
    with tempfile.TemporaryDirectory(prefix="campusbridge-eval-") as temp:
        config = config.model_copy(update={"environment": "test", "database_path": Path(temp) / "eval.sqlite3",
                                          "knowledge_backend": "fts5", "seed_demo": True})
        class LatencyMock(MockModel):
            async def complete(self,request):
                if mock_delay_ms:
                    import asyncio
                    await asyncio.sleep(mock_delay_ms/1000)
                return await super().complete(request)
        delegate = BudgetedProvider(OpenAICompatibleProvider(config, max_requests=request_budget),
                                    max_cost_usd=max_cost_usd, max_input_bytes=max_input_bytes) if real else LatencyMock()
        model = ScenarioModel(delegate)
        app = create_app(config, model=model)
        with TestClient(app) as client:
            student, counselor = login(client, "student_lan"), login(client, "counselor_yu")
            for case in cases:
                case_start = time.monotonic()
                conversation = client.post("/api/conversations", headers=student).json()["conversation_id"]
                preparation, final_reply, trace, public_trace = [], {}, {"events": [], "calls": []}, {}
                model.configure(None)
                runtime_config = app.state.runtime.settings
                failure = None
                try:
                    for turn_index, message in enumerate(case["turns"]):
                        last = turn_index == len(case["turns"]) - 1
                        if last:
                            model.configure(case.get("fault"))
                        response = client.post(f"/api/conversations/{conversation}/messages",
                            headers={**student, "Idempotency-Key": case["case_id"] + "-" + str(turn_index)}, json={"message": message})
                        if response.status_code != 200:
                            raise ValueError("API_STATUS_" + str(response.status_code))
                        reply = response.json()
                        run_id = reply["run_id"]
                        current_trace = client.get(f"/api/runs/{run_id}/trace", headers=counselor).json()
                        if not last:
                            preparation.append({"reply": reply, "trace": current_trace, "raw_model_outputs": list(model.records)})
                        else:
                            final_reply, trace = reply, current_trace
                            public_trace = client.get(f"/api/runs/{run_id}/trace", headers=student).json()
                except Exception as exc:
                    # All cases remain in the denominator; no error response bodies or credentials recorded.
                    failure = "CASE_EXECUTION_FAILED"
                finally:
                    app.state.runtime.settings = runtime_config
                case_list = client.get("/api/cases", headers=counselor).json()["cases"]
                case_created = False
                for item in case_list:
                    detail = client.get("/api/cases/" + item["case_id"], headers=counselor).json()
                    if detail["run_id"] == final_reply.get("run_id"):
                        case_created = True
                def source_lookup(source_id):
                    result = client.get("/api/knowledge/sources/" + source_id, headers=counselor)
                    return result.json() if result.status_code == 200 else None
                score = score_case(case, final_reply, trace, case_created, source_lookup, public_trace)
                if failure:
                    score["case_pass"] = False
                    score["mismatch_fields"].append("execution")
                record = {"case_id": case["case_id"], "group": case["group"], "score": score,
                    "elapsed_ms": (time.monotonic() - case_start) * 1000, "failure_code": failure,
                    "raw_model_outputs": list(model.records), "preparation_turns": preparation,
                    "reply": final_reply, "trace": trace, "student_trace": public_trace}
                records.append(record)
                write_json(output / (case["case_id"] + ".json"), record)
                print(case["case_id"] + " " + ("PASS" if score["case_pass"] else "FAIL " + ",".join(score["mismatch_fields"])), flush=True)
        external = getattr(delegate, "requests_sent", 0)
    post_verify = verify_packet(ROOT, packet)
    usage = call_metrics(records)
    review = review_metrics(records)
    actual = actual_api_metrics(records) if real else {"actual_api_requests":0,"actual_input_tokens_reported":0,
        "actual_output_tokens_reported":0,"actual_calls_with_unknown_usage":0,"actual_api_latency_ms_mean":None,"actual_api_calls_by_agent":{}}
    actual_cost = actual_peak_cost({"reported_input_tokens": actual["actual_input_tokens_reported"],
        "reported_output_tokens": actual["actual_output_tokens_reported"],
        "calls_with_unknown_usage": actual["actual_calls_with_unknown_usage"]}) if real else None
    summary = {"evaluation_type": "real_provider_development" if real else "offline_mock_behavior",
        "provider": provider_name, "model": config.model if real else MockModel.model_name,"orchestration":config.orchestration,
        "external_model_requests": external, "elapsed_seconds": time.monotonic() - elapsed,
        "all_cases": summarize(records), "natural_cases": summarize([r for r in records if not r["score"]["controlled_fault"]]),
        "controlled_fault_cases": summarize([r for r in records if r["score"]["controlled_fault"]]),
        "by_group": {group: summarize([r for r in records if r["group"] == group]) for group in sorted(GROUPS)},
        **usage, **actual, **review, **orchestration_metrics(records),
        "review_by_case_type": {
            "natural": review_metrics([r for r in records if not r["score"]["controlled_fault"]]),
            "controlled_fault": review_metrics([r for r in records if r["score"]["controlled_fault"]]),
            "note": "Controlled review scenarios use deterministic fault injection; natural cases measure the model OutputReviewer.",
        },
        "estimated_peak_cost_usd": actual_cost,
        "cost_budget_upper_bound_usd": max_cost_usd if real else None,
        "reserved_peak_cost_usd": delegate.reserved_cost_usd if real else None,
        "cost_rates_usd_per_million_tokens": {"input_peak_noncached": DEEPESEEK_FLASH_INPUT_PEAK_USD_PER_MILLION,
                                              "output_peak": DEEPESEEK_FLASH_OUTPUT_PEAK_USD_PER_MILLION} if real else None,
        "token_totals_scope": "all final and preparation turns; unknown usage not imputed",
        "integrity_before": verified, "integrity_after": post_verify}
    write_json(output / "evaluation.json", summary)
    failures = [r for r in records if not r["score"]["case_pass"]]
    lines = ["# Stage 6 固定评测结果", "", "口径：" + summary["evaluation_type"]+" / "+config.orchestration, "",
        f"本次选中 {summary['all_cases']['cases']} 条：通过 {summary['all_cases']['passed']}；自然用例 {summary['natural_cases']['passed']}/{summary['natural_cases']['cases']}；受控故障 {summary['controlled_fault_cases']['passed']}/{summary['controlled_fault_cases']['cases']}。",
        f"外部请求 {external}；总耗时 {summary['elapsed_seconds']:.3f} 秒。", "", "## 失败案例", ""]
    lines += [r["case_id"] + "：" + ", ".join(r["score"]["mismatch_fields"]) for r in failures] or ["本次固定用例无失败。具体故障控制与恢复结果保存在逐条 JSON。"]
    (output / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (output / "SHA256SUMS.txt").write_text("".join(sha(file) + "  " + file.name + "\n" for file in sorted(output.iterdir())
        if file.is_file()), encoding="utf-8", newline="\n")
    return summary


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--cases-file", type=Path)
    prep.add_argument("--dataset-kind", choices=["fixed30", "stage5-boundary","stage6-smoke"], default="fixed30")
    verify = commands.add_parser("verify")
    verify.add_argument("--packet", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--packet", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--provider", choices=["mock", "openai-compatible"], default="mock")
    run.add_argument("--allow-real-models", action="store_true")
    run.add_argument("--case-ids", nargs="+")
    run.add_argument("--max-estimated-cost-usd", type=float, default=3.0)
    run.add_argument("--max-input-bytes", type=int, default=32768)
    run.add_argument("--orchestration",choices=["sequential","dynamic"],default="sequential")
    run.add_argument("--mock-delay-ms",type=float,default=0)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare_packet(ROOT, args.output, args.cases_file, args.dataset_kind)
            result = verify_packet(ROOT, args.output)
        elif args.command == "verify":
            result = verify_packet(ROOT, args.packet)
        elif args.command == "finalize":
            result = finalize_run(args.run_dir)
        else:
            result = run_evaluation(args.packet, args.output, args.provider, args.allow_real_models,
                                   case_ids=args.case_ids, max_cost_usd=args.max_estimated_cost_usd,
                                   max_input_bytes=args.max_input_bytes,orchestration=args.orchestration,mock_delay_ms=args.mock_delay_ms)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as exc:
        # Evaluation errors contain our constant codes and relative paths only, never Settings validation inputs.
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
