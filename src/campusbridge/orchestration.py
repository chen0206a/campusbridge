"""Deterministic task planning and arbitration. These components make no model calls."""
import json
import re
import time

from campusbridge.contracts import (AgentFailure, AgentName, ArbitrationDecision, DecisionEvidence,
    Event, ExecutionPlan, RiskLevel, Route, RunState, Task, TaskDependency, TaskEvent, TaskStatus, digest)
from campusbridge.policy import combine_risk, explicit_danger, explicit_human_request
from campusbridge.retrieval import terms

COMPLETED = (TaskStatus.SUCCEEDED, TaskStatus.DEGRADED, TaskStatus.SKIPPED)
ANALYSIS_READY = COMPLETED + (TaskStatus.BLOCKED, TaskStatus.FAILED)


def independent_analysis(message, history):
    # The same input contract is used in both modes. Referent-dependent requests retain Intent -> Risk.
    return not (history and any(word in message for word in ("那", "刚才", "上述", "这个", "它", "继续", "之前")))


def retrieval_query(message, history):
    if history and any(word in message for word in ("那", "刚才", "上述", "这个", "它")):
        previous = [item.text for item in history if item.role == "user"]
        if previous:
            return previous[-1][:500] + " " + message
    return message


def meaningful_terms(message):
    text = re.sub(r"\[source:[^\]]*\]", "", message)
    for generic in ("校园服务", "校园", "有没有", "申请规定", "申请", "规定", "资料", "相关", "指南",
                    "需要", "准备", "哪些", "什么", "如何", "请问", "是否", "流程", "信息", "工作人员"):
        text = text.replace(generic, "")
    return set(terms(text))


class Arbitrator:
    def decide(self, data, *, dynamic=True):
        risk = combine_risk(data.message, data.risk, data.intent)
        conflicts, reasons = [], []
        if risk.level == RiskLevel.HIGH:
            reasons.append("HIGH_RISK_PRIORITY")
            if data.intent.intent.value not in {"URGENT"}:
                conflicts.append("INTENT_RISK_DISAGREEMENT")
            if data.intent.needs_knowledge:
                conflicts.append("KNOWLEDGE_REQUEST_SUPERSEDED_BY_HIGH_RISK")
        if risk.level == RiskLevel.LOW and risk.needs_human_support:
            conflicts.append("LOW_SEVERITY_WITH_HUMAN_SUPPORT_REQUEST")
            reasons.append("SUPPORT_NEED_IS_INDEPENDENT_OF_SEVERITY")
        if dynamic and data.intent.intent.value == "URGENT" and not risk.needs_human_support:
            risk = risk.model_copy(update={"needs_human_support": True})
            conflicts.append("INTENT_URGENT_RISK_LOW")
            reasons.append("STAFF_CONFIRMATION_FOR_DISAGREEMENT")
        if data.failure_codes:
            conflicts += ["AGENT_PARTIAL_FAILURE"]
            reasons += list(data.failure_codes)
        route = Route.HUMAN_SUPPORT if risk.needs_human_support else (
            Route.KNOWLEDGE_SUPPORT if data.intent.needs_knowledge else Route.CONVERSATION)
        reasons.append("HUMAN_SUPPORT_REQUIRED" if risk.needs_human_support else "KNOWLEDGE_REQUESTED" if data.intent.needs_knowledge else "ORDINARY_CONVERSATION")
        evidence = [DecisionEvidence(origin="agent", source_id=name, content_hash=digest(json.dumps(item.model_dump(mode="json"), sort_keys=True)),
            reason="TYPED_AGENT_RESULT") for name,item in (("IntentAgent", data.intent), ("RiskAgent", data.risk))]
        if explicit_danger(data.message) or explicit_human_request(data.message):
            evidence.append(DecisionEvidence(origin="rule", source_id="server-support-policy-v1", content_hash=digest(data.message), reason="CURRENT_REQUEST_POLICY"))
        response_message = data.message
        context, rejected = data.context, []
        if dynamic and re.search(r"\[source:[^\]]*\]", response_message):
            response_message = re.sub(r"\[source:[^\]]*\]", "（用户提及的来源标识）", response_message)
            reasons.append("UNTRUSTED_SOURCE_SYNTAX_IS_DATA")
        if context is not None and dynamic and context.retrieval_status == "HIT":
            relevant = meaningful_terms(retrieval_query(data.message, data.history))
            valid, seen = [], set()
            duplicates = {item.source_id for item in context.knowledge if sum(k.source_id == item.source_id for k in context.knowledge) > 1}
            for item in context.knowledge:
                valid_hash = bool(item.text.strip()) and (not item.source_hash or item.source_hash == digest(item.text))
                overlap = relevant & set(terms(item.title + " " + item.text))
                if valid_hash and item.source_id not in duplicates and overlap and item.source_id not in seen:
                    valid.append(item); seen.add(item.source_id)
                    evidence.append(DecisionEvidence(origin="knowledge", source_id=item.source_id, content_hash=digest(item.text), reason="VALID_IDENTITY_AND_LEXICAL_RELEVANCE"))
                else:
                    rejected.append(item.source_id)
            if rejected or not valid:
                conflicts.append("CONTEXT_EVIDENCE_REJECTED")
                reasons.append("ONLY_CURRENT_VALID_RELEVANT_EVIDENCE")
                context = context.model_copy(update={"knowledge": tuple(valid), "retrieval_status": "HIT" if valid else "NO_HIT", "degraded": True})
        if context is not None:
            context = context.model_copy(update={"route": route})
        return ArbitrationDecision(stage="evidence" if data.context is not None else "analysis", final_route=route,
            risk=risk, strategy="FAIL_CLOSED" if risk.level == RiskLevel.UNKNOWN else "HIGH_RISK_FIRST" if risk.level == RiskLevel.HIGH else
                "HUMAN_REQUEST" if risk.needs_human_support else "NO_RELEVANT_EVIDENCE" if context is not None and context.retrieval_status in {"NO_HIT","ERROR"} else "EVIDENCE_GROUNDED" if route==Route.KNOWLEDGE_SUPPORT else "DIRECT_SUPPORT",
            conflicts=tuple(dict.fromkeys(conflicts)), reasons=tuple(dict.fromkeys(reasons)), adopted_evidence=tuple(evidence),
            rejected_source_ids=tuple(dict.fromkeys(rejected)), context=context, response_message=response_message)


