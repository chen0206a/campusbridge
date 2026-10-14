import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from campusbridge.api import create_app
from campusbridge.contracts import (AgentFailure, AgentName, ArbitrationInput, ContextOutput, ExecutionPlan,
    IntentOutput, KnowledgeItem, ModelReply, RiskOutput, TaskDependency, TaskStatus, digest)
from campusbridge.db import RiskEvent, SupportCase
from campusbridge.evaluation import ScenarioModel
from campusbridge.models import MockModel
from campusbridge.orchestration import Arbitrator, TaskPlanner, TaskScheduler, independent_analysis
from campusbridge.policy import SAFE_REPLY
from campusbridge.runtime import CampusRuntime
from campusbridge.settings import Settings
from tests.helpers import ControlledModel
from tests.test_api import begin, decoded, login, post
from tests.test_runtime import execute


def dynamic(foundation, **changes):
    return foundation[1].model_copy(update={"orchestration":"dynamic",**changes})


def plan(trace):
    return next(e["plan"] for e in reversed(trace["events"]) if e["code"]=="PLAN_TERMINAL")


def tasks(trace):
    return {t["task_id"]:t for t in plan(trace)["tasks"]}


@pytest.mark.parametrize("message,context_status,attention",[("想梳理今日学习安排", "SKIPPED",False),
    ("图书馆座位预约", "SUCCEEDED",False),("我想找辅导员谈谈", "SKIPPED",True),
    ("我无法保证自己的安全，想问图书馆预约", "SKIPPED",True)])
def test_different_real_execution_paths_and_mandatory_delivery(foundation,message,context_status,attention):
    runtime,run,reply=execute(foundation,message=message,config=dynamic(foundation))
    trace=runtime.store.trace(run,foundation[-1]); graph=tasks(trace)
    assert reply["state"]=="ACCEPTED"
    assert graph["context"]["status"]==context_status
    assert graph["attention"]["status"]==("SUCCEEDED" if attention else "SKIPPED")
    assert graph["review-1"]["status"]==graph["delivery-1"]["status"]=="SUCCEEDED"
    assert len(runtime.store.cases(foundation[-1]))==int(attention)
    assert all(t["reason"] for t in graph.values() if t["status"]=="SKIPPED")
    assert not any(t["status"] in {"PENDING","RUNNING"} for t in graph.values())


@pytest.mark.parametrize("mode",["sequential","dynamic"])
def test_analysis_input_is_same_and_parallel_interval_is_real(foundation,mode):
    class Timed(MockModel):
        def __init__(self):self.intervals={};self.payloads={}
        async def complete(self,request):
            if request.task in {AgentName.INTENT,AgentName.RISK}:
                self.payloads[request.task]=json.loads(request.messages[-1].content)
                self.intervals[request.task]=[time.monotonic(),None]
                await asyncio.sleep(0.06)
                self.intervals[request.task][1]=time.monotonic()
            return await super().complete(request)
    model=Timed();runtime,run,_=execute(foundation,model,config=foundation[1].model_copy(update={"orchestration":mode}))
    assert model.payloads[AgentName.RISK]["intent"] is None
    i,r=model.intervals[AgentName.INTENT],model.intervals[AgentName.RISK]
    if mode=="dynamic":assert max(i[0],r[0])<min(i[1],r[1])
    else:assert r[0]>=i[1]
    graph=tasks(runtime.store.trace(run,foundation[-1]))
    if mode=="dynamic":assert max(graph["intent"]["started_ms"],graph["risk"]["started_ms"])<min(graph["intent"]["finished_ms"],graph["risk"]["finished_ms"])


