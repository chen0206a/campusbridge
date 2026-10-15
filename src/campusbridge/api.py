import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from campusbridge.contracts import AccessDenied, FINAL_STATES, NotFound, RunState, StateConflict
from campusbridge.db import Database, KnowledgeDatabase
from campusbridge.knowledge import DemoKnowledge
from campusbridge.models import MockModel
from campusbridge.provider import OpenAICompatibleProvider
from campusbridge.retrieval import SQLiteKnowledge, bundled_documents
from campusbridge.dense_retrieval import configured_knowledge
from campusbridge.runtime import CampusRuntime
from campusbridge.security import Authentication
from campusbridge.settings import Settings
from campusbridge.store import Store
from campusbridge.redis_support import RateLimitError, RedisSupport
from campusbridge.retrieval_cache import cached_knowledge
from campusbridge.transport import require_transport, rate_peer


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(InputModel):
    username: Annotated[str, Field(min_length=1, max_length=80, strict=True)]
    password: Annotated[str, Field(min_length=1, max_length=128, strict=True)]


class MessageInput(InputModel):
    message: Annotated[str, Field(min_length=1, max_length=3000, strict=True)]


class CaseActionInput(InputModel):
    expected_version: Annotated[StrictInt, Field(ge=0)]
    note: Annotated[str, Field(max_length=2000, strict=True)] = ""


def stream_event(name, value):
    return "event: " + name + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n"


