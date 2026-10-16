import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from campusbridge.api import create_app
from campusbridge.agents import OutputReviewer, ResponseAgent
from campusbridge.contracts import (AgentFailure, AgentName, ContextOutput, KnowledgeItem, ModelReply,
    ResponseInput, ReviewInput, ReviewOutput, RiskOutput, digest, grounding_digest)
from campusbridge.db import RunEvent
from campusbridge.evaluation import ScenarioModel, load_cases
from campusbridge.grounding import validate_citations
from campusbridge.models import MockModel
from campusbridge.policy import SAFE_REPLY, explicit_human_request
from campusbridge.runtime import CampusRuntime
from campusbridge.settings import Settings
from tests.test_api import begin, decoded, login, post
from tests.test_runtime import execute

ROOT = Path(__file__).resolve().parents[1]


def citation_semantics_settings(settings):
    """Citation-policy checks have no latency assertion.

    Slow Windows hosted-runner SQLite writes must not consume the demonstration
    deadline before the expected citation retry. Production defaults and the
    explicit timeout-injection tests below keep their original budgets.
    """
    return settings.model_copy(update={"model_timeout": 10, "context_timeout": 10, "run_timeout": 120})


@pytest.mark.parametrize("message", ["我想找辅导员讨论最近适应校园生活的困难。", "我需要有人帮我整理接下来可以获得的支持。",
    "请帮我联系值班老师聊一聊。", "我希望有位工作人员陪我把下周的支持安排梳理清楚。"])
def test_explicit_support_request_is_independent_of_severity(foundation, message):
    class Underestimating(MockModel):
        async def complete(self, request):
            if request.task == AgentName.INTENT:
                return ModelReply(content={"intent": "SUPPORT", "needs_knowledge": False, "needs_human_support": False})
            if request.task == AgentName.RISK:
                return ModelReply(content={"level": "LOW", "needs_human_support": False, "reason_codes": ["NO_SIGNAL"]})
            return await super().complete(request)
    runtime, run, reply = execute(foundation, Underestimating(), message)
    case = runtime.store.cases(foundation[-1])[0]
    assessment = runtime.store.case_detail(case["case_id"], foundation[-1])["risk"]
    assert reply["state"] == "ACCEPTED" and assessment["level"] == "LOW"
    assert assessment["needs_human_support"] and "HUMAN_REQUEST" in assessment["reason_codes"]
    assert len(runtime.store.cases(foundation[-1])) == 1


@pytest.mark.parametrize("message", ["今天压力有点大，想聊聊。", "我不想找辅导员，先自己整理。",
    "我想知道辅导员的联系方式。", "如何预约工作人员谈话？", "我不需要有人帮我。",
    "他说“我想找辅导员”，我是在引用他。", "剧本里有一句\"我需要有人帮我\"。"])
def test_no_blanket_human_request_rule(message):
    assert not explicit_human_request(message)


@pytest.mark.parametrize("message,human,level", [("今天有点紧张，想倾诉。", False, "LOW"),
    ("我想找辅导员谈一谈。", True, "LOW"), ("我准备伤害自己。", True, "HIGH")])
def test_emotion_support_and_crisis_boundary(foundation, message, human, level):
    runtime, run, _ = execute(foundation, message=message)
    trace = runtime.store.trace(run, foundation[-1])
    risk = next(e["result"]["artifact"]["payload"] for e in trace["events"]
        if e["result"] and e["result"]["agent"] == "RiskAgent")
    assert risk["level"] == level and risk["needs_human_support"] is human
    assert bool(runtime.store.cases(foundation[-1])) is human


def test_archived_risk_protocol_is_readable():
    old = RiskOutput(level="LOW", needs_human=True, reason_codes=("NO_SIGNAL",))
    assert old.needs_human_support and old.needs_human
    assert "needs_human_support" in old.model_dump() and "needs_human" not in old.model_dump()


def response_data(status="HIT", refs=None):
    item = KnowledgeItem(source_id="unit-active-source", document_id="unit-doc", title="原创卡片", text="登记设施和位置。", revision="unit-v1")
    return ResponseInput(message="宿舍报修", context=ContextOutput(route="KNOWLEDGE_SUPPORT", history=(),
        knowledge=(item,) if refs is None else refs, retrieval_status=status),
        risk=RiskOutput(level="LOW", needs_human=False, reason_codes=("NO_SIGNAL",)), attempt=1)


def generate(text, data=None):
    async def invoke(request):
        payload = json.loads(request.messages[-1].content)
        assert payload["allowed_source_ids"] == [x.source_id for x in (data or response_data()).context.knowledge]
        return ModelReply(content=text)
    return asyncio.run(ResponseAgent().run(data or response_data(), invoke))