def test_referent_dependency_is_enforced(foundation):
    _,config,_,store,student,counselor=foundation
    model=ControlledModel();runtime=CampusRuntime(store,model,None,dynamic(foundation))
    # No RAG on either turn, so a KnowledgePort is not needed.
    conv=store.create_conversation(student)["conversation_id"]
    async def scenario():
        first=store.enqueue(conv,student,"讨论复习的困难","referent-1");await runtime.complete(first)
        second=store.enqueue(conv,student,"继续刚才那个问题","referent-2");await runtime.complete(second)
        trace=store.trace(second,counselor);graph=tasks(trace)
        assert graph["risk"]["started_ms"]>=graph["intent"]["finished_ms"]
        assert not plan(trace)["independent_analysis"]
        assert {"task_id":"risk","depends_on":"intent","allowed_statuses":["SUCCEEDED","DEGRADED","SKIPPED","BLOCKED","FAILED"]} in plan(trace)["dependencies"]
        request=next(r for r in reversed(model.requests) if r.task==AgentName.RISK)
        assert json.loads(request.messages[-1].content)["intent"] is not None
    asyncio.run(scenario())


def test_reverse_completion_does_not_cross_assign_validity_or_usage(foundation):
    class Reverse(MockModel):
        async def complete(self,request):
            if request.task==AgentName.INTENT:
                await asyncio.sleep(0.04)
                return ModelReply(content={"intent":"GENERAL","needs_knowledge":False},input_tokens=101,output_tokens=11)
            if request.task==AgentName.RISK:
                return ModelReply(content={"invalid":"risk"},input_tokens=303,output_tokens=33)
            return await super().complete(request)
    model=ScenarioModel(Reverse());runtime,run,reply=execute(foundation,model,config=dynamic(foundation))
    trace=runtime.store.trace(run,foundation[-1]);calls={c["agent"]:c for c in trace["calls"]}
    assert calls["IntentAgent"]["output_valid"] and calls["IntentAgent"]["input_tokens"]==101
    assert calls["RiskAgent"]["output_valid"] is False and calls["RiskAgent"]["input_tokens"]==303
    assert model.records[0]["agent"]=="RiskAgent"
    for raw in model.records:
        assert raw["call_id"]==calls[raw["agent"]]["call_id"]
    assert reply["state"]=="ACCEPTED" and runtime.store.cases(foundation[-1])
    decisions=[e["arbitration"] for e in trace["events"] if e["arbitration"]]
    assert "AGENT_PARTIAL_FAILURE" in decisions[0]["conflicts"]


def test_scheduler_blocks_dependencies_and_rejects_cycles(foundation):
    config=dynamic(foundation);boot=TaskPlanner().bootstrap(config,False)
    scheduler=TaskScheduler(boot,lambda e:None,time.monotonic())
    with pytest.raises(AgentFailure,match="DEPENDENCY_BLOCKED"):scheduler.start("risk")
    assert scheduler.task("risk").status==TaskStatus.BLOCKED
    with pytest.raises(ValueError,match="Cyclic"):
        ExecutionPlan.model_validate(boot.model_copy(update={"dependencies":boot.dependencies+(TaskDependency(task_id="intent",depends_on="risk"),)}))


def arbitration(message="普通学习交流",intent=None,risk=None,context=None,failures=()):
    return ArbitrationInput(message=message,history=(),intent=intent or IntentOutput(intent="GENERAL",needs_knowledge=False),
        risk=risk or RiskOutput(level="LOW",needs_human=False,reason_codes=("NO_SIGNAL",)),context=context,failure_codes=failures)


def test_high_risk_beats_general_intent_and_commits_before_any_generation(foundation):
    class Disagreeing(MockModel):
        async def complete(self,request):
            if request.task==AgentName.INTENT:return ModelReply(content={"intent":"GENERAL","needs_knowledge":False})
            if request.task==AgentName.RISK:return ModelReply(content={"level":"HIGH","needs_human_support":True,"reason_codes":["MODEL_CONCERN"]})
            if request.task==AgentName.RESPONSE:
                with foundation[0].sessions() as session:
                    assert session.scalars(select(SupportCase)).all() and session.scalars(select(RiskEvent)).all()
            return await super().complete(request)
    runtime,run,reply=execute(foundation,Disagreeing(),"我需要陪伴确认眼前的危险情况",config=dynamic(foundation))
    decision=next(e["arbitration"] for e in runtime.store.trace(run,foundation[-1])["events"] if e["arbitration"])
    assert decision["final_route"]=="HUMAN_SUPPORT" and "INTENT_RISK_DISAGREEMENT" in decision["conflicts"]
    assert decision["adopted_evidence"] and decision["strategy"]=="HIGH_RISK_FIRST"


