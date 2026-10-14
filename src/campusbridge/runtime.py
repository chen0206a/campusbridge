from __future__ import annotations

import asyncio
import time
import json
from contextvars import ContextVar
from dataclasses import dataclass

from campusbridge.agents import ContextAgent, IntentAgent, OutputReviewer, ResponseAgent, RiskAgent
from campusbridge.contracts import (
    AgentFailure, AgentName, AgentResult, Artifact, CandidateOutput, ContextInput, ContextOutput, Event,
    FINAL_STATES, IntentInput, IntentKind, IntentOutput, ModelReply, ResponseInput, ResultStatus,
    ReviewInput, RiskInput, RiskLevel, RiskOutput, Route, RunState, Verdict, digest, identifier,
    ArbitrationInput, Task, TaskDependency, TaskStatus,
)
from campusbridge.policy import SAFE_REPLY, combine_risk, explicit_danger, template_approval, valid_binding
from campusbridge.orchestration import Arbitrator, TaskPlanner, TaskScheduler, independent_analysis


class CampusRuntime:
    """Explicit, bounded, single-process orchestration with durable business checkpoints."""
    def __init__(self, store, model, knowledge, settings):
        self.store, self.model, self.settings = store, model, settings
        self.intent, self.risk = IntentAgent(), RiskAgent()
        self.context = ContextAgent(knowledge)
        self.response, self.reviewer = ResponseAgent(), OutputReviewer()
        self.jobs = {}
        self.conversation_locks = {}
        self.external_tasks = set()

    async def bounded(self, coroutine, seconds):
        task = asyncio.create_task(coroutine)
        self.external_tasks.add(task)
        def cleanup(done):
            self.external_tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(cleanup)
        try:
            finished, _ = await asyncio.wait({task}, timeout=max(0, seconds))
            if not finished:
                task.cancel()
                raise TimeoutError()
            return task.result()
        finally:
            if not task.done():
                task.cancel()

    async def complete(self, run_id):
        snapshot = self.store.run_snapshot(run_id)
        if snapshot["state"] in FINAL_STATES:
            return
        if run_id not in self.jobs:
            task = asyncio.create_task(self._serialized(run_id, snapshot["conversation_id"]))
            self.jobs[run_id] = task
            def finished(done):
                self.jobs.pop(run_id, None)
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(finished)
        await asyncio.shield(self.jobs[run_id])

    async def _serialized(self, run_id, conversation_id):
        lock = self.conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            if self.store.run_snapshot(run_id)["state"] in FINAL_STATES:
                return
            execution = DynamicRunExecution if self.settings.orchestration == "dynamic" else RunExecution
            await execution(self, run_id).execute()

    async def shutdown(self):
        jobs = list(self.jobs.values())
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        calls = list(self.external_tasks)
        for task in calls:
            task.cancel()
        if calls:
            await asyncio.wait(calls, timeout=0.2)
        if hasattr(self.model, "aclose"):
            await self.model.aclose()


@dataclass(frozen=True)
class CompletedStep:
    result: AgentResult
    output: object
    call_id: str | None
    cancelled: bool = False