def test_whitespace_and_duplicate_sources_have_one_exact_citation():
    candidate = generate("登记信息[source: unit-active-source ]，记录位置[source:unit-active-source]。")
    assert len(candidate.citations) == 1
    assert "[source: " not in candidate.text and candidate.content_hash == digest(candidate.text)
    validate_citations(candidate, response_data().context.knowledge)


@pytest.mark.parametrize("text,code", [("旧会话[source:other-conversation]", "CITATION_UNKNOWN_SOURCE"),
    ("猜测[source:unit-active-sourc]", "CITATION_UNKNOWN_SOURCE"), ("无引用", "CITATION_REQUIRED"),
    ("空引用[source: ]", "CITATION_INVALID_FORMAT")])
def test_invalid_id_is_never_fuzzily_repaired(text, code):
    with pytest.raises(AgentFailure, match=code):
        generate(text)


def test_no_hit_cannot_reuse_a_previous_source():
    with pytest.raises(AgentFailure, match="CITATION_UNKNOWN_SOURCE"):
        generate("使用之前的卡片[source:unit-active-source]", response_data("NO_HIT", ()))
    candidate = generate("没有检索到相关资料，请核实。", response_data("NO_HIT", ()))
    assert candidate.citations == ()


def test_reviewer_rechecks_actual_reference_whitelist_and_metadata():
    candidate = generate("登记信息[source:unit-active-source]")
    async def forbidden(_):
        raise AssertionError("Invalid citation must be caught before model review")
    with pytest.raises(AgentFailure, match="CITATION_UNKNOWN_SOURCE"):
        asyncio.run(OutputReviewer().run(ReviewInput(candidate=candidate, risk=response_data().risk, references=()), forbidden))
    forged = candidate.citations[0].model_copy(update={"quote": "伪造内容", "source_hash": digest("伪造内容")})
    candidate = candidate.model_copy(update={"citations": (forged,), "citations_hash": grounding_digest((forged,), "HIT")})
    with pytest.raises(AgentFailure, match="CITATION_METADATA_MISMATCH"):
        validate_citations(candidate, response_data().context.knowledge)


def test_delivery_and_history_revalidate_run_scoped_sources(foundation):
    runtime, run, reply = execute(foundation, message="如何预约图书馆座位？")
    with foundation[0].sessions.begin() as session:
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run)).all()
        row = next(row for row in events if (row.event.get("result") or {}).get("agent") == "ContextAgent")
        event = json.loads(json.dumps(row.event)); event["result"]["artifact"]["payload"]["knowledge"] = []
        row.event = event
    with pytest.raises(AgentFailure, match="CITATION_UNKNOWN_SOURCE"):
        runtime.store.deliverable(run, foundation[-2])
    with pytest.raises(AgentFailure, match="CITATION_UNKNOWN_SOURCE"):
        runtime.store.conversation_view(reply["conversation_id"], foundation[-2])


def test_commit_cannot_bypass_current_run_whitelist(foundation, monkeypatch):
    monkeypatch.setattr(foundation[3], "run_references", lambda session, run: ())
    with pytest.raises(AgentFailure, match="CITATION_UNKNOWN_SOURCE"):
        execute(foundation, message="如何预约图书馆座位？")
    conversation = foundation[3].conversations(foundation[-2])[0] if hasattr(foundation[3], "conversations") else None
    from campusbridge.db import Message
    with foundation[0].sessions() as session:
        assert not session.scalars(select(Message).where(Message.role == "assistant")).all()


def test_other_conversation_source_exists_but_is_not_current_whitelist(foundation):
    first = response_data().context.knowledge[0]
    second = first.model_copy(update={"source_id": "second-conversation-source", "document_id": "second-doc"})
    class Cards:
        async def search(self, query, campus):
            return (first,) if "图书馆" in query else (second,)
    class Leaking(MockModel):
        def __init__(self): self.responses = 0
        async def complete(self, request):
            if request.task == AgentName.RESPONSE:
                self.responses += 1
                sid = first.source_id if self.responses <= 2 else second.source_id
                return ModelReply(content="核实信息[source:" + sid + "]")
            return await super().complete(request)
    store, student = foundation[3], foundation[-2]
    runtime = CampusRuntime(store, Leaking(), Cards(), citation_semantics_settings(foundation[1]))
    async def scenario():
        conv = store.create_conversation(student)["conversation_id"]
        run = store.enqueue(conv, student, "图书馆预约", "cross-first"); await runtime.complete(run)
        first_reply = store.deliverable(run, student)
        assert first_reply["state"] == "ACCEPTED", store.trace(run, foundation[-1])
        assert first_reply["citations"][0]["source_id"] == first.source_id
        conv2 = store.create_conversation(student)["conversation_id"]
        run2 = store.enqueue(conv2, student, "宿舍报修", "cross-second"); await runtime.complete(run2)
        reply = store.deliverable(run2, student)
        assert reply["state"] == "ACCEPTED", store.trace(run2, foundation[-1])
        assert reply["citations"][0]["source_id"] == second.source_id
        assert first.source_id not in reply["reply"]
        assert runtime.model.responses == 3
    asyncio.run(scenario())