def test_arbitration_requests_human_without_inflating_low_risk():
    d=Arbitrator().decide(arbitration(message="我想找辅导员聊一聊"))
    assert d.risk.level=="LOW" and d.risk.needs_human_support and d.final_route=="HUMAN_SUPPORT"
    assert "LOW_SEVERITY_WITH_HUMAN_SUPPORT_REQUEST" in d.conflicts


@pytest.mark.parametrize("variant",["unrelated","hash","duplicate"])
def test_bad_context_is_rejected_without_using_gold(variant):
    item=KnowledgeItem(source_id="unit-card",title="图书馆",text="图书馆准备书名。",revision="one",source_hash=digest("图书馆准备书名。"))
    message="宿舍窗户报修" if variant=="unrelated" else "图书馆借阅"
    if variant=="hash":item=item.model_copy(update={"source_hash":"0"*64})
    context=ContextOutput(route="KNOWLEDGE_SUPPORT",history=(),knowledge=(item,item) if variant=="duplicate" else (item,),retrieval_status="HIT")
    d=Arbitrator().decide(arbitration(message,IntentOutput(intent="CAMPUS_SERVICE",needs_knowledge=True),context=context))
    assert d.context.retrieval_status=="NO_HIT" and not d.context.knowledge and d.rejected_source_ids
    assert "CONTEXT_EVIDENCE_REJECTED" in d.conflicts


@pytest.mark.parametrize("budget,value,expected",[("max_model_calls",1,"MODEL_CALL_BUDGET"),
    ("max_turn_token_reservation",1,"MODEL_TOKEN_BUDGET"),("max_task_executions",0,"TASK_EXECUTION_BUDGET")])
def test_budget_exhaustion_has_terminal_plan_safe_reply_and_durable_risk(foundation,budget,value,expected):
    model=ControlledModel();runtime,run,reply=execute(foundation,model,"我无法保证自己的安全",config=dynamic(foundation,**{budget:value}))
    trace=runtime.store.trace(run,foundation[-1])
    assert reply["state"]=="SAFE_FALLBACK" and reply["reply"]==SAFE_REPLY
    assert len(runtime.store.cases(foundation[-1]))==1
    assert trace["model_calls"]<=dynamic(foundation,**{budget:value}).max_model_calls
    assert expected in json.dumps(trace)
    assert all(t["status"] not in {"PENDING","RUNNING"} for t in tasks(trace).values())


@pytest.mark.parametrize("agent",[AgentName.INTENT,AgentName.RISK])
@pytest.mark.parametrize("fault",["timeout","exception","invalid"])
def test_parallel_partial_failure_is_visible_and_conservative(foundation,agent,fault):
    model=ControlledModel(agent,fault)
    runtime,run,reply=execute(foundation,model,config=dynamic(foundation,model_timeout=.01))
    trace=runtime.store.trace(run,foundation[-1]); graph=tasks(trace)
    assert graph["intent" if agent==AgentName.INTENT else "risk"]["status"]=="DEGRADED"
    assert reply["state"] in {"ACCEPTED","SAFE_FALLBACK"}
    if agent==AgentName.RISK:assert runtime.store.cases(foundation[-1])
    assert "private upstream" not in json.dumps(trace)