class TaskPlanner:
    def bootstrap(self, settings, independent, early_attention=False):
        dependencies = () if independent and settings.orchestration == "dynamic" and settings.max_parallel_agents == 2 else (
            TaskDependency(task_id="risk", depends_on="intent", allowed_statuses=ANALYSIS_READY),)
        return ExecutionPlan(orchestration=settings.orchestration, independent_analysis=independent,
            tasks=(Task(task_id="early-attention", kind="attention", enabled=early_attention, reason="EXPLICIT_DANGER_BEFORE_MODELS" if early_attention else "NO_EXPLICIT_DANGER", safety_checkpoint=True),
                   Task(task_id="intent", kind="agent", agent=AgentName.INTENT, reason="CLASSIFY_CURRENT_REQUEST"),
                   Task(task_id="risk", kind="agent", agent=AgentName.RISK, reason="INDEPENDENT_ASSESSMENT" if independent else "INTENT_DEPENDENT_ASSESSMENT"),
                   Task(task_id="risk-checkpoint", kind="attention", enabled=False, reason="WAIT_FOR_TYPED_RISK_RESULT", safety_checkpoint=True),
                   Task(task_id="arbitrate", kind="arbitration", reason="SERVER_POLICY_AND_RESULT_MERGE", safety_checkpoint=True)),
            dependencies=dependencies + (TaskDependency(task_id="risk-checkpoint",depends_on="risk",allowed_statuses=ANALYSIS_READY),
                TaskDependency(task_id="arbitrate",depends_on="risk-checkpoint",allowed_statuses=COMPLETED)) + tuple(TaskDependency(task_id="arbitrate", depends_on=name, allowed_statuses=ANALYSIS_READY) for name in ("intent", "risk")),
            max_task_executions=settings.max_task_executions, max_model_calls=settings.max_model_calls,
            max_token_reservation=settings.max_turn_token_reservation)

    def expand(self, plan, decision):
        knowledge = decision.final_route == Route.KNOWLEDGE_SUPPORT
        context_enabled = plan.orchestration == "sequential" or knowledge
        tasks = (Task(task_id="attention", kind="attention", reason="HUMAN_SUPPORT_REQUIRED" if decision.risk.needs_human_support else "NO_HUMAN_SUPPORT_NEEDED",
                      enabled=decision.risk.needs_human_support, safety_checkpoint=True),
                 Task(task_id="context", kind="agent", agent=AgentName.CONTEXT, enabled=context_enabled,
                      reason="RAG_REQUESTED" if knowledge else "SEQUENTIAL_CONTEXT" if context_enabled else "HUMAN_PRIORITY" if decision.risk.needs_human_support else "HISTORY_ALREADY_AVAILABLE"),
                 Task(task_id="evidence", kind="arbitration", reason="VERIFY_CONTEXT_EVIDENCE", safety_checkpoint=True))
        edges = (TaskDependency(task_id="attention", depends_on="arbitrate"),
                 TaskDependency(task_id="context", depends_on="attention", allowed_statuses=COMPLETED),
                 TaskDependency(task_id="evidence", depends_on="context", allowed_statuses=COMPLETED+(TaskStatus.BLOCKED,)))
        return self.add_attempt(plan.model_copy(update={"tasks":plan.tasks+tasks,"dependencies":plan.dependencies+edges}), 1)

    def add_attempt(self, plan, attempt):
        tasks=(Task(task_id=f"response-{attempt}",kind="agent",agent=AgentName.RESPONSE,reason="GENERATE_REVIEWABLE_CANDIDATE"),
               Task(task_id=f"review-{attempt}",kind="agent",agent=AgentName.REVIEW,reason="REVIEW_ACTUAL_CANDIDATE", safety_checkpoint=False),
               Task(task_id=f"delivery-{attempt}",kind="delivery",agent=AgentName.DELIVERY,reason="ONLY_MATCHED_PASS_APPROVAL",safety_checkpoint=True))
        edges=(TaskDependency(task_id=f"response-{attempt}",depends_on="evidence"),
               TaskDependency(task_id=f"review-{attempt}",depends_on=f"response-{attempt}",allowed_statuses=(TaskStatus.SUCCEEDED,)),
               TaskDependency(task_id=f"delivery-{attempt}",depends_on=f"review-{attempt}",allowed_statuses=(TaskStatus.SUCCEEDED,)))
        if attempt > 1:
            resumable = ANALYSIS_READY
            edges += tuple(TaskDependency(task_id=f"response-{attempt}", depends_on=f"{kind}-{attempt-1}", allowed_statuses=resumable)
                           for kind in ("response", "review"))
        return ExecutionPlan.model_validate(plan.model_copy(update={"tasks":plan.tasks+tasks,"dependencies":plan.dependencies+edges}))


