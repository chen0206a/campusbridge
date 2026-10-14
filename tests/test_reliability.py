import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from campusbridge.api import create_app
from campusbridge.contracts import AgentName, Event, ModelReply, RiskLevel, RiskOutput, RunState
from campusbridge.db import Account, AgentRun, LoginToken
from campusbridge.knowledge import DemoKnowledge
from campusbridge.models import MockModel
from campusbridge.policy import SAFE_REPLY
from campusbridge.runtime import CampusRuntime
from campusbridge.settings import Settings
from tests.helpers import ControlledModel
from tests.test_api import begin, login, post
from tests.test_runtime import execute


def test_concurrent_turns_preserve_message_history_order(foundation):
    _, config, _, store, student, counselor = foundation
    runtime = CampusRuntime(store, MockModel(), DemoKnowledge(), config)
    conv = store.create_conversation(student)["conversation_id"]
    async def scenario():
        first = store.enqueue(conv, student, "第一轮的新事情", "concurrent-1")
        second = store.enqueue(conv, student, "第二轮的后续问题", "concurrent-2")
        await asyncio.gather(runtime.complete(first), runtime.complete(second))
        messages = store.conversation_view(conv, student)["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
        context = next(e["result"]["artifact"]["payload"] for e in store.trace(second, counselor)["events"]
                       if e["result"] and e["result"]["agent"] == "ContextAgent")
        assert context["history"][0]["text"] == "第一轮的新事情"
        assert all(m["text"] != "第二轮的后续问题" for m in context["history"])
    asyncio.run(scenario())


def test_duplicate_run_consumers_execute_model_chain_once(foundation):
    _, config, _, store, student, counselor = foundation
    model = ControlledModel()
    runtime = CampusRuntime(store, model, DemoKnowledge(), config)
    conv = store.create_conversation(student)["conversation_id"]
    first = store.enqueue(conv, student, "同一个请求", "duplicate")
    second = store.enqueue(conv, student, "同一个请求", "duplicate")
    async def scenario():
        await asyncio.gather(runtime.complete(first), runtime.complete(second))
    asyncio.run(scenario())
    assert first == second and len(model.requests) == 4
    assert len(store.conversation_view(conv, student)["messages"]) == 2


def test_disconnected_consumer_does_not_cancel_review_or_risk_case(foundation):
    class SlowReview(ControlledModel):
        async def complete(self, request):
            if request.task == AgentName.REVIEW:
                self.in_review = True
                await asyncio.sleep(0.03)
            return await super().complete(request)
    model = SlowReview()
    model.in_review = False
    _, config, _, store, student, counselor = foundation
    runtime = CampusRuntime(store, model, DemoKnowledge(), config)
    conv = store.create_conversation(student)["conversation_id"]
    run_id = store.enqueue(conv, student, "我无法保证自己的安全", "disconnect")
    async def scenario():
        consumer = asyncio.create_task(runtime.complete(run_id))
        while not model.in_review:
            await asyncio.sleep(0.001)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        await runtime.complete(run_id)
        assert store.deliverable(run_id, student)["state"] == "ACCEPTED"
        assert len(store.cases(counselor)) == 1
        await runtime.shutdown()
    asyncio.run(scenario())


def test_real_context_timeout_is_degraded_without_blocking_reply(foundation):
    class SlowKnowledge:
        async def search(self, query, campus_id):
            await asyncio.sleep(1)
            return ()
    runtime, run_id, output = execute(foundation, message="我想预约校园服务", knowledge=SlowKnowledge(),
                                    config=foundation[1].model_copy(update={"context_timeout": 0.005}))
    context = next(e["result"] for e in runtime.store.trace(run_id, foundation[-1])["events"]
                   if e["result"] and e["result"]["agent"] == "ContextAgent")
    assert context["status"] == "DEGRADED" and context["failure_code"] == "CONTEXT_TIMEOUT"
    assert output["state"] == "ACCEPTED"


@pytest.mark.parametrize("unsafe", ["你患有抑郁症。", "请发送 demo@example.test。", "风险等级：HIGH", "请不要联系任何人。"])
def test_rule_reviewer_blocks_diagnosis_privacy_labels_and_isolation(foundation, unsafe):
    model = ControlledModel(texts=[unsafe])
    runtime, run_id, output = execute(foundation, model)
    assert output["reply"] == SAFE_REPLY and model.responses == 2 and model.reviews == 0
    assert unsafe not in str(runtime.store.trace(run_id, foundation[-2]))


def test_model_cannot_downgrade_hard_danger_in_real_chain(foundation):
    class Downgrading(ControlledModel):
        async def complete(self, request):
            if request.task == AgentName.RISK:
                return ModelReply(content={"level": "LOW", "needs_human": False, "reason_codes": ["NO_SIGNAL"]})
            return await super().complete(request)
    runtime, run_id, result = execute(foundation, Downgrading(), "我准备伤害自己")
    case = runtime.store.cases(foundation[-1])[0]
    assert runtime.store.case_detail(case["case_id"], foundation[-1])["risk"]["level"] == "HIGH"
    assert "安全" in result["reply"] and "联系" in result["reply"]


def test_wrong_review_hash_is_rejected(foundation):
    class WrongHash(ControlledModel):
        async def complete(self, request):
            reply = await super().complete(request)
            if request.task == AgentName.REVIEW:
                return ModelReply(content={**reply.content, "content_hash": "a" * 64})
            return reply
    runtime, run_id, output = execute(foundation, WrongHash())
    assert output["state"] == "SAFE_FALLBACK"
    assert runtime.store.trace(run_id, foundation[-1])["failure_code"] == "REVIEW_IDENTITY_MISMATCH"


def test_known_risk_case_can_be_claimed_after_api_generation_failure(tmp_path):
    app = create_app(Settings(environment="test", database_path=tmp_path / "riskfail.sqlite3"),
                     ControlledModel(AgentName.RESPONSE, "exception"))
    with TestClient(app) as api:
        student, counselor = login(api), login(api, "counselor_yu")
        conv = begin(api, student)
        response = post(api, conv, student, "我无法保证自己的安全")
        assert response.status_code == 200 and response.json()["state"] == "SAFE_FALLBACK"
        case = api.get("/api/cases", headers=counselor).json()["cases"][0]
        assert api.post(f"/api/cases/{case['case_id']}/claim", headers={**counselor, "Idempotency-Key": "claim-after-fail"},
                        json={"expected_version": 0}).json()["state"] == "CLAIMED"


def test_restart_marks_incomplete_run_failed_and_preserves_committed_case(foundation):
    _, config, _, store, student, counselor = foundation
    conv = store.create_conversation(student)["conversation_id"]
    run_id = store.enqueue(conv, student, "我想轻生", "restart")
    store.begin(run_id, 12)
    store.persist_attention(run_id, RiskOutput(level=RiskLevel.HIGH, needs_human=True, reason_codes=("EXPLICIT_DANGER",)))
    store.record(run_id, Event(state=RunState.GENERATING, code="SIMULATED_PROCESS_STOP"))
    store.recover_interrupted()
    assert store.run_snapshot(run_id)["state"] == RunState.FAILED
    assert store.trace(run_id, counselor)["failure_code"] == "SERVER_RESTARTED"
    assert store.cases(counselor)[0]["state"] == "OPEN"


def test_expired_disabled_tokens_and_hashed_storage(tmp_path):
    app = create_app(Settings(environment="test", database_path=tmp_path / "auth.sqlite3"))
    with TestClient(app) as api:
        headers = login(api)
        raw = headers["Authorization"].split()[1]
        with app.state.database.sessions.begin() as session:
            credential = session.scalars(select(LoginToken)).one()
            assert credential.token_hash != raw and len(credential.token_hash) == 64
            credential.expires_at = 0
        assert api.post("/api/conversations", headers=headers).status_code == 401
        fresh = login(api)
        with app.state.database.sessions.begin() as session:
            account = session.scalar(select(Account).where(Account.username == "student_lan"))
            account.enabled = False
        assert api.post("/api/conversations", headers=fresh).status_code == 401


def test_late_generation_result_cannot_bypass_timeout(foundation):
    class Late(ControlledModel):
        async def complete(self, request):
            if request.task == AgentName.RESPONSE:
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    await asyncio.sleep(0.02)
                return ModelReply(content="迟到的结果。")
            return await super().complete(request)
    _, config, _, store, student, counselor = foundation
    runtime = CampusRuntime(store, Late(), DemoKnowledge(), config.model_copy(update={"model_timeout": 0.005}))
    conv = store.create_conversation(student)["conversation_id"]
    run_id = store.enqueue(conv, student, "开始聊聊", "late")
    async def scenario():
        await runtime.complete(run_id)
        assert store.deliverable(run_id, student)["reply"] == SAFE_REPLY
        await asyncio.sleep(0.04)
        assert "迟到的结果" not in str(store.conversation_view(conv, student))
        await runtime.shutdown()
    asyncio.run(scenario())


def test_global_time_budget_prevents_later_model_calls(foundation):
    model = ControlledModel(AgentName.INTENT, "timeout")
    runtime, run_id, output = execute(foundation, model, config=foundation[1].model_copy(update={"run_timeout": 0.005}))
    trace = runtime.store.trace(run_id, foundation[-1])
    assert trace["model_calls"] <= 1 and output["reply"] == SAFE_REPLY
    assert trace["failure_code"] == "RUN_TIME_BUDGET"


def test_knowledge_route_carries_source_revision_and_uses_reference(foundation):
    runtime, run_id, output = execute(foundation, message="我想预约图书馆的校园服务")
    context = next(e["result"]["artifact"]["payload"] for e in runtime.store.trace(run_id, foundation[-1])["events"]
                   if e["result"] and e["result"]["agent"] == "ContextAgent")
    assert context["route"] == "KNOWLEDGE_SUPPORT" and not context["degraded"]
    assert context["knowledge"][0]["source_id"] == "campus-demo-library"
    assert context["knowledge"][0]["revision"] == "original-v1"
    assert context["knowledge"][0]["text"] in output["reply"]