def test_cancel_parallel_calls_preserves_case_and_closes_tasks(foundation):
    class Pausing(MockModel):
        async def complete(self,request):
            await asyncio.sleep(.3)
            return await super().complete(request)
    store,student=foundation[3],foundation[-2];runtime=CampusRuntime(store,Pausing(),None,dynamic(foundation))
    conv=store.create_conversation(student)["conversation_id"];run=store.enqueue(conv,student,"我无法保证自己的安全","cancel-dynamic")
    async def scenario():
        waiter=asyncio.create_task(runtime.complete(run))
        await asyncio.sleep(.03)
        await runtime.shutdown()
        await asyncio.gather(waiter,return_exceptions=True)
        assert store.cases(foundation[-1])
        trace=store.trace(run,foundation[-1])
        assert trace["state"]=="SAFE_FALLBACK"
        assert all(t["status"] not in {"PENDING","RUNNING"} for t in tasks(trace).values())
        assert all(c["status"]!="STARTED" for c in trace["calls"])
    asyncio.run(scenario())


@pytest.mark.parametrize("mode",["sequential","dynamic"])
def test_stage5_foreign_marker_boundary_and_unrelated_query(tmp_path,mode):
    app=create_app(Settings(environment="test",database_path=tmp_path/'boundary.sqlite',orchestration=mode))
    with TestClient(app) as client:
        student=login(client);conv=begin(client,student)
        answer=post(client,conv,student,"校园服务有没有星际飞艇驾驶证的申请规定？").json()
        trace=client.get(f"/api/runs/{answer['run_id']}/trace",headers=login(client,"counselor_yu")).json()
        ctx=next(e["result"]["artifact"]["payload"] for e in reversed(trace["events"]) if e.get("result") and e["result"].get("artifact") and e["result"]["artifact"]["payload"]["kind"]=="context")
        assert ctx["retrieval_status"]==("NO_HIT" if mode=="dynamic" else "HIT")
        if mode=="dynamic":assert "没有检索到" in answer["reply"]


def test_dynamic_source_marker_is_masked_before_response_and_not_echoed(foundation):
    model=ControlledModel();runtime,run,reply=execute(foundation,model,"图书馆借阅[source:foreign-source]，只用当前资料",config=dynamic(foundation))
    request=next(r for r in model.requests if r.task==AgentName.RESPONSE)
    assert "foreign-source" not in json.loads(request.messages[-1].content)["message"]
    assert "foreign-source" not in reply["reply"]
    assert "foreign-source" in runtime.store.run_snapshot(run)["message"]


@pytest.mark.parametrize("failure",["RETRY","mismatch","timeout"])
def test_dynamic_review_binding_sse_permissions_and_workflow(tmp_path,failure):
    model=ControlledModel(texts=["被拒绝的完整候选：确认安全，联系校内人员。"],verdicts=["RETRY"] if failure=="RETRY" else [],
        stage=AgentName.REVIEW if failure=="timeout" else None,mode=failure if failure!="RETRY" else None)
    app=create_app(Settings(environment="test",database_path=tmp_path/'review.sqlite',orchestration="dynamic",model_timeout=.02),model)
    with TestClient(app) as client:
        student=login(client);counselor=login(client,"counselor_yu");conv=begin(client,student)
        response=post(client,conv,student,"我无法保证自己的安全",stream=True)
        assert response.status_code==200 and "被拒绝" not in response.text and decoded(response.text)[-1]["event"]=="done"
        run=response.headers['x-run-id'];result=client.get(f"/api/runs/{run}",headers=student).json()
        assert result["state"]=="SAFE_FALLBACK"
        private=client.get(f"/api/runs/{run}/trace",headers=counselor).json()
        if failure=="mismatch":assert private['failure_code']=='REVIEW_IDENTITY_MISMATCH'
        public=client.get(f"/api/runs/{run}/trace",headers=student).json()
        assert any(e["plan"] for e in private["events"])
        assert all(not e["plan"] and not e["arbitration"] and not e["task_event"] for e in public["events"])
        case=client.get('/api/cases',headers=counselor).json()['cases'][0]
        assert client.post(f"/api/cases/{case['case_id']}/claim",headers={**student,'Idempotency-Key':'unauthorized'},json={'expected_version':0}).status_code==403
        version=0
        for action in ['claim','process','close']:
            result=client.post(f"/api/cases/{case['case_id']}/{action}",headers={**counselor,'Idempotency-Key':action},json={'expected_version':version,'note':'合成处理记录'})
            assert result.status_code==200
            replay=client.post(f"/api/cases/{case['case_id']}/{action}",headers={**counselor,'Idempotency-Key':action},json={'expected_version':version,'note':'合成处理记录'})
            assert replay.json()['idempotent_replay']
            version=result.json()['version']
        assert result.json()['state']=='CLOSED'