class TaskScheduler:
    """Only the coordinator mutates plans. Worker results never mutate this scheduler."""
    def __init__(self, plan, emit, started):
        self.plan,self.emit,self.started=plan,emit,started
        self.executions=0
        self.token_reservation=0
        self.emit(Event(state=RunState.ANALYZING,code="PLAN_CREATED",plan=plan))

    def task(self, task_id):
        return next(t for t in self.plan.tasks if t.task_id == task_id)

    def update_plan(self, plan):
        self.plan=ExecutionPlan.model_validate(plan)
        self.emit(Event(state=RunState.ANALYZING,code="PLAN_UPDATED",plan=self.plan))

    def update(self, task):
        self.plan=self.plan.model_copy(update={"tasks":tuple(task if t.task_id==task.task_id else t for t in self.plan.tasks)})

    def trace(self, task, code):
        self.emit(Event(state=RunState.ANALYZING,code=code,task_event=TaskEvent(task=task,
            dependencies=tuple(d for d in self.plan.dependencies if d.task_id==task.task_id),
            task_executions=self.executions,token_reservation=self.token_reservation)))

    def start(self, task_id):
        task=self.task(task_id)
        if task.status != TaskStatus.PENDING:
            raise AgentFailure("TASK_ALREADY_TERMINAL_OR_RUNNING")
        if not task.enabled:
            self.skip(task_id,task.reason);return False
        for edge in self.plan.dependencies:
            if edge.task_id==task_id and self.task(edge.depends_on).status not in edge.allowed_statuses:
                self.block(task_id,"DEPENDENCY_BLOCKED");raise AgentFailure("DEPENDENCY_BLOCKED")
        if not task.safety_checkpoint and self.executions>=self.plan.max_task_executions:
            self.block(task_id,"TASK_EXECUTION_BUDGET");raise AgentFailure("TASK_EXECUTION_BUDGET")
        if not task.safety_checkpoint:self.executions+=1
        task=task.model_copy(update={"status":TaskStatus.RUNNING,"started_ms":(time.monotonic()-self.started)*1000})
        self.update(task);self.trace(task,"TASK_STARTED");return True

    def finish(self,task_id,status,failure=None):
        task=self.task(task_id)
        if task.status != TaskStatus.RUNNING:return
        task=task.model_copy(update={"status":status,"finished_ms":(time.monotonic()-self.started)*1000,"failure_code":failure})
        self.update(task);self.trace(task,"TASK_FINISHED")

    def block(self,task_id,reason):
        task=self.task(task_id).model_copy(update={"status":TaskStatus.BLOCKED,"failure_code":reason,"reason":reason,
                                                 "finished_ms":(time.monotonic()-self.started)*1000})
        self.update(task);self.trace(task,"TASK_BLOCKED")

    def skip(self,task_id,reason):
        task=self.task(task_id).model_copy(update={"status":TaskStatus.SKIPPED,"reason":reason,"finished_ms":(time.monotonic()-self.started)*1000})
        self.update(task);self.trace(task,"TASK_SKIPPED")

    def close(self):
        for task in self.plan.tasks:
            if task.status==TaskStatus.PENDING:self.skip(task.task_id,"RUN_ENDED_BEFORE_TASK")
            elif task.status==TaskStatus.RUNNING:self.finish(task.task_id,TaskStatus.CANCELLED,"PROCESS_STOPPED")
        self.emit(Event(state=RunState.ANALYZING,code="PLAN_TERMINAL",plan=self.plan))
