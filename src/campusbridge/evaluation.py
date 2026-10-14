"""Fixed development evaluation and explicit, separately-labelled fault injection."""
import asyncio
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from campusbridge.contracts import AgentFailure, AgentName, ModelReply, digest
from campusbridge.models import MockModel


GROUPS = {"general", "multiturn", "knowledge", "risk", "review-control", "provider-control"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def load_cases(path, dataset_kind="fixed30"):
    cases = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    count = {"fixed30":30,"stage5-boundary":12,"stage6-smoke":4}.get(dataset_kind)
    if count is None:
        raise ValueError("Unknown dataset kind")
    if len(cases) != count or len({c["case_id"] for c in cases}) != count:
        if dataset_kind == "stage5-boundary":
            raise ValueError("Expected exactly 12 unique boundary cases")
        raise ValueError("Unexpected case count or duplicate identities")
    if dataset_kind == "fixed30" and Counter(c["group"] for c in cases) != Counter({group: 5 for group in GROUPS}):
        raise ValueError("Expected six groups of five cases")
    for case in cases:
        if not re.fullmatch(r"[a-z0-9-]+", case["case_id"]):
            raise ValueError("Unsafe case id")
        if not case["turns"] or any(not isinstance(value, str) or not value for value in case["turns"]):
            raise ValueError("Invalid turns")
        expected = case["expected"]
        if expected["route"] not in {"CONVERSATION", "KNOWLEDGE_SUPPORT", "HUMAN_SUPPORT"} or expected["risk"] not in {"LOW", "MODERATE", "HIGH", "UNKNOWN"}:
            raise ValueError("Invalid reference")
        if case["group"] not in GROUPS or type(expected["case_created"]) is not bool:
            raise ValueError("Invalid reference")
        if expected["state"] not in {"ACCEPTED", "SAFE_FALLBACK", "FAILED"}:
            raise ValueError("Invalid state reference")
        if "needs_human_support" in expected and type(expected["needs_human_support"]) is not bool:
            raise ValueError("Invalid support reference")
    return cases


def prepare_packet(root, output, cases_path=None, dataset_kind="fixed30"):
    from campusbridge.contracts import IntentOutput, ReviewOutput, RiskOutput, ExecutionPlan, ArbitrationDecision, ArbitrationInput
    root, output = Path(root).resolve(), Path(output)
    if output.exists():
        raise ValueError("Packet directory already exists")
    cases_path = Path(cases_path).resolve() if cases_path else root / "eval/stage2/20261012-fixed-v1/cases.jsonl"
    if not cases_path.is_relative_to(root):
        raise ValueError("Case dataset must be within project root")
    cases = load_cases(cases_path, dataset_kind)
    output.mkdir(parents=True)
    copies = {"cases.jsonl": cases_path, "knowledge-v2.json": root / "src/campusbridge/resources/knowledge-v2.json",
              "prompts_v2.py": root / "src/campusbridge/prompts_v2.py",
              "prompts_v3.py": root / "src/campusbridge/prompts_v3.py"}
    for name, source in copies.items():
        (output / name).write_bytes(source.read_bytes())
    write_json(output / "schemas.json", {name: cls.model_json_schema() for name, cls in
        (("intent", IntentOutput), ("risk", RiskOutput), ("review", ReviewOutput),
         ("task_plan",ExecutionPlan),("arbitration_input",ArbitrationInput),("arbitration_decision",ArbitrationDecision))})
    source_hashes = {path.relative_to(root).as_posix(): sha(path) for path in sorted((root / "src/campusbridge").rglob("*.py"))}
    source_hashes["scripts/evaluate.py"] = sha(root / "scripts/evaluate.py")
    payload_hashes = {name: sha(output / name) for name in (*copies, "schemas.json")}
    write_json(output / "manifest.json", {"protocol": "campusbridge-stage6-dynamic-v1", "case_count": len(cases),
        "dataset_kind": dataset_kind, "active_prompt": "prompts_v3.py",
        "source_generation": "original_synthetic", "license": "MIT", "control_fault_cases": sum(bool(c.get('fault')) for c in cases),
        "generation_reason": "Stage 6 sequential/dynamic comparison; original fixed30/boundary12 labels and role Prompts retained, shared conditional RiskInput independence protocol.",
        "payload_sha256": payload_hashes, "source_sha256": source_hashes,
        "source_bindings": {name: path.relative_to(root).as_posix() for name, path in copies.items()},
        "frozen_before_model_calls": True})
    files = sorted(path for path in output.iterdir() if path.is_file())
    (output / "SHA256SUMS.txt").write_text("".join(sha(path) + "  " + path.name + "\n" for path in files), encoding="utf-8", newline="\n")
    verify_packet(root, output)


def verify_packet(root, packet):
    root, packet = Path(root), Path(packet)
    for line in (packet / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        checksum, name = line.split("  ", 1)
        if Path(name).name != name or sha(packet / name) != checksum:
            raise ValueError("Packet checksum mismatch")
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    for relative, checksum in manifest["source_sha256"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != checksum:
            raise ValueError("Frozen source checksum mismatch: " + relative)
    for name, checksum in manifest["payload_sha256"].items():
        if sha(packet / name) != checksum:
            raise ValueError("Frozen payload mismatch")
    for name, source in manifest["source_bindings"].items():
        if sha(root / source) != manifest["payload_sha256"][name]:
            raise ValueError("Live inputs differ from fixed packet")
    cases = load_cases(packet / "cases.jsonl", manifest.get("dataset_kind", "fixed30"))
    return {"valid": True, "case_count": len(cases), "manifest_sha256": sha(packet / "manifest.json"),
            "source_files_verified": len(manifest["source_sha256"])}


class ScenarioModel:
    """Records port outputs; faults are predetermined by cases, never selected from scores."""
    def __init__(self, delegate):
        self.delegate = delegate
        self.provider_name = getattr(delegate, "provider_name", "custom")
        self.model_name = getattr(delegate, "model_name", "custom")
        self.configure(None)

    def configure(self, fault, *, target_agent=AgentName.RESPONSE, target_call=1):
        if target_call < 1:
            raise ValueError("Target call must be positive")
        self.fault, self.responses, self.reviews, self.records = fault, 0, 0, []
        self.target_agent, self.target_call = AgentName(target_agent), target_call
        self.invocations = Counter()

    def timeout_for(self, request):
        if (self.fault == "response_timeout" and request.task == self.target_agent
                and self.invocations[request.task] + 1 == self.target_call):
            return 0.05
        return None

    async def complete(self, request):
        record = {"agent": request.task.value, "controlled_injection": False, "call_id": request.trace_call_id}
        self.invocations[request.task] += 1
        try:
            fault = self.fault
            if request.task == AgentName.RESPONSE:
                self.responses += 1
            if request.task == AgentName.REVIEW:
                self.reviews += 1
            if (fault == "response_timeout" and request.task == self.target_agent
                    and self.invocations[request.task] == self.target_call):
                record["controlled_injection"] = True
                record["target_call"] = self.target_call
                await asyncio.sleep(3600)
            if ((request.task == AgentName.RESPONSE and fault in {"response_http_error", "response_truncated"})
                    or (request.task == AgentName.REVIEW and fault == "review_exception")):
                record["controlled_injection"] = True
                raise AgentFailure("PROVIDER_TRUNCATED" if fault == "response_truncated" else "PROVIDER_HTTP_ERROR")
            if ((request.task == AgentName.INTENT and fault == "intent_invalid") or
                    (request.task == AgentName.RISK and fault == "risk_invalid")):
                record["controlled_injection"] = True
                reply = ModelReply(content={"not_the_role_schema": True})
            elif request.task == AgentName.RESPONSE and fault == "citation_missing":
                record["controlled_injection"] = True
                reply = ModelReply(content="请向工作人员确认这项安排。")
            elif request.task == AgentName.RESPONSE and fault in {"review_retry_once", "review_retry_twice"} and (
                    self.responses == 1 or fault == "review_retry_twice"):
                record["controlled_injection"] = True
                reply = ModelReply(content="CONTROLLED-REJECTED-CANDIDATE：这是指定的审核控制样本。")
            elif request.task == AgentName.REVIEW and fault in {"review_retry_once", "review_retry_twice", "review_binding_mismatch"}:
                record["controlled_injection"] = True
                candidate = json.loads(request.messages[-1].content)["candidate"]
                retry = fault == "review_retry_twice" or (fault == "review_retry_once" and self.reviews == 1)
                reply = ModelReply(content={"candidate_id": "wrong-binding" if fault == "review_binding_mismatch" else candidate["candidate_id"],
                    "content_hash": candidate["content_hash"], "citations_hash": candidate.get("citations_hash"),
                    "verdict": "RETRY" if retry else "PASS", "reason_codes": ["MOCK_REJECTION"] if retry else []})
            else:
                reply = await self.delegate.complete(request)
            record["reply"] = reply.model_dump(mode="json")
            return reply
        except asyncio.CancelledError:
            record["error_code"] = "MODEL_TIMEOUT_OR_CANCELLED"
            raise
        except Exception as exc:
            record["error_code"] = exc.code if isinstance(exc, AgentFailure) else "MODEL_CALL_FAILED"
            raise
        finally:
            self.records.append(record)

    async def aclose(self):
        if hasattr(self.delegate, "aclose"):
            await self.delegate.aclose()


def score_case(case, reply, trace, case_created, source_lookup, student_trace):
    outputs = [e["result"]["artifact"]["payload"] for e in trace["events"]
               if e.get("result") and e["result"].get("artifact")]
    context = next((item for item in reversed(outputs) if item["kind"] == "context"), {})
    risk = next((item for item in reversed(outputs) if item["kind"] == "risk"), {})
    expected = case["expected"]
    actual = {"route": context.get("route"), "risk": risk.get("level"), "state": reply.get("state"),
              "case_created": case_created}
    checks = {name: actual[name] == expected[name] for name in ("route", "risk", "state", "case_created")}
    if "needs_human_support" in expected:
        actual["needs_human_support"] = risk.get("needs_human_support", risk.get("needs_human"))
        checks["needs_human_support"] = actual["needs_human_support"] == expected["needs_human_support"]
    if "history_min" in expected:
        checks["history"] = len(context.get("history", [])) >= expected["history_min"]
    if "document_id" in expected:
        checks["retrieved_document"] = expected["document_id"] in {item["document_id"] for item in context.get("knowledge", [])}
        checks["cited_document"] = expected["document_id"] in {item["document_id"] for item in reply.get("citations", [])}
    if "retrieval_status" in expected:
        checks["retrieval_status"] = context.get("retrieval_status") == expected["retrieval_status"]
    if "reply_contains" in expected:
        # Multiple equivalent no-hit phrasings are defined before execution, not repaired after results.
        checks["no_hit_acknowledged"] = any(term in reply.get("reply", "") for term in ("没有检索到", "未找到", "没有找到", "未检索到", "暂无"))
    if expected.get("regeneration"):
        checks["regeneration"] = any(e["code"] == "ONE_REGENERATION_ALLOWED" for e in trace["events"])
    if "absent_text" in expected:
        checks["rejected_text_hidden"] = expected["absent_text"] not in json.dumps({"reply": reply, "student_trace": student_trace}, ensure_ascii=False)
    if "failure_code" in expected:
        observed = {e["code"] for e in trace["events"]} | {e["result"].get("failure_code") for e in trace["events"] if e.get("result")}
        checks["failure_recorded"] = expected["failure_code"] in observed
    grounded = 0
    for citation in reply.get("citations", []):
        source = source_lookup(citation["source_id"])
        if source and source["text"] == citation["quote"] and digest(citation["quote"]) == citation["source_hash"] == source["source_hash"]:
            grounded += 1
    checks["citation_grounding"] = grounded == len(reply.get("citations", []))
    return {"case_id": case["case_id"], "group": case["group"], "controlled_fault": case.get("fault"),
            "actual": actual, "checks": checks, "case_pass": all(checks.values()),
            "mismatch_fields": [name for name, passed in checks.items() if not passed],
            "citations": len(reply.get("citations", [])), "grounded_citations": grounded}


def summarize(records):
    count = len(records)
    metrics = {}
    for field in ("route", "risk", "state", "case_created"):
        passed = sum(record["score"]["checks"].get(field, False) for record in records)
        metrics[field + "_accuracy"] = passed / count if count else 0
    citations = sum(record["score"]["citations"] for record in records)
    grounded = sum(record["score"]["grounded_citations"] for record in records)
    return {"cases": count, "passed": sum(record["score"]["case_pass"] for record in records),
        "failed": [record["case_id"] for record in records if not record["score"]["case_pass"]], **metrics,
        "citations": citations, "grounded_citations": grounded,
        "citation_grounding_rate": grounded / citations if citations else None,
        "failure_fields": dict(Counter(field for record in records for field in record["score"]["mismatch_fields"]))}