def create_app(settings=None, model=None, knowledge=None):
    settings = settings or Settings.from_environment()
    database = Database.business(settings)
    authentication = Authentication(database, settings)
    if settings.seed_demo:
        authentication.seed()
    store = Store(database)
    if model is None:
        model = OpenAICompatibleProvider(settings) if settings.model_provider == "openai-compatible" else MockModel()
    knowledge_database = None
    if knowledge is None:
        if settings.knowledge_backend == "fts5":
            knowledge_database = KnowledgeDatabase(settings.knowledge_file)
        knowledge = configured_knowledge(knowledge_database, settings) if settings.knowledge_backend == "fts5" else DemoKnowledge()
        if settings.knowledge_backend == "fts5":
            knowledge.import_documents(bundled_documents())
    redis_support = RedisSupport(settings)
    knowledge = cached_knowledge(knowledge, redis_support)
    runtime = CampusRuntime(store, model, knowledge, settings)

    @asynccontextmanager
    async def lifetime(app):
        try:
            database.acquire_runtime_owner(enforce=True)
            store.recover_interrupted()
            yield
        finally:
            await runtime.shutdown()
            redis_support.close()
            if knowledge_database is not None: knowledge_database.close()
            database.close()

    app = FastAPI(title="CampusBridge", version="0.1.0", lifespan=lifetime,
                  description="校园学生支持：多 Agent 协作、实际回复审核和人工工单。默认离线 MockModel。")
    app.state.database, app.state.store, app.state.runtime, app.state.authentication = database, store, runtime, authentication
    app.state.knowledge = knowledge
    app.state.knowledge_database = knowledge_database
    app.state.redis_support = redis_support
    bearer = HTTPBearer(auto_error=False)

    def local_request(request: Request):
        require_transport(request, settings)

    def actor(request: Request, credential: HTTPAuthorizationCredentials | None = Depends(bearer)):
        local_request(request)
        if credential is None:
            raise HTTPException(401, "AUTHENTICATION_REQUIRED")
        try:
            return authentication.authenticate(credential.credentials)
        except AccessDenied:
            raise HTTPException(401, "INVALID_TOKEN") from None

    def student(identity=Depends(actor)):
        store.student(identity)
        return identity

    @app.exception_handler(AccessDenied)
    async def denied(request, exc):
        return JSONResponse({"detail": "PERMISSION_DENIED"}, status_code=403)

    @app.exception_handler(RateLimitError)
    async def limited(request, exc):
        return JSONResponse({"detail": exc.code}, status_code=exc.status,
                            headers={"Retry-After": str(exc.retry_after), "Cache-Control": "no-store"})

    @app.exception_handler(NotFound)
    async def missing(request, exc):
        return JSONResponse({"detail": "RESOURCE_NOT_FOUND"}, status_code=404)

    @app.exception_handler(StateConflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # No submitted passwords or arbitrary message text in validation responses.
        return JSONResponse({"detail": "INVALID_REQUEST", "fields": [list(e["loc"]) for e in exc.errors()]}, status_code=422)

    @app.exception_handler(Exception)
    async def unavailable(request, exc):
        return JSONResponse({"detail": "REQUEST_UNAVAILABLE"}, status_code=503)

    @app.get("/health")
    def health():
        return {"status": "ok", "project": "CampusBridge", "model_provider": getattr(model, "provider_name", "custom"),
                "knowledge_backend": settings.knowledge_backend}

    @app.get("/api/knowledge/sources/{source_id}")
    def knowledge_source(source_id: str, identity=Depends(actor)):
        if not hasattr(knowledge, "source"):
            raise NotFound("SOURCE_NOT_FOUND")
        return knowledge.source(source_id, identity)

    @app.post("/api/auth/login")
    def login(body: LoginInput, request: Request):
        local_request(request)
        redis_support.login(body.username, rate_peer(request, settings))
        try:
            return authentication.login(body.username, body.password)
        except AccessDenied:
            raise HTTPException(401, "INVALID_CREDENTIALS") from None

    @app.post("/api/conversations", status_code=201)
    def new_conversation(identity=Depends(student)):
        return store.create_conversation(identity)

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str, identity=Depends(student)):
        return store.conversation_view(conversation_id, identity)

    @app.post("/api/conversations/{conversation_id}/messages")
    async def message(conversation_id: str, body: MessageInput,
                      idempotency_key: Annotated[str, Header(min_length=1, max_length=128)], identity=Depends(student)):
        await asyncio.to_thread(redis_support.message, identity)
        run_id = store.enqueue(conversation_id, identity, body.message, idempotency_key)
        await runtime.complete(run_id)
        return store.deliverable(run_id, identity)

    def response_stream(run_id, identity):
        async def send():
            yield stream_event("meta", {"run_id": run_id})
            yield stream_event("status", {"state": "PROCESSING"})
            try:
                await runtime.complete(run_id)
                accepted = store.deliverable(run_id, identity)
                for offset in range(0, len(accepted["reply"]), settings.stream_chunk_chars):
                    if store.deliverable(run_id, identity) != accepted:
                        raise StateConflict("APPROVED_CONTENT_CHANGED")
                    yield stream_event("token", {"text": accepted["reply"][offset:offset + settings.stream_chunk_chars]})
                    await asyncio.sleep(0)
                if store.deliverable(run_id, identity) != accepted:
                    raise StateConflict("APPROVED_CONTENT_CHANGED")
                yield stream_event("done", {key: accepted[key] for key in ("run_id", "state", "message_id", "content_hash")})
            except asyncio.CancelledError:
                raise
            except Exception:
                yield stream_event("error", {"code": "REPLY_UNAVAILABLE", "run_id": run_id})
        return StreamingResponse(send(), media_type="text/event-stream", headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Run-ID": run_id})

    @app.post("/api/conversations/{conversation_id}/stream")
    async def stream(conversation_id: str, body: MessageInput,
                     idempotency_key: Annotated[str, Header(min_length=1, max_length=128)], identity=Depends(student)):
        await asyncio.to_thread(redis_support.message, identity)
        run_id = store.enqueue(conversation_id, identity, body.message, idempotency_key)
        return response_stream(run_id, identity)

    @app.get("/api/runs/{run_id}")
    def run_view(run_id: str, identity=Depends(actor)):
        state = store.authorized_run_state(run_id, identity)
        if state not in FINAL_STATES:
            return JSONResponse({"run_id": run_id, "state": "PROCESSING"}, status_code=202)
        if state == RunState.FAILED:
            return JSONResponse({"run_id": run_id, "state": "FAILED"}, status_code=503)
        return store.deliverable(run_id, identity)

    @app.get("/api/runs/{run_id}/trace")
    def run_trace(run_id: str, identity=Depends(actor)):
        return store.trace(run_id, identity)

    @app.get("/api/runs/{run_id}/stream")
    async def replay(run_id: str, identity=Depends(student)):
        store.authorized_run_state(run_id, identity)
        return response_stream(run_id, identity)

    @app.get("/api/cases")
    def cases(identity=Depends(actor)):
        return {"cases": store.cases(identity)}

    @app.get("/api/cases/{case_id}")
    def case(case_id: str, identity=Depends(actor)):
        return store.case_detail(case_id, identity)

    @app.post("/api/cases/{case_id}/{action}")
    def action(case_id: str, action: Literal["claim", "process", "close"], body: CaseActionInput,
               idempotency_key: Annotated[str, Header(min_length=1, max_length=128)], identity=Depends(actor)):
        return store.case_action(case_id, identity, action, body.note, body.expected_version, idempotency_key)

    return app
