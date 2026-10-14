import asyncio

import pytest
from pydantic import ValidationError

from campusbridge.contracts import AccessDenied, AgentName, Identity, ModelReply, RiskLevel, RiskOutput
from campusbridge.db import Database
from campusbridge.policy import combine_risk, sanitized_input
from campusbridge.security import Authentication, DEMO_PASSWORD
from campusbridge.settings import Settings
from campusbridge.store import Store


def test_server_authentication_and_owned_conversation(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    config = Settings(environment="test", database_path=tmp_path / "test.sqlite3")
    auth = Authentication(db, config)
    auth.seed()
    session = auth.login("student_lan", DEMO_PASSWORD)
    student = auth.authenticate(session["access_token"])
    conversation = Store(db).create_conversation(student)
    assert Store(db).conversation_view(conversation["conversation_id"], student)["messages"] == []
    with pytest.raises(AccessDenied):
        auth.login("student_lan", "wrong")
    with pytest.raises(AccessDenied):
        auth.authenticate("forged")
    db.close()


def test_test_environment_cannot_default_to_development():
    with pytest.raises(ValidationError):
        Settings(environment="test")


def test_hard_risk_is_not_downgraded_by_model():
    low = RiskOutput(level=RiskLevel.LOW, needs_human=False, reason_codes=("NO_SIGNAL",))
    merged = combine_risk("我无法保证自己的安全", low)
    assert merged.level == RiskLevel.HIGH and merged.needs_human


def test_input_identifiers_and_credentials_are_masked():
    masked = sanitized_input("请记住 demo@example.test 13900123987 Bearer " + "a" * 24)
    assert "demo@example.test" not in masked and "13900123987" not in masked and "a" * 24 not in masked


@pytest.mark.parametrize("data", [{"content": "text", "output_tokens": -1}, {"content": "text", "input_tokens": True},
                                  {"content": "text", "hidden": "extra"}])
def test_model_reply_contract_rejects_invalid_usage_or_extra_fields(data):
    with pytest.raises(ValidationError):
        ModelReply.model_validate(data)