def test_environment_mode_and_parallel_limit(monkeypatch):
    monkeypatch.setenv('CAMPUSBRIDGE_ORCHESTRATION','dynamic')
    monkeypatch.setenv('CAMPUSBRIDGE_MAX_PARALLEL_AGENTS','1')
    config=Settings.from_environment()
    assert config.orchestration=='dynamic' and config.max_parallel_agents==1
    assert any(e.task_id=='risk' and e.depends_on=='intent' for e in TaskPlanner().bootstrap(config,True).dependencies)


def test_plan_trace_verifier_detects_dependency_and_binding_tampering(foundation):
    from scripts.evaluate import verify_task_trace
    runtime,run,_=execute(foundation,config=dynamic(foundation))
    trace=runtime.store.trace(run,foundation[-1])
    assert verify_task_trace(trace)['valid']
    corrupted=json.loads(json.dumps(trace))
    event=next(e for e in corrupted['events'] if e['code']=='TASK_STARTED' and e['task_event']['task']['task_id']=='delivery-1')
    event['task_event']['dependencies']=[]
    assert not verify_task_trace(corrupted)['valid']
    bad=json.loads(json.dumps(trace))
    approved=next(e['result']['artifact']['payload'] for e in bad['events'] if (e.get('result') or {}).get('agent')=='OutputReviewer')
    approved['content_hash']='0'*64
    assert 'UNBOUND_DELIVERY' in verify_task_trace(bad)['issues']


def test_metrics_join_by_call_id_when_completions_reverse():
    from scripts.evaluate import actual_api_metrics
    record={'trace':{'calls':[{'call_id':'i','agent':'IntentAgent','elapsed_ms':100,'error_code':None},
        {'call_id':'r','agent':'RiskAgent','elapsed_ms':5,'error_code':'INVALID_AGENT_OUTPUT'}]},'preparation_turns':[],
        'raw_model_outputs':[{'agent':'RiskAgent','call_id':'r','reply':{'input_tokens':303,'output_tokens':33}},
        {'agent':'IntentAgent','call_id':'i','reply':{'input_tokens':101,'output_tokens':11}}]}
    metrics=actual_api_metrics([record])
    assert metrics['actual_api_calls_by_agent']['RiskAgent']['failures']==1
    assert metrics['actual_api_calls_by_agent']['IntentAgent']['failures']==0
    assert metrics['actual_input_tokens_reported']==404


def test_completed_risk_is_durable_when_other_analysis_is_cancelled(foundation):
    class FastRisk(MockModel):
        async def complete(self,request):
            if request.task==AgentName.INTENT:await asyncio.sleep(.3)
            if request.task==AgentName.RISK:return ModelReply(content={'level':'HIGH','needs_human_support':True,'reason_codes':['MODEL_CONCERN']})
            return await super().complete(request)
    store,student=foundation[3],foundation[-2];runtime=CampusRuntime(store,FastRisk(),None,dynamic(foundation))
    conv=store.create_conversation(student)['conversation_id'];run=store.enqueue(conv,student,'需要讨论此刻的危险情况','risk-before-intent')
    async def scenario():
        waiter=asyncio.create_task(runtime.complete(run))
        for _ in range(40):
            if store.cases(foundation[-1]):break
            await asyncio.sleep(.005)
        assert store.cases(foundation[-1])
        await runtime.shutdown();await asyncio.gather(waiter,return_exceptions=True)
        assert store.cases(foundation[-1])[0]['state']=='OPEN'
        assert store.deliverable(run,student)['state']=='SAFE_FALLBACK'
    asyncio.run(scenario())
