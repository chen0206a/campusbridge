import asyncio

import pytest
from sqlalchemy import select

from campusbridge.contracts import AgentName, RunState
from campusbridge.db import RiskEvent, SupportCase
from campusbridge.knowledge import DemoKnowledge
from campusbridge.models import MockModel
from campusbridge.policy import SAFE_REPLY
from campusbridge.runtime import CampusRuntime
from tests.helpers import ControlledModel


def execute(foundation, model=None, message="今天想聊聊学习安排", config=None, knowledge=None):
    database, settings, auth, store, student, counselor = foundation
    runtime = CampusRuntime(store, model or MockModel(), knowledge or DemoKnowledge(), config or settings)
    conversation = store.create_conversation(student)["conversation_id"]
    run_id = store.enqueue(conversation, student, message, "runtime-one")
    asyncio.run(runtime.complete(run_id))
    return runtime, run_id, store.deliverable(run_id, student)


def test_all_agents_execute_and_trace_typed_results(foundation):
    runtime, run_id, output = execute(foundation)
    trace = runtime.store.trace(run_id, foundation[-1])
    agents = [event["result"]["agent"] for event in trace["events"] if event["result"]]
    assert agents == [agent.value for agent in AgentName]
    assert output["state"] == "ACCEPTED"
    assert all(event["result"]["elapsed_ms"] >= 0 for event in trace["events"] if event["result"])
    assert trace["model_calls"] == 4
    assert not runtime.store.cases(foundation[-1])


def test_multiturn_context_contains_only_prior_turns(foundation):
    _, config, _, store, student, counselor = foundation
    model = ControlledModel()
    runtime = CampusRuntime(store, model, DemoKnowledge(), config)
    conversation = store.create_conversation(student)["conversation_id"]
    async def scenario():
        first = store.enqueue(conversation, student, "我在安排期中复习", "first")
        await runtime.complete(first)
        second = store.enqueue(conversation, student, "继续刚才的话题", "second")
        await runtime.complete(second)
        trace = store.trace(second, counselor)
        context = next(e["result"]["artifact"]["payload"] for e in trace["events"] if e["result"] and e["result"]["agent"] == "ContextAgent")
        assert [item["role"] for item in context["history"]] == ["user", "assistant"]
        assert context["history"][0]["text"] == "我在安排期中复习"
        assert "上一轮" in store.deliverable(second, student)["reply"]
        assert len(store.conversation_view(conversation, student)["messages"]) == 4
    asyncio.run(scenario())


def test_one_retry_then_pass(foundation):
    rejected = "被审核拒绝的候选文本甲。"
    model = ControlledModel(texts=[rejected, "我们可以一起选择一个小步骤。"], verdicts=["RETRY", "PASS"])
    runtime, run_id, result = execute(foundation, model)
    assert result["state"] == "ACCEPTED" and rejected not in result["reply"]
    assert model.responses == model.reviews == 2
    assert any(event["code"] == "ONE_REGENERATION_ALLOWED" for event in runtime.store.trace(run_id, foundation[-1])["events"])


def test_two_rejections_then_template_no_more_generation(foundation):
    model = ControlledModel(texts=["被拒候选乙。"], verdicts=["RETRY"])
    runtime, run_id, result = execute(foundation, model)
    assert model.responses == model.reviews == 2
    assert result["state"] == "SAFE_FALLBACK" and result["reply"] == SAFE_REPLY
    assert "被拒候选" not in str(runtime.store.trace(run_id, foundation[-2]))


@pytest.mark.parametrize("stage", [AgentName.INTENT, AgentName.RISK, AgentName.RESPONSE, AgentName.REVIEW])
@pytest.mark.parametrize("mode", ["timeout", "exception", "invalid"])
def test_model_faults_are_bounded_visible_and_safe(foundation, stage, mode):
    settings = foundation[1].model_copy(update={"model_timeout": 0.01})
    runtime, run_id, result = execute(foundation, ControlledModel(stage, mode), config=settings)
    trace = runtime.store.trace(run_id, foundation[-1])
    assert "private upstream" not in str(trace)
    failing = [e["result"] for e in trace["events"] if e["result"] and e["result"]["agent"] == stage.value and e["result"]["failure_code"]]
    assert failing
    if stage in {AgentName.RESPONSE, AgentName.REVIEW}:
        assert result["reply"] == SAFE_REPLY


def test_wrong_review_candidate_binding_falls_back(foundation):
    model = ControlledModel(mode="mismatch")
    runtime, run_id, result = execute(foundation, model)
    assert result["reply"] == SAFE_REPLY and model.responses == 1
    assert runtime.store.trace(run_id, foundation[-1])["failure_code"] == "REVIEW_IDENTITY_MISMATCH"


def test_case_is_committed_before_generation_and_survives_failure(foundation):
    database = foundation[0]
    model = ControlledModel(AgentName.RESPONSE, "exception")
    seen = []
    def inspect():
        with database.sessions() as session:
            seen.append((len(session.scalars(select(RiskEvent)).all()), len(session.scalars(select(SupportCase)).all())))
    # A successful pre-call inspector proves ordering even when generation itself raises.
    class Inspecting(ControlledModel):
        async def complete(self, request):
            if request.task == AgentName.RESPONSE:
                inspect()
            return await super().complete(request)
    runtime, run_id, result = execute(foundation, Inspecting(AgentName.RESPONSE, "exception"), "我无法保证自己的安全")
    assert seen == [(1, 1)] and result["reply"] == SAFE_REPLY
    case = runtime.store.cases(foundation[-1])[0]
    assert case["state"] == "OPEN"
    assert runtime.store.case_detail(case["case_id"], foundation[-1])["risk"]["level"] == "HIGH"


def test_case_and_risk_transaction_roll_back_together(foundation, monkeypatch):
    store, student = foundation[3], foundation[4]
    original = store._event
    def fail_risk_commit(session, run, event):
        if event.code == "RISK_AND_CASE_COMMITTED":
            raise RuntimeError("transaction injection")
        return original(session, run, event)
    monkeypatch.setattr(store, "_event", fail_risk_commit)
    model = ControlledModel()
    with pytest.raises(RuntimeError):
        execute(foundation, model, "我想轻生")
    assert model.responses == 0
    with foundation[0].sessions() as session:
        assert not session.scalars(select(RiskEvent)).all() and not session.scalars(select(SupportCase)).all()


def test_knowledge_route_and_retrieval_degradation(foundation):
    class Unavailable:
        async def search(self, query, campus_id):
            raise OSError("retrieval unavailable")
    runtime, run_id, result = execute(foundation, message="怎样预约校园服务", knowledge=Unavailable())
    payload = next(e["result"]["artifact"]["payload"] for e in runtime.store.trace(run_id, foundation[-1])["events"]
                   if e["result"] and e["result"]["agent"] == "ContextAgent")
    assert payload["degraded"] and payload["knowledge"] == [] and payload["route"] == "KNOWLEDGE_SUPPORT"
    assert result["state"] == "ACCEPTED"


def test_execution_count_budget(foundation):
    model = ControlledModel()
    runtime, run_id, result = execute(foundation, model, config=foundation[1].model_copy(update={"max_model_calls": 2}))
    assert len(model.requests) == 2 and result["reply"] == SAFE_REPLY
    assert runtime.store.trace(run_id, foundation[-1])["failure_code"] == "MODEL_CALL_BUDGET"
