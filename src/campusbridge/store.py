import json
import time
from functools import wraps

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from campusbridge.contracts import (
    AccessDenied, AgentName, AgentResult, CandidateOutput, CaseState, ContextOutput, DeliveryOutput, Event,
    FINAL_STATES, HistoryItem, Identity, NotFound, ResultStatus, ReviewOutput, RiskOutput,
    RunState, StateConflict, digest, identifier,
)
from campusbridge.db import Account, AgentRun, CaseEvent, Conversation, Message, ModelCall, RiskEvent, RunEvent, SupportCase
from campusbridge.policy import permit_delivery, sanitized_input
from campusbridge.grounding import validate_citations


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def mysql_conflict_retry(operation):
    """Retry only rolled-back InnoDB deadlocks/lock waits, never ambiguous disconnects."""
    @wraps(operation)
    def invoke(*args, **kwargs):
        for attempt in range(3):
            try: return operation(*args, **kwargs)
            except OperationalError as exc:
                if not getattr(exc.orig, "args", ()) or exc.orig.args[0] not in {1205, 1213}: raise
                if attempt == 2: raise StateConflict("DATABASE_WRITE_CONFLICT") from None
                time.sleep(.01 * (2 ** attempt))
    return invoke


class Store:
    def __init__(self, database):
        self.db = database

    def _locked_run(self, session, run_id, *, conversation=False):
        if conversation:
            conversation_id = session.scalar(select(AgentRun.conversation_id).where(AgentRun.id == run_id))
            if conversation_id is None: raise NotFound("RUN_NOT_FOUND")
            self.db.lock(session, Conversation, conversation_id)
        value = self.db.lock(session, AgentRun, run_id)
        if value is None: raise NotFound("RUN_NOT_FOUND")
        return value

    @staticmethod
    def student(actor):
        if actor.role != "student":
            raise AccessDenied("STUDENT_ROLE_REQUIRED")

    @staticmethod
    def counselor(actor):
        if actor.role != "counselor":
            raise AccessDenied("COUNSELOR_ROLE_REQUIRED")

    def conversation(self, session, conversation_id, actor, *, for_update=False):
        value = self.db.lock(session, Conversation, conversation_id) if for_update else session.get(Conversation, conversation_id)
        if value is None or actor.role != "student" or value.student_id != actor.account_id:
            raise NotFound("CONVERSATION_NOT_FOUND")
        owner = session.get(Account, value.student_id)
        if owner is None or owner.campus_id != actor.campus_id: raise NotFound("CONVERSATION_NOT_FOUND")
        return value

    def run(self, session, run_id, actor):
        value = session.get(AgentRun, run_id)
        if value is None:
            raise NotFound("RUN_NOT_FOUND")
        owner = session.get(Account, value.student_id)
        if ((actor.role == "student" and value.student_id != actor.account_id)
                or (actor.role == "counselor" and owner.campus_id != actor.campus_id)):
            raise NotFound("RUN_NOT_FOUND")
        return value

    def create_conversation(self, actor):
        self.student(actor)
        with self.db.write() as session:
            conversation = Conversation(id=identifier(), student_id=actor.account_id)
            session.add(conversation)
            session.flush()
            return {"conversation_id": conversation.id, "created_at": conversation.created_at}

    def conversation_view(self, conversation_id, actor):
        with self.db.sessions() as session:
            conversation = self.conversation(session, conversation_id, actor)
            messages = session.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.order_index)).all()
            for message in messages:
                if message.role == "assistant":
                    run = session.get(AgentRun, message.run_id)
                    if (run is None or run.response_message_id != message.id
                            or run.state not in {RunState.ACCEPTED.value, RunState.SAFE_FALLBACK.value}):
                        raise StateConflict("MESSAGE_APPROVAL_MISSING")
                    candidate = CandidateOutput.model_validate(run.candidate)
                    permit_delivery(candidate, ReviewOutput.model_validate(run.approval))
                    validate_citations(candidate, self.run_references(session, run.id))
                    if message.text != candidate.text:
                        raise StateConflict("MESSAGE_CONTENT_CHANGED")
            return {"conversation_id": conversation.id, "messages": [
                {"message_id": m.id, "run_id": m.run_id, "role": m.role, "text": m.text} for m in messages]}

    def enqueue(self, conversation_id, actor, message, key):
        self.student(actor)
        fingerprint = digest(canonical({"conversation": conversation_id, "message": message}))
        with self.db.write() as session:
            # Lock an existing parent before the first idempotency-key insert.
            # This covers the student's keys across different conversations too.
            owner = self.db.lock(session, Account, actor.account_id)
            if owner is None or owner.campus_id != actor.campus_id: raise NotFound("CONVERSATION_NOT_FOUND")
            self.conversation(session, conversation_id, actor, for_update=True)
            existing = session.scalar(select(AgentRun).where(AgentRun.student_id == actor.account_id, AgentRun.operation_key == key))
            if existing:
                if existing.request_hash != fingerprint:
                    raise StateConflict("OPERATION_KEY_REUSED")
                return existing.id
            run = AgentRun(id=identifier(), conversation_id=conversation_id, student_id=actor.account_id,
                operation_key=key, request_hash=fingerprint, input_text=sanitized_input(message), state=RunState.QUEUED.value)
            session.add(run)
            session.flush()
            return run.id

    def run_snapshot(self, run_id):
        with self.db.sessions() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise NotFound("RUN_NOT_FOUND")
            account = session.get(Account, run.student_id)
            return {"id": run.id, "conversation_id": run.conversation_id, "student_id": run.student_id,
                    "message": run.input_text, "state": RunState(run.state), "campus_id": account.campus_id}

    def begin(self, run_id, history_limit):
        with self.db.write() as session:
            run = self._locked_run(session, run_id, conversation=True)
            if run.state != RunState.QUEUED.value:
                raise StateConflict("RUN_ALREADY_STARTED")
            history = session.scalars(select(Message).where(Message.conversation_id == run.conversation_id)
                .order_by(Message.order_index.desc()).limit(history_limit)).all() if history_limit else []
            order = session.scalar(select(func.coalesce(func.max(Message.order_index), 0)).where(Message.conversation_id == run.conversation_id))
            session.add(Message(id=identifier(), conversation_id=run.conversation_id, run_id=run.id, role="user",
                                text=run.input_text, order_index=order + 1))
            run.state = RunState.ANALYZING.value
            self._event(session, run, Event(state=RunState.ANALYZING, code="TURN_STARTED"))
            return tuple(HistoryItem(role=m.role, text=m.text) for m in reversed(history))

    @staticmethod
    def _event(session, run, event):
        sequence = session.scalar(select(func.coalesce(func.max(RunEvent.sequence), 0)).where(RunEvent.run_id == run.id)) + 1
        session.add(RunEvent(id=event.event_id, run_id=run.id, sequence=sequence, event=event.model_dump(mode="json")))

    def record(self, run_id, event, failure_code=None):
        with self.db.write() as session:
            run = self._locked_run(session, run_id)
            if RunState(run.state) in FINAL_STATES:
                raise StateConflict("RUN_IS_TERMINAL")
            run.state = event.state.value
            if failure_code:
                run.failure_code = failure_code
            artifact = event.result.artifact if event.result else None
            if artifact and isinstance(artifact.payload, CandidateOutput):
                run.candidate = artifact.payload.model_dump(mode="json")
            self._event(session, run, event)

    def audit(self, run_id, event):
        """Coordinator metadata; never changes business RunState, including terminal-state auditing."""
        with self.db.write() as session:
            run = self._locked_run(session, run_id)
            if run is None:
                raise NotFound("RUN_NOT_FOUND")
            self._event(session, run, event)

    def count_call(self, run_id, limit):
        with self.db.write() as session:
            run = self._locked_run(session, run_id)
            if run.model_calls >= limit:
                return False
            run.model_calls += 1
            return True

    def usage(self, run_id, reply):
        with self.db.write() as session:
            run = self._locked_run(session, run_id)
            run.input_tokens += reply.input_tokens or 0
            run.output_tokens += reply.output_tokens or 0

    def start_model_call(self, run_id, task, limit, provider, model):
        with self.db.write() as session:
            run = self._locked_run(session, run_id)
            if run.model_calls >= limit:
                return None
            run.model_calls += 1
            call = ModelCall(id=identifier(), run_id=run_id, call_index=run.model_calls,
                             agent=task.value, provider=provider, model=model, status="STARTED")
            session.add(call)
            return call.id

    def finish_model_call(self, call_id, status, elapsed_ms, error_code=None, reply=None):
        with self.db.write() as session:
            run_id = session.scalar(select(ModelCall.run_id).where(ModelCall.id == call_id))
            run = self._locked_run(session, run_id)
            call = self.db.lock(session, ModelCall, call_id)
            if call.status != "STARTED":
                return
            call.status, call.elapsed_ms, call.error_code = status, elapsed_ms, error_code
            if reply:
                call.input_tokens, call.output_tokens = reply.input_tokens, reply.output_tokens
                call.finish_reason = reply.finish_reason
                run.input_tokens += reply.input_tokens or 0
                run.output_tokens += reply.output_tokens or 0

    def model_output_validity(self, call_id, valid, error_code=None):
        if call_id is None:
            return
        with self.db.write() as session:
            call = self.db.lock(session, ModelCall, call_id)
            if call.status == "SUCCESS":
                call.output_valid, call.error_code = valid, error_code

    def persist_attention(self, run_id, assessment):
        if not assessment.needs_human:
            return None
        with self.db.write() as session:
            run = self._locked_run(session, run_id)
            existing = session.scalar(select(RiskEvent).where(RiskEvent.run_id == run_id))
            if existing:
                return session.scalar(select(SupportCase.id).where(SupportCase.risk_event_id == existing.id))
            account = session.get(Account, run.student_id)
            risk = RiskEvent(id=identifier(), run_id=run_id, student_id=run.student_id, assessment=assessment.model_dump(mode="json"))
            session.add(risk)
            session.flush()
            case = SupportCase(id=identifier(), risk_event_id=risk.id, student_id=run.student_id,
                               campus_id=account.campus_id, state=CaseState.OPEN.value, version=0)
            session.add(case)
            session.flush()
            session.add(CaseEvent(id=identifier(), case_id=case.id, operation_key="system-create", request_hash=digest(run_id),
                actor_id=run.student_id, action="CREATE", from_state="", to_state=CaseState.OPEN.value,
                note="系统根据人工支持需求或风险线索创建关注事项，等待工作人员确认。", result_version=0))
            self._event(session, run, Event(state=RunState.ANALYZING, code="RISK_AND_CASE_COMMITTED"))
            return case.id

    def commit_reply(self, run_id, candidate, approval, final_state):
        preparation_started = time.perf_counter()
        permit_delivery(candidate, approval)
        with self.db.write() as session:
            run = self._locked_run(session, run_id, conversation=True)
            if RunState(run.state) in FINAL_STATES:
                raise StateConflict("RUN_IS_TERMINAL")
            if run.candidate != candidate.model_dump(mode="json"):
                raise StateConflict("CURRENT_CANDIDATE_CHANGED")
            validate_citations(candidate, self.run_references(session, run_id))
            order = session.scalar(select(func.coalesce(func.max(Message.order_index), 0)).where(Message.conversation_id == run.conversation_id))
            message = Message(id=identifier(), conversation_id=run.conversation_id, run_id=run_id, role="assistant",
                              text=candidate.text, order_index=order + 1)
            session.add(message)
            run.approval = approval.model_dump(mode="json")
            run.state = final_state.value
            run.response_message_id = message.id
            if final_state == RunState.ACCEPTED:
                run.failure_code = None
            from campusbridge.contracts import Artifact
            result = AgentResult(agent=AgentName.DELIVERY, status=ResultStatus.SUCCESS,
                input_summary={"candidate_id": candidate.candidate_id, "content_hash": candidate.content_hash},
                artifact=Artifact(producer=AgentName.DELIVERY, payload=DeliveryOutput(candidate_id=candidate.candidate_id,
                    content_hash=candidate.content_hash, message_id=message.id)),
                elapsed_ms=(time.perf_counter() - preparation_started) * 1000)
            self._event(session, run, Event(state=final_state, code="REPLY_COMMITTED", result=result))

    def deliverable(self, run_id, actor):
        with self.db.sessions() as session:
            run = self.run(session, run_id, actor)
            if run.state not in {RunState.ACCEPTED.value, RunState.SAFE_FALLBACK.value} or not run.response_message_id:
                raise StateConflict("REPLY_NOT_AVAILABLE")
            candidate = CandidateOutput.model_validate(run.candidate)
            approval = ReviewOutput.model_validate(run.approval)
            permit_delivery(candidate, approval)
            validate_citations(candidate, self.run_references(session, run_id))
            message = session.get(Message, run.response_message_id)
            if message is None or message.text != candidate.text or message.run_id != run.id:
                raise StateConflict("COMMITTED_REPLY_CHANGED")
            return {"run_id": run.id, "conversation_id": run.conversation_id, "state": run.state,
                    "message_id": message.id, "candidate_id": candidate.candidate_id,
                    "content_hash": candidate.content_hash, "reply": message.text,
                    "citations": [c.model_dump(mode="json") for c in candidate.citations]}

    @staticmethod
    def run_references(session, run_id):
        """Use this run's durable Context artifact, never conversation history or global sources."""
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence.desc()))
        for row in events:
            result = row.event.get("result") or {}
            artifact = result.get("artifact") or {}
            payload = artifact.get("payload") or {}
            if result.get("agent") == AgentName.CONTEXT.value and payload.get("kind") == "context":
                return ContextOutput.model_validate(payload).knowledge
        return ()

    def trace(self, run_id, actor):
        with self.db.sessions() as session:
            run = self.run(session, run_id, actor)
            rows = session.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)).all()
            events = []
            for row in rows:
                data = Event.model_validate(row.event).model_dump(mode="json")
                if actor.role == "student" and data["result"]:
                    artifact = data["result"].get("artifact")
                    if artifact:
                        payload = artifact["payload"]
                        artifact["payload"] = {key: payload[key] for key in ("kind", "candidate_id", "content_hash", "message_id") if key in payload}
                if actor.role == "student":
                    data["plan"] = data["arbitration"] = data["task_event"] = None
                events.append({"sequence": row.sequence, "created_at": row.created_at, **data})
            calls = session.scalars(select(ModelCall).where(ModelCall.run_id == run_id).order_by(ModelCall.call_index)).all()
            call_data = [{key: getattr(call, key) for key in ("call_index", "agent", "provider", "model", "status",
                "elapsed_ms", "input_tokens", "output_tokens", "error_code", "output_valid", "finish_reason")} for call in calls]
            for data, call in zip(call_data, calls):
                data["call_id"] = call.id
            return {"run_id": run.id, "state": run.state, "model_calls": run.model_calls, "calls": call_data,
                    "usage_reporting_calls": sum(c.input_tokens is not None or c.output_tokens is not None for c in calls),
                    "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
                    "failure_code": run.failure_code, "events": events}

    def authorized_run_state(self, run_id, actor):
        with self.db.sessions() as session:
            return RunState(self.run(session, run_id, actor).state)

    def fail(self, run_id, code):
        self.record(run_id, Event(state=RunState.FAILED, code=code), failure_code=code)

    def recover_interrupted(self):
        # Single-process MVP: startup recovery never launches a new model request.
        with self.db.write() as session:
            runs = session.scalars(select(AgentRun).where(AgentRun.state.not_in([state.value for state in FINAL_STATES])).order_by(AgentRun.id).with_for_update()).all()
            for run in runs:
                run.state = RunState.FAILED.value
                run.failure_code = "SERVER_RESTARTED"
                self._event(session, run, Event(state=RunState.FAILED, code="SERVER_RESTARTED"))
            for call in session.scalars(select(ModelCall).where(ModelCall.status == "STARTED")):
                call.status, call.error_code = "INTERRUPTED", "SERVER_RESTARTED"

    def _case(self, session, case_id, actor, *, for_update=False):
        case = self.db.lock(session, SupportCase, case_id) if for_update else session.get(SupportCase, case_id)
        if case is None or case.campus_id != actor.campus_id:
            raise NotFound("CASE_NOT_FOUND")
        return case

    @staticmethod
    def _case_data(case):
        return {"case_id": case.id, "risk_event_id": case.risk_event_id, "student_id": case.student_id,
                "state": case.state, "owner_id": case.owner_id, "version": case.version}

    def cases(self, actor):
        self.counselor(actor)
        with self.db.sessions() as session:
            return [self._case_data(case) for case in session.scalars(select(SupportCase)
                    .where(SupportCase.campus_id == actor.campus_id).order_by(SupportCase.created_at))]

    def case_detail(self, case_id, actor):
        self.counselor(actor)
        with self.db.sessions() as session:
            case = self._case(session, case_id, actor)
            risk = session.get(RiskEvent, case.risk_event_id)
            events = session.scalars(select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.result_version)).all()
            return {**self._case_data(case), "risk": risk.assessment, "run_id": risk.run_id,
                    "events": [{"actor_id": e.actor_id, "action": e.action, "from_state": e.from_state,
                                "to_state": e.to_state, "note": e.note, "version": e.result_version,
                                "created_at": e.created_at} for e in events]}

    def case_action(self, case_id, actor, action, note, expected_version, key):
        self.counselor(actor)
        note = sanitized_input(note.strip())
        fingerprint = digest(canonical({"action": action, "note": note, "expected_version": expected_version}))
        with self.db.write() as session:
            case = self._case(session, case_id, actor, for_update=True)
            previous = session.scalar(select(CaseEvent).where(CaseEvent.case_id == case_id, CaseEvent.operation_key == key))
            if previous:
                if previous.actor_id != actor.account_id or previous.request_hash != fingerprint:
                    raise StateConflict("OPERATION_KEY_REUSED")
                return {"case_id": case.id, "state": previous.to_state, "version": previous.result_version,
                        "actor_id": previous.actor_id, "idempotent_replay": True}
            if case.version != expected_version:
                raise StateConflict("STALE_CASE_VERSION")
            old = CaseState(case.state)
            if action == "claim":
                if old != CaseState.OPEN:
                    raise StateConflict("CASE_NOT_OPEN")
                target = CaseState.CLAIMED
                case.owner_id = actor.account_id
            else:
                if case.owner_id != actor.account_id:
                    raise AccessDenied("CASE_OWNER_REQUIRED")
                if action == "process" and old in {CaseState.CLAIMED, CaseState.IN_PROGRESS}:
                    target = CaseState.IN_PROGRESS
                elif action == "close" and old == CaseState.IN_PROGRESS:
                    target = CaseState.CLOSED
                else:
                    raise StateConflict("INVALID_CASE_TRANSITION")
                if len(note) < 3:
                    raise StateConflict("HANDLING_NOTE_REQUIRED")
            case.state, case.version = target.value, case.version + 1
            session.add(CaseEvent(id=identifier(), case_id=case.id, operation_key=key, request_hash=fingerprint,
                actor_id=actor.account_id, action=action.upper(), from_state=old.value, to_state=target.value,
                note=note, result_version=case.version))
            return {"case_id": case.id, "state": target.value, "version": case.version,
                    "actor_id": actor.account_id, "idempotent_replay": False}


# Each method contains one short database transaction and no model/network side effect.
for _operation in ("create_conversation", "enqueue", "begin", "record", "audit", "count_call", "usage",
                   "start_model_call", "finish_model_call", "model_output_validity", "persist_attention",
                   "commit_reply", "recover_interrupted", "case_action"):
    setattr(Store, _operation, mysql_conflict_retry(getattr(Store, _operation)))