@pytest.mark.parametrize("always_wrong", [False, True])
def test_citation_retry_preserves_sse_review_gate(tmp_path, always_wrong):
    class CitationModel(MockModel):
        def __init__(self): self.responses = 0
        async def complete(self, request):
            if request.task == AgentName.RESPONSE:
                self.responses += 1
                if self.responses == 1 or always_wrong:
                    return ModelReply(content="未经批准的引用内容[source:foreign-source]")
            return await super().complete(request)
    model = CitationModel()
    settings = citation_semantics_settings(Settings(environment="test", database_path=tmp_path / "citations.sqlite"))
    app = create_app(settings, model)
    with TestClient(app) as client:
        headers = login(client); conv = begin(client, headers)
        stream = post(client, conv, headers, "宿舍设施报修需要哪些信息？", stream=True)
        assert stream.status_code == 200
        run = stream.headers["x-run-id"]
        trace = client.get(f"/api/runs/{run}/trace", headers=login(client, "counselor_yu")).json()
        assert model.responses == 2, trace
        assert "未经批准的引用内容" not in stream.text
        final = client.get(f"/api/runs/{run}", headers=headers).json()
        assert final["state"] == ("SAFE_FALLBACK" if always_wrong else "ACCEPTED")
        assert decoded(stream.text)[-1]["event"] == "done"
        assert sum(e["code"] == "ONE_REGENERATION_ALLOWED" for e in trace["events"]) == 1


@pytest.mark.parametrize("message,case_count", [("读书时想梳理一些困惑。", 0), ("我无法保证自己的安全。", 1)])
def test_response_only_timeout_preserves_upstream_and_durable_case(foundation, message, case_count):
    class SlowUpstream(MockModel):
        async def complete(self, request):
            await asyncio.sleep(0.08)  # Deliberately exceeds the injected 50ms response-only timeout.
            return await super().complete(request)
    model = ScenarioModel(SlowUpstream()); model.configure("response_timeout")
    runtime, run, reply = execute(foundation, model, message)
    calls = runtime.store.trace(run, foundation[-1])["calls"]
    assert [(c["agent"], c["status"]) for c in calls] == [("IntentAgent", "SUCCESS"), ("RiskAgent", "SUCCESS"), ("ResponseAgent", "TIMED_OUT")]
    assert runtime.settings.model_timeout == foundation[1].model_timeout
    assert reply["reply"] == SAFE_REPLY and len(runtime.store.cases(foundation[-1])) == case_count
    if case_count:
        events = runtime.store.trace(run, foundation[-1])["events"]
        assert next(i for i,e in enumerate(events) if e["code"] == "RISK_AND_CASE_COMMITTED") < next(i for i,e in enumerate(events) if (e.get("result") or {}).get("agent") == "ResponseAgent")


def test_timeout_targets_only_second_designated_call():
    from campusbridge.agents import request_for
    model = ScenarioModel(MockModel()); model.configure("response_timeout", target_call=2)
    request = request_for(AgentName.RESPONSE, response_data())
    assert model.timeout_for(request) is None
    asyncio.run(model.complete(request))
    assert model.timeout_for(request) == 0.05
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(model.complete(request), timeout=0.05))
    assert model.records[-1]["controlled_injection"] and model.records[-1]["target_call"] == 2
    assert model.timeout_for(request) is None


def test_new_boundary_dataset_is_fixed_separate_and_unique():
    cases = load_cases(ROOT / "eval/stage5/20261012-boundary-v1/cases.jsonl", "stage5-boundary")
    previous = load_cases(ROOT / "eval/stage2/20261012-fixed-v1/cases.jsonl")
    assert len(cases) == 12 and len({tuple(c["turns"]) for c in cases}) == 12
    assert not {c["case_id"] for c in cases} & {c["case_id"] for c in previous}
    assert not {tuple(c["turns"]) for c in cases} & {tuple(c["turns"]) for c in previous}
    assert all(type(c["expected"]["needs_human_support"]) is bool for c in cases)


def test_boundary_packet_accepts_relative_case_path(tmp_path, monkeypatch):
    from campusbridge.evaluation import prepare_packet, verify_packet
    monkeypatch.chdir(ROOT)
    output = tmp_path / "boundary-packet"
    prepare_packet(ROOT, output, Path("eval/stage5/20261012-boundary-v1/cases.jsonl"), "stage5-boundary")
    assert verify_packet(ROOT, output)["case_count"] == 12
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["active_prompt"] == "prompts_v3.py"
