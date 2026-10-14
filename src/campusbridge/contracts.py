from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated, Literal, Union
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


def identifier():
    return uuid4().hex


def digest(value: str):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DataContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class AgentName(str, Enum):
    INTENT = "IntentAgent"
    RISK = "RiskAgent"
    CONTEXT = "ContextAgent"
    RESPONSE = "ResponseAgent"
    REVIEW = "OutputReviewer"
    DELIVERY = "Delivery"


class RunState(str, Enum):
    QUEUED = "QUEUED"
    ANALYZING = "ANALYZING"
    CONTEXT_READY = "CONTEXT_READY"
    GENERATING = "GENERATING"
    REVIEWING = "REVIEWING"
    REGENERATING = "REGENERATING"
    ACCEPTED = "ACCEPTED"
    SAFE_FALLBACK = "SAFE_FALLBACK"
    FAILED = "FAILED"


FINAL_STATES = {RunState.ACCEPTED, RunState.SAFE_FALLBACK, RunState.FAILED}


class ResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class IntentKind(str, Enum):
    GENERAL = "GENERAL"
    SUPPORT = "SUPPORT"
    CAMPUS_SERVICE = "CAMPUS_SERVICE"
    URGENT = "URGENT"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class Route(str, Enum):
    CONVERSATION = "CONVERSATION"
    KNOWLEDGE_SUPPORT = "KNOWLEDGE_SUPPORT"
    HUMAN_SUPPORT = "HUMAN_SUPPORT"


class Verdict(str, Enum):
    PASS = "PASS"
    RETRY = "RETRY"
    REJECT = "REJECT"


class CaseState(str, Enum):
    OPEN = "OPEN"
    CLAIMED = "CLAIMED"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"


class Identity(DataContract):
    account_id: str
    role: Literal["student", "counselor"]
    campus_id: str


class HistoryItem(DataContract):
    role: Literal["user", "assistant"]
    text: str


class IntentInput(DataContract):
    message: str
    history: tuple[HistoryItem, ...]


class IntentOutput(DataContract):
    kind: Literal["intent"] = "intent"
    intent: IntentKind
    needs_knowledge: StrictBool
    needs_human_support: StrictBool = False


class RiskInput(DataContract):
    message: str
    history: tuple[HistoryItem, ...]
    intent: IntentOutput | None = None


class RiskOutput(DataContract):
    kind: Literal["risk"] = "risk"
    level: RiskLevel
    needs_human_support: StrictBool = Field(validation_alias=AliasChoices("needs_human_support", "needs_human"))
    reason_codes: tuple[Literal["EXPLICIT_DANGER", "MODEL_CONCERN", "ASSESSMENT_UNAVAILABLE", "NO_SIGNAL", "HUMAN_REQUEST"], ...]

    @property
    def needs_human(self):
        """Read compatibility for existing store integrations and archived assessments."""
        return self.needs_human_support


class KnowledgeItem(DataContract):
    source_id: str
    title: str
    text: str
    revision: str
    document_id: str | None = None
    source_hash: str | None = None
    chunk_index: int | None = None
    start: int | None = None
    end: int | None = None
    score: float | None = None


class ContextInput(DataContract):
    message: str
    history: tuple[HistoryItem, ...]
    intent: IntentOutput
    risk: RiskOutput
    campus_id: str


class ContextOutput(DataContract):
    kind: Literal["context"] = "context"
    route: Route
    history: tuple[HistoryItem, ...]
    knowledge: tuple[KnowledgeItem, ...]
    degraded: bool = False
    retrieval_status: Literal["SKIPPED", "HIT", "NO_HIT", "ERROR"] = "SKIPPED"
    query_hash: str | None = None


class ResponseInput(DataContract):
    message: str
    context: ContextOutput
    risk: RiskOutput
    attempt: Annotated[StrictInt, Field(ge=1, le=2)]
    feedback_codes: tuple[str, ...] = ()


class Citation(DataContract):
    source_id: str
    document_id: str | None = None
    title: str
    revision: str
    quote: str
    source_hash: str