class RunExecution:
    def __init__(self, runtime, run_id):
        self.runtime, self.run_id, self.store = runtime, run_id, runtime.store
        self.snapshot = self.store.run_snapshot(run_id)
        self.started = time.monotonic()
        self.deadline = self.started + runtime.settings.run_timeout
        self.call_context = ContextVar("campusbridge-call-id", default=None)
        self.writer_lock = asyncio.Lock()
        self.last_call_id = None
        self.token_reservation = 0
        self.scheduler = None
        self.audit_events = []
        self.planner, self.arbitrator = TaskPlanner(), Arbitrator()
        self.analysis_failures = []

    def audit(self, event):
        event=event.model_copy(update={"state":self.store.run_snapshot(self.run_id)["state"]})
        if self.runtime.settings.orchestration == "sequential":
            self.audit_events.append(event)  # Preserve the existing sequential Agent trace order.
        else:
            self.store.audit(self.run_id, event)

    def initialize_plan(self, message, history):
        self.independent = independent_analysis(message, history)
        plan = self.planner.bootstrap(self.runtime.settings, self.independent, explicit_danger(message))
        self.scheduler = TaskScheduler(plan, self.audit, self.started)
        self.checkpoint("early-attention",lambda:self.store.persist_attention(self.run_id,
            combine_risk(message,RiskOutput(level="LOW",needs_human=False,reason_codes=("NO_SIGNAL",)))))

    def checkpoint(self, task_id, operation):
        if not self.scheduler.start(task_id):return None
        try:
            output=operation()
        except BaseException:
            self.scheduler.finish(task_id,TaskStatus.FAILED,"CHECKPOINT_FAILED")
            raise
        self.scheduler.finish(task_id,TaskStatus.SUCCEEDED)
        return output

    def risk_checkpoint(self, risk):
        task=self.scheduler.task("risk-checkpoint").model_copy(update={"enabled":risk.needs_human_support,
            "reason":"INDEPENDENT_RISK_REQUIRES_ATTENTION" if risk.needs_human_support else "RISK_DOES_NOT_REQUIRE_ATTENTION"})
        self.scheduler.update(task)
        self.scheduler.update_plan(self.scheduler.plan)
        self.checkpoint("risk-checkpoint",lambda:self.store.persist_attention(self.run_id,risk))

    def remaining(self):
        return max(0, self.deadline - time.monotonic())

    async def invoke(self, request):
        if self.remaining() <= 0:
            raise AgentFailure("RUN_TIME_BUDGET")
        body = json.dumps([m.model_dump(mode="json") for m in request.messages], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        reservation = len(body) + 256 * len(request.messages) + 128 + request.max_output_tokens
        async with self.writer_lock:
            if self.token_reservation + reservation > self.runtime.settings.max_turn_token_reservation:
                raise AgentFailure("MODEL_TOKEN_BUDGET")
            call_id = self.store.start_model_call(self.run_id, request.task, self.runtime.settings.max_model_calls,
                getattr(self.runtime.model, "provider_name", "custom"), getattr(self.runtime.model, "model_name", "custom"))
            if call_id is None:
                raise AgentFailure("MODEL_CALL_BUDGET")
            self.token_reservation += reservation
        self.call_context.set(call_id)
        request = request.model_copy(update={"trace_call_id": call_id})
        started = time.monotonic()
        try:
            timeout = self.runtime.settings.model_timeout
            # Trusted evaluation ports may narrow one selected call; no global deadline mutation.
            timeout_for = getattr(self.runtime.model, "timeout_for", None)
            if timeout_for:
                scoped = timeout_for(request)
                if scoped is not None:
                    timeout = min(timeout, scoped)
            reply = await self.runtime.bounded(self.runtime.model.complete(request),
                                              min(timeout, self.remaining()))
            reply = ModelReply.model_validate(reply)
        except BaseException as exc:
            status = "CANCELLED" if isinstance(exc, asyncio.CancelledError) else "TIMED_OUT" if isinstance(exc, TimeoutError) else "FAILED"
            code = exc.code if isinstance(exc, AgentFailure) else "MODEL_TIMEOUT" if isinstance(exc, TimeoutError) else "MODEL_CALL_INTERRUPTED" if isinstance(exc, asyncio.CancelledError) else "MODEL_CALL_FAILED"
            async with self.writer_lock:
                self.store.finish_model_call(call_id, status, (time.monotonic() - started) * 1000, code,
                    reply=exc.usage if isinstance(exc, AgentFailure) else None)
            raise
        async with self.writer_lock:
            self.store.finish_model_call(call_id, "SUCCESS", (time.monotonic() - started) * 1000, reply=reply)
        return reply

    async def collect_step(self, agent, summary, operation, fallback=None, forced_failure=None):
        started = time.monotonic()
        failure = None
        scope = self.call_context.set(None)
        cancelled = False
        try:
            if forced_failure:
                raise AgentFailure(forced_failure)
            output = await operation()
            status = ResultStatus.SUCCESS
            async with self.writer_lock:
                self.store.model_output_validity(self.call_context.get(), True)
        except asyncio.CancelledError:
            output, status, failure, cancelled = None, ResultStatus.FAILED, "PROCESS_STOPPED", True
        except Exception as exc:
            failure = exc.code if isinstance(exc, AgentFailure) else (
                ("CONTEXT_TIMEOUT" if agent == AgentName.CONTEXT else "MODEL_TIMEOUT") if isinstance(exc, TimeoutError) else
                "INVALID_AGENT_OUTPUT" if isinstance(exc, (ValueError, TypeError)) else "AGENT_EXCEPTION")
            output = fallback() if fallback else None
            status = ResultStatus.DEGRADED if fallback else ResultStatus.FAILED
            async with self.writer_lock:
                self.store.model_output_validity(self.call_context.get(), False, failure)
        artifact = Artifact(producer=agent, payload=output) if output is not None else None
        result = AgentResult(agent=agent, status=status, input_summary=summary, artifact=artifact,
                             elapsed_ms=(time.monotonic() - started) * 1000, failure_code=failure)
        call_id = self.call_context.get()
        self.call_context.reset(scope)
        return CompletedStep(result, output, call_id, cancelled)

    def apply_step(self, completed, state, task_id):
        self.last_call_id = completed.call_id
        self.store.record(self.run_id, Event(state=state, code="AGENT_INTERRUPTED" if completed.cancelled else "AGENT_COMPLETED", result=completed.result), completed.result.failure_code)
        if self.scheduler and task_id:
            self.scheduler.token_reservation = self.token_reservation
            status = TaskStatus.CANCELLED if completed.cancelled else {ResultStatus.SUCCESS:TaskStatus.SUCCEEDED,
                ResultStatus.DEGRADED:TaskStatus.DEGRADED,ResultStatus.FAILED:TaskStatus.FAILED,ResultStatus.SKIPPED:TaskStatus.SKIPPED}[completed.result.status]
            self.scheduler.finish(task_id,status,completed.result.failure_code)
        if completed.result.failure_code and completed.result.agent in {AgentName.INTENT, AgentName.RISK}:
            self.analysis_failures.append(completed.result.agent.value+":"+completed.result.failure_code)
        return completed.output

    async def step(self, agent, state, summary, operation, fallback=None, task_id=None, skipped_output=None):
        failure = None
        if self.scheduler and task_id:
            try:
                started = self.scheduler.start(task_id)
                if not started:
                    result = AgentResult(agent=agent,status=ResultStatus.SKIPPED,input_summary=summary,
                        artifact=Artifact(producer=agent,payload=skipped_output) if skipped_output is not None else None,elapsed_ms=0)
                    return self.apply_step(CompletedStep(result,skipped_output,None),state,task_id)
            except AgentFailure as exc:
                failure = exc.code
        completed = await self.collect_step(agent,summary,operation,fallback,failure)
        output = self.apply_step(completed,state,task_id)
        if completed.cancelled:
            raise asyncio.CancelledError()
        if output is None:
            raise AgentFailure(completed.result.failure_code)
        return output

    def arbitrate(self, data, task_id):
        self.scheduler.start(task_id)
        decision = self.arbitrator.decide(data,dynamic=self.runtime.settings.orchestration=="dynamic")
        self.audit(Event(state=RunState.ANALYZING,code="ARBITRATION_DECIDED",arbitration=decision))
        self.scheduler.finish(task_id,TaskStatus.SUCCEEDED)
        return decision

    async def analyze(self, message, history):
        intent_input = IntentInput(message=message,history=history)
        intent = await self.step(AgentName.INTENT,RunState.ANALYZING,
            {"message_chars":len(message),"history_messages":len(history)},
            lambda:self.runtime.intent.run(intent_input,self.invoke),
            lambda:IntentOutput(intent=IntentKind.SUPPORT,needs_knowledge=False),task_id="intent")
        risk_input = RiskInput(message=message,history=history,intent=None if self.independent else intent)
        async def risk_operation():
            result = await self.runtime.risk.run(risk_input,self.invoke)
            return combine_risk(message,result,intent)
        risk = await self.step(AgentName.RISK,RunState.ANALYZING,
            {"message_chars":len(message),"history_messages":len(history),"intent":"independent" if self.independent else intent.intent.value},
            risk_operation,lambda:combine_risk(message,RiskOutput(level="UNKNOWN",needs_human=True,reason_codes=("ASSESSMENT_UNAVAILABLE",)),intent),task_id="risk")
        self.risk_checkpoint(risk)
        return intent,risk

    async def prepare(self, message, history):
        self.initialize_plan(message,history)
        intent,risk = await self.analyze(message,history)
        decision = self.arbitrate(ArbitrationInput(message=message,history=history,intent=intent,risk=risk,failure_codes=tuple(self.analysis_failures)),"arbitrate")
        self.scheduler.update_plan(self.planner.expand(self.scheduler.plan,decision))
        if decision.risk != risk:
            resolved=AgentResult(agent=AgentName.RISK,status=ResultStatus.DEGRADED,input_summary={"server_arbitration":True},artifact=Artifact(producer=AgentName.RISK,payload=decision.risk),elapsed_ms=0)
            self.store.record(self.run_id,Event(state=RunState.ANALYZING,code="RISK_RESOLVED",result=resolved))
        risk=decision.risk
        self.checkpoint("attention",lambda:self.store.persist_attention(self.run_id,risk))
        data=ContextInput(message=message,history=history,intent=intent,risk=risk,campus_id=self.snapshot["campus_id"])
        skipped=ContextOutput(route=decision.final_route,history=history,knowledge=(),retrieval_status="SKIPPED")
        context=await self.step(AgentName.CONTEXT,RunState.CONTEXT_READY,
            {"history_messages":len(history),"knowledge_requested":intent.needs_knowledge},
            lambda:self.runtime.bounded(self.runtime.context.run(data),min(self.runtime.settings.context_timeout,self.remaining())),
            lambda:ContextOutput(route=decision.final_route,history=history,knowledge=(),degraded=True,retrieval_status="ERROR" if decision.final_route==Route.KNOWLEDGE_SUPPORT else "SKIPPED"),
            task_id="context",skipped_output=skipped)
        resolved=self.arbitrate(ArbitrationInput(message=message,history=history,intent=intent,risk=risk,
            context=context,failure_codes=tuple(self.analysis_failures)),"evidence")
        if resolved.context != context:
            result=AgentResult(agent=AgentName.CONTEXT,status=ResultStatus.DEGRADED,input_summary={"server_arbitration":True},
                artifact=Artifact(producer=AgentName.CONTEXT,payload=resolved.context),elapsed_ms=0)
            self.store.record(self.run_id,Event(state=RunState.CONTEXT_READY,code="CONTEXT_RESOLVED",result=result))
        return resolved.response_message,risk,resolved.context

    def abandon_attempt(self, attempt, reason):
        for name in (f"review-{attempt}",f"delivery-{attempt}"):
            if self.scheduler.task(name).status==TaskStatus.PENDING:self.scheduler.skip(name,reason)

    def regenerate(self, attempt, reason):
        self.abandon_attempt(attempt,reason)
        self.scheduler.update_plan(self.planner.add_attempt(self.scheduler.plan,attempt+1))

    async def execute(self):
        message = self.snapshot["message"]
        try:
            history = self.store.begin(self.run_id, self.runtime.settings.history_messages)
            try:
                message,risk,context = await self.prepare(message,history)
            except AgentFailure as exc:
                self.store.record(self.run_id,Event(state=RunState.ANALYZING,code="PREPARATION_FALLBACK"),exc.code)
                self.fallback()
                return
            feedback = ()
            for attempt in (1, 2):
                data = ResponseInput(message=message, context=context, risk=risk, attempt=attempt, feedback_codes=feedback)
                try:
                    candidate = await self.step(AgentName.RESPONSE, RunState.GENERATING,
                        {"attempt": attempt, "history_messages": len(context.history), "route": context.route.value,
                         "source_ids": [item.source_id for item in context.knowledge]},
                        lambda: self.runtime.response.run(data, self.invoke),task_id=f"response-{attempt}")
                    review = await self.step(AgentName.REVIEW, RunState.REVIEWING,
                        {"candidate_id": candidate.candidate_id, "content_hash": candidate.content_hash,
                         "candidate_chars": len(candidate.text)},
                        lambda: self.runtime.reviewer.run(ReviewInput(candidate=candidate, risk=risk, references=context.knowledge), self.invoke),task_id=f"review-{attempt}")
                except AgentFailure as exc:
                    if attempt == 1 and exc.code.startswith("CITATION_"):
                        feedback = (exc.code,)
                        self.store.record(self.run_id, Event(state=RunState.REGENERATING, code="ONE_REGENERATION_ALLOWED"))
                        self.regenerate(attempt,exc.code)
                        continue
                    self.fallback()
                    return
                if not valid_binding(candidate, review):
                    self.store.model_output_validity(self.last_call_id, False, "REVIEW_IDENTITY_MISMATCH")
                    self.store.record(self.run_id, Event(state=RunState.REVIEWING, code="REVIEW_IDENTITY_MISMATCH"),
                                      "REVIEW_IDENTITY_MISMATCH")
                    self.fallback()
                    return
                if review.verdict == Verdict.PASS:
                    self.scheduler.start(f"delivery-{attempt}")
                    self.store.commit_reply(self.run_id, candidate, review, RunState.ACCEPTED)
                    self.scheduler.finish(f"delivery-{attempt}",TaskStatus.SUCCEEDED)
                    return
                self.scheduler.skip(f"delivery-{attempt}","REVIEW_NOT_ACCEPTED")
                self.store.record(self.run_id, Event(state=RunState.REVIEWING, code="CANDIDATE_REJECTED"), "CANDIDATE_REJECTED")
                if attempt == 2 or review.verdict == Verdict.REJECT:
                    self.fallback()
                    return
                feedback = review.reason_codes
                self.store.record(self.run_id, Event(state=RunState.REGENERATING, code="ONE_REGENERATION_ALLOWED"))
                self.regenerate(attempt,"REVIEW_RETRY")
        except asyncio.CancelledError:
            try:
                self.fallback()
            except Exception:
                self.store.fail(self.run_id, "PROCESS_STOPPED")
            raise
        except Exception:
            # Storage and unexpected orchestration failures are visible and do not create a false delivery success.
            if self.store.run_snapshot(self.run_id)["state"] not in FINAL_STATES:
                self.store.fail(self.run_id, "PERSISTENCE_OR_RUNTIME_FAILURE")
            raise
        finally:
            if self.scheduler:
                self.scheduler.close()
            for event in self.audit_events:
                self.store.audit(self.run_id,event)
            self.audit_events.clear()

    def fallback(self):
        if self.scheduler:
            plan=self.scheduler.plan
            tasks=(Task(task_id="safe-template",kind="template",reason="FIXED_EMERGENCY_REPLY",safety_checkpoint=True),
                   Task(task_id="template-review",kind="template",reason="EXACT_TEMPLATE_APPROVAL",safety_checkpoint=True),
                   Task(task_id="safe-delivery",kind="delivery",reason="ONLY_TEMPLATE_APPROVAL",safety_checkpoint=True))
            edges=(TaskDependency(task_id="template-review",depends_on="safe-template",allowed_statuses=(TaskStatus.SUCCEEDED,)),
                   TaskDependency(task_id="safe-delivery",depends_on="template-review",allowed_statuses=(TaskStatus.SUCCEEDED,)))
            self.scheduler.update_plan(plan.model_copy(update={"tasks":plan.tasks+tasks,"dependencies":plan.dependencies+edges}))
            self.scheduler.start("safe-template")
        candidate = CandidateOutput(candidate_id=identifier(), text=SAFE_REPLY, content_hash=digest(SAFE_REPLY),
                                    attempt=3, origin="SAFE_TEMPLATE")
        result = AgentResult(agent=AgentName.RESPONSE, status=ResultStatus.DEGRADED,
            input_summary={"static_template": True}, artifact=Artifact(producer=AgentName.RESPONSE, payload=candidate), elapsed_ms=0)
        self.store.record(self.run_id, Event(state=RunState.GENERATING, code="SAFE_TEMPLATE_SELECTED", result=result))
        if self.scheduler:
            self.scheduler.finish("safe-template",TaskStatus.SUCCEEDED)
            self.scheduler.start("template-review")
        approval = template_approval(candidate)
        reviewed = AgentResult(agent=AgentName.REVIEW, status=ResultStatus.SUCCESS,
            input_summary={"candidate_id": candidate.candidate_id, "content_hash": candidate.content_hash},
            artifact=Artifact(producer=AgentName.REVIEW, payload=approval), elapsed_ms=0)
        self.store.record(self.run_id, Event(state=RunState.REVIEWING, code="SAFE_TEMPLATE_VALIDATED", result=reviewed))
        if self.scheduler:
            self.scheduler.finish("template-review",TaskStatus.SUCCEEDED)
            self.scheduler.start("safe-delivery")
        self.store.commit_reply(self.run_id, candidate, approval, RunState.SAFE_FALLBACK)
        if self.scheduler:self.scheduler.finish("safe-delivery",TaskStatus.SUCCEEDED)


class DynamicRunExecution(RunExecution):
    def merge_analysis(self, completed, name):
        output=self.apply_step(completed,RunState.ANALYZING,name)
        if name=="risk" and isinstance(output,RiskOutput):self.risk_checkpoint(output)
        return output

    async def analyze(self,message,history):
        if not self.independent or self.runtime.settings.max_parallel_agents==1:
            return await super().analyze(message,history)
        inputs={"intent":IntentInput(message=message,history=history),"risk":RiskInput(message=message,history=history,intent=None)}
        agents={"intent":(AgentName.INTENT,self.runtime.intent,lambda:IntentOutput(intent=IntentKind.SUPPORT,needs_knowledge=False)),
                "risk":(AgentName.RISK,self.runtime.risk,lambda:combine_risk(message,RiskOutput(level="UNKNOWN",needs_human=True,reason_codes=("ASSESSMENT_UNAVAILABLE",))))}
        pending,outputs={},{}
        try:
            for name,(agent,worker,fallback) in agents.items():
                failure=None
                try:self.scheduler.start(name)
                except AgentFailure as exc:failure=exc.code
                operation=lambda worker=worker,data=inputs[name]:worker.run(data,self.invoke)
                task=asyncio.create_task(self.collect_step(agent,{"message_chars":len(message),"history_messages":len(history),"independent":True},operation,fallback,failure))
                pending[task]=name
            while pending:
                done,_=await asyncio.wait(pending,return_when=asyncio.FIRST_COMPLETED)
                for task in sorted(done,key=lambda item:pending[item]):
                    name=pending.pop(task);completed=task.result()
                    outputs[name]=self.merge_analysis(completed,name)
                    if completed.cancelled:raise asyncio.CancelledError()
            return outputs["intent"],outputs["risk"]
        finally:
            if pending:
                remaining=list(pending)
                for task in remaining:task.cancel()
                results=await asyncio.gather(*remaining,return_exceptions=True)
                for task,result in zip(remaining,results):
                    if isinstance(result,CompletedStep):self.merge_analysis(result,pending[task])
