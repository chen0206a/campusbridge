"""Additive web-application endpoints; the frozen Stage 2 runtime stays unchanged."""
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select

from campusbridge.api import create_app
from campusbridge.contracts import AccessDenied, digest
from campusbridge.db import Account, AgentRun, Conversation, LoginToken, Message, Student
from campusbridge.settings import Settings


def create_web_app(settings=None, model=None, knowledge=None, static_directory=None):
    config = settings or Settings.from_environment()
    app = create_app(config, model=model, knowledge=knowledge)
    db, auth, store = app.state.database, app.state.authentication, app.state.store
    bearer = HTTPBearer(auto_error=False)

    def credential(request: Request, value: HTTPAuthorizationCredentials | None = Depends(bearer)):
        allowed = {"127.0.0.1", "::1"} | ({"testclient"} if config.environment == "test" else set())
        if request.client is None or request.client.host not in allowed:
            raise HTTPException(403, "LOCAL_DEVELOPMENT_ONLY")
        if value is None:
            raise HTTPException(401, "AUTHENTICATION_REQUIRED")
        try:
            return auth.authenticate(value.credentials), value.credentials
        except AccessDenied:
            raise HTTPException(401, "INVALID_TOKEN") from None

    @app.get("/api/auth/me")
    def me(identity=Depends(credential)):
        actor, _ = identity
        with db.sessions() as session:
            account = session.get(Account, actor.account_id)
            student = session.get(Student, actor.account_id)
            return {"account_id": actor.account_id, "username": account.username, "role": actor.role,
                    "display_name": student.display_name if student else "辅导员",
                    "campus_id": actor.campus_id}

    @app.post("/api/auth/logout", status_code=204)
    def logout(identity=Depends(credential)):
        _, token = identity
        with db.sessions.begin() as session:
            session.execute(delete(LoginToken).where(LoginToken.token_hash == digest(token)))

    @app.get("/api/conversations")
    def conversations(identity=Depends(credential)):
        actor, _ = identity
        store.student(actor)
        with db.sessions() as session:
            # No candidate text, internal assessment or other student's conversations.
            rows = session.scalars(select(Conversation).where(Conversation.student_id == actor.account_id)
                .order_by(Conversation.created_at.desc(), Conversation.id).limit(100)).all()
            result = []
            for row in rows:
                first = session.scalar(select(Message).where(Message.conversation_id == row.id, Message.role == "user")
                    .order_by(Message.order_index).limit(1))
                latest = session.scalar(select(func.max(Message.created_at)).where(Message.conversation_id == row.id))
                count = session.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == row.id))
                result.append({"conversation_id": row.id, "title": first.text[:36] if first else "新的对话",
                    "created_at": row.created_at, "updated_at": latest or row.created_at, "message_count": count})
            result.sort(key=lambda item: (item["updated_at"], item["conversation_id"]), reverse=True)
            return {"conversations": result}

    @app.get("/api/cases/{case_id}/student-context")
    def student_context(case_id: str, identity=Depends(credential)):
        actor, _ = identity
        detail = store.case_detail(case_id, actor)  # Counselor and campus validation remain server-side.
        with db.sessions() as session:
            run = session.get(AgentRun, detail["run_id"])
            student = session.get(Student, run.student_id)
            return {"student_display_name": student.display_name, "student_id": student.id,
                    "request_text": run.input_text, "created_at": run.created_at}

    # Production build uses relative /api URLs and the same origin. The app uses no client-side path routes.
    static_directory = Path(static_directory) if static_directory else Path(__file__).resolve().parents[2] / "frontend/dist"
    if (static_directory / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_directory, html=True), name="campusbridge-web")
    return app