def grounding_digest(citations, retrieval_status):
    if not citations and retrieval_status == "SKIPPED":
        return None  # MVP artifacts remain readable with their original text hashes.
    return digest(json.dumps({"citations": [c.model_dump(mode="json") for c in citations],
        "retrieval_status": retrieval_status}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class CandidateOutput(DataContract):
    kind: Literal["candidate"] = "candidate"
    candidate_id: str
    text: Annotated[str, Field(min_length=1, max_length=5000)]
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    attempt: Annotated[StrictInt, Field(ge=1, le=3)]
    origin: Literal["MODEL", "SAFE_TEMPLATE"]
    citations: tuple[Citation, ...] = ()
    retrieval_status: Literal["SKIPPED", "HIT", "NO_HIT", "ERROR"] = "SKIPPED"
    citations_hash: str | None = None

    @model_validator(mode="after")
    def grounding_identity(self):
        if self.citations_hash != grounding_digest(self.citations, self.retrieval_status):
            raise ValueError("Citation metadata identity mismatch")
        return self


class ReviewInput(DataContract):
    candidate: CandidateOutput
    risk: RiskOutput
    references: tuple[KnowledgeItem, ...] = ()


class ReviewOutput(DataContract):
    kind: Literal["review"] = "review"
    candidate_id: str
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    verdict: Verdict
    reason_codes: tuple[Literal["UNSAFE_INSTRUCTION", "DIAGNOSIS", "PRIVACY_DISCLOSURE", "INTERNAL_LABEL",
        "SAFETY_GUIDANCE_MISSING", "MOCK_REJECTION", "APPROVED_TEMPLATE", "UNSUPPORTED_CLAIM"], ...] = ()
    authority: Literal["MODEL", "RULE", "TEMPLATE"] = "MODEL"
    citations_hash: str | None = None


class DeliveryOutput(DataContract):
    kind: Literal["delivery"] = "delivery"
    candidate_id: str
    content_hash: str
    message_id: str


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class Task(DataContract):
    task_id: str
    kind: Literal["agent", "arbitration", "attention", "delivery", "template"]
    agent: AgentName | None = None
    status: TaskStatus = TaskStatus.PENDING
    enabled: bool = True
    reason: str
    safety_checkpoint: bool = False
    started_ms: float | None = None
    finished_ms: float | None = None
    failure_code: str | None = None


class TaskDependency(DataContract):
    task_id: str
    depends_on: str
    allowed_statuses: tuple[TaskStatus, ...] = (TaskStatus.SUCCEEDED, TaskStatus.DEGRADED)


class ExecutionPlan(DataContract):
    plan_id: str = Field(default_factory=identifier)
    version: str = "campusbridge-task-plan-v1"
    orchestration: Literal["sequential", "dynamic"]
    independent_analysis: bool
    tasks: tuple[Task, ...]
    dependencies: tuple[TaskDependency, ...] = ()
    max_task_executions: int
    max_model_calls: int
    max_token_reservation: int

    @model_validator(mode="after")
    def valid_graph(self):
        names = {t.task_id for t in self.tasks}
        if len(names) != len(self.tasks) or len(names) > 32:
            raise ValueError("Invalid task identities or unbounded plan")
        parents = {name: [] for name in names}
        for edge in self.dependencies:
            if edge.task_id not in names or edge.depends_on not in names:
                raise ValueError("Unknown task dependency")
            parents[edge.task_id].append(edge.depends_on)
        visited, active = set(), set()
        def visit(name):
            if name in active:
                raise ValueError("Cyclic execution plan")
            if name in visited:
                return
            active.add(name)
            for parent in parents[name]:
                visit(parent)
            active.remove(name); visited.add(name)
        for name in names:
            visit(name)
        return self


class DecisionEvidence(DataContract):
    origin: Literal["rule", "agent", "knowledge"]
    source_id: str
    content_hash: str
    reason: str


class ArbitrationInput(DataContract):
    message: str
    history: tuple[HistoryItem, ...]
    intent: IntentOutput
    risk: RiskOutput
    failure_codes: tuple[str, ...] = ()
    context: ContextOutput | None = None


class ArbitrationDecision(DataContract):
    stage: Literal["analysis", "evidence"]
    final_route: Route
    risk: RiskOutput
    strategy: str
    conflicts: tuple[str, ...] = ()
    reasons: tuple[str, ...]
    adopted_evidence: tuple[DecisionEvidence, ...]
    rejected_source_ids: tuple[str, ...] = ()
    context: ContextOutput | None = None
    response_message: str


class TaskEvent(DataContract):
    task: Task
    dependencies: tuple[TaskDependency, ...] = ()
    task_executions: int
    token_reservation: int = 0


Payload = Annotated[Union[IntentOutput, RiskOutput, ContextOutput, CandidateOutput, ReviewOutput, DeliveryOutput],
                    Field(discriminator="kind")]


class Artifact(DataContract):
    artifact_id: str = Field(default_factory=identifier)
    producer: AgentName
    payload: Payload


class AgentResult(DataContract):
    agent: AgentName
    status: ResultStatus
    input_summary: dict[str, str | int | bool | list[str]]
    artifact: Artifact | None = None
    elapsed_ms: float = Field(ge=0)
    failure_code: str | None = None


class Event(DataContract):
    event_id: str = Field(default_factory=identifier)
    state: RunState
    code: str
    result: AgentResult | None = None
    plan: ExecutionPlan | None = None
    task_event: TaskEvent | None = None
    arbitration: ArbitrationDecision | None = None


class ChatMessage(DataContract):
    role: Literal["system", "user"]
    content: str


class ModelRequest(DataContract):
    task: AgentName
    messages: tuple[ChatMessage, ...]
    max_output_tokens: Annotated[StrictInt, Field(ge=1, le=2048)] = 1024
    temperature: float = Field(default=0, ge=0, le=1)
    trace_call_id: str | None = Field(default=None, exclude=True)


class ModelReply(DataContract):
    content: str | dict
    input_tokens: Annotated[StrictInt, Field(ge=0)] | None = None
    output_tokens: Annotated[StrictInt, Field(ge=0)] | None = None
    finish_reason: str | None = None
    request_id: str | None = None


class AccessDenied(RuntimeError):
    pass


class StateConflict(RuntimeError):
    pass


class NotFound(RuntimeError):
    pass


class AgentFailure(RuntimeError):
    def __init__(self, code, usage=None):
        self.code = code
        self.usage = usage
        super().__init__(code)
