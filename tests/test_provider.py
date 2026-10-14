import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from campusbridge.agents import IntentAgent
from campusbridge.contracts import AgentFailure, AgentName, ChatMessage, IntentInput, ModelRequest
from campusbridge.provider import OpenAICompatibleProvider
from campusbridge.serialization import strict_json_object
from campusbridge.settings import Settings


KEY = "test-only-not-a-real-api-key-20261012"


def settings(**kwargs):
    return Settings(model_provider="openai-compatible", endpoint="https://provider.example/v1",
                    model="synthetic-test-model", api_key=KEY, **kwargs)


def request(task=AgentName.INTENT):
    return ModelRequest(task=task, messages=(ChatMessage(role="system", content="JSON task"),
        ChatMessage(role="user", content="{}")))


def envelope(content='{"intent":"GENERAL","needs_knowledge":false}', **kwargs):
    return {"id": "chatcmpl-synthetic", "choices": [{"message": {"content": content}, "finish_reason": "stop"}], **kwargs}


def invoke(handler, req=None, config=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(config or settings(), client=client)
            return await provider.complete(req or request())
    return asyncio.run(go())


def test_request_mapping_and_usage():
    def handler(req):
        body = json.loads(req.content)
        assert str(req.url) == "https://provider.example/v1/chat/completions"
        assert req.headers["Authorization"] == "Bearer " + KEY
        assert body["messages"][0]["role"] == "system"
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 1024 and body["temperature"] == 0 and body["stream"] is False
        return httpx.Response(200, json=envelope(usage={"prompt_tokens": 31, "completion_tokens": 9}))
    result = invoke(handler)
    assert (result.input_tokens, result.output_tokens, result.request_id) == (31, 9, "chatcmpl-synthetic")


def test_response_is_plain_text_and_usage_can_be_unknown():
    def handler(req):
        body = json.loads(req.content)
        assert "response_format" not in body
        assert "max_completion_tokens" in body and "max_tokens" not in body
        return httpx.Response(200, json=envelope("原创完整回复"))
    result = invoke(handler, request(AgentName.RESPONSE), settings(provider_max_tokens_parameter="max_completion_tokens"))
    assert result.content == "原创完整回复" and result.input_tokens is None


@pytest.mark.parametrize("status,code", [(401, "PROVIDER_AUTH"), (403, "PROVIDER_AUTH"),
    (429, "PROVIDER_RATE_LIMIT"), (503, "PROVIDER_HTTP_ERROR"), (302, "PROVIDER_HTTP_ERROR")])
def test_http_failure_no_body_or_key_leak_and_no_retry(status, code):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(status, text="private body " + KEY, headers={"Location": "https://untrusted.example"})
    with pytest.raises(AgentFailure) as failure:
        invoke(handler)
    assert failure.value.code == code and len(seen) == 1 and KEY not in str(failure.value)


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout, "PROVIDER_TIMEOUT"),
    (httpx.ConnectError, "PROVIDER_NETWORK_ERROR")])
def test_transport_failure(exception, code):
    def handler(req):
        raise exception("private diagnostic " + KEY)
    with pytest.raises(AgentFailure, match=code):
        invoke(handler)


@pytest.mark.parametrize("value,code", [({"choices": []}, "PROVIDER_INVALID_RESPONSE"),
    ({"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}, "PROVIDER_TRUNCATED"),
    ({"choices": [{"message": {"content": "x", "refusal": "refused"}, "finish_reason": "stop"}]}, "PROVIDER_REFUSAL_OR_TOOL_CALL"),
    ({"choices": [{"message": {"content": "x", "tool_calls": [{}]}, "finish_reason": "stop"}]}, "PROVIDER_REFUSAL_OR_TOOL_CALL"),
    (envelope("```json\n{}\n```"), "PROVIDER_INVALID_RESPONSE"),
    (envelope('{"x":1,"x":2}'), "PROVIDER_INVALID_RESPONSE"),
    (envelope(KEY), "PROVIDER_SECRET_REFLECTION"),
    (envelope(usage={"prompt_tokens": "12"}), "PROVIDER_INVALID_RESPONSE")])
def test_strict_transport_output(value, code):
    with pytest.raises(AgentFailure, match=code):
        invoke(lambda req: httpx.Response(200, json=value))


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', "[]", "null", 'before {"a":1}', "```json\n{}\n```"])
def test_strict_json_rejects_repairs_and_duplicates(raw):
    with pytest.raises(ValueError):
        strict_json_object(raw)


def test_json_mode_still_requires_role_schema():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200,
                json=envelope('{"intent":"GENERAL","needs_knowledge":"false"}')))) as client:
            provider = OpenAICompatibleProvider(settings(), client=client)
            with pytest.raises(ValidationError):
                await IntentAgent().run(IntentInput(message="你好", history=()), provider.complete)
    asyncio.run(go())


def test_provider_budget_before_network():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: pytest.fail("network"))) as client:
            with pytest.raises(AgentFailure, match="PROVIDER_REQUEST_BUDGET"):
                await OpenAICompatibleProvider(settings(), client=client, max_requests=0).complete(request())
    asyncio.run(go())


def test_settings_env_switch_is_explicit(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("CAMPUSBRIDGE_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    assert Settings.from_environment().model_provider == "mock"
    monkeypatch.setenv("CAMPUSBRIDGE_MODEL_PROVIDER", "openai-compatible")
    with pytest.raises(ValidationError):
        Settings.from_environment()
    monkeypatch.setenv("CAMPUSBRIDGE_ENDPOINT", "https://provider.example/v1")
    monkeypatch.setenv("CAMPUSBRIDGE_MODEL", "synthetic-test-model")
    monkeypatch.setenv("CAMPUSBRIDGE_API_KEY", KEY)
    monkeypatch.setenv("CAMPUSBRIDGE_THINKING_MODE", "disabled")
    config = Settings.from_environment()
    assert config.provider_thinking_mode == "disabled"
    assert config.model_timeout == 20 and config.run_timeout == 90
    assert KEY not in repr(config) and "api_key" not in config.model_dump()


@pytest.mark.parametrize("url", ["http://remote.example/v1", "https://user:pass@example.test/v1",
    "https://example.test/v1?key=secret", "ftp://example.test"])
def test_bad_endpoint_rejected(url):
    with pytest.raises(ValidationError):
        Settings(model_provider="openai-compatible", endpoint=url, model="test", api_key=KEY)


def test_endpoint_full_path_and_json_mode_disabled():
    config = settings(provider_json_mode=False).model_copy(update={"endpoint": "http://127.0.0.1:9999/v1/chat/completions"})
    def handler(req):
        assert str(req.url).endswith("/v1/chat/completions") and "response_format" not in json.loads(req.content)
        return httpx.Response(200, json=envelope())
    invoke(handler, config=config)


def test_optional_thinking_mode_is_forwarded():
    config = settings(provider_thinking_mode="disabled")
    def handler(req):
        body = json.loads(req.content)
        assert body["thinking"] == {"type": "disabled"}
        return httpx.Response(200, json=envelope())
    invoke(handler, config=config)


def test_reported_usage_survives_truncated_output():
    value = envelope("partial text", usage={"prompt_tokens": 120, "completion_tokens": 1024})
    value["choices"][0]["finish_reason"] = "length"
    with pytest.raises(AgentFailure) as failure:
        invoke(lambda req: httpx.Response(200, json=value))
    assert failure.value.code == "PROVIDER_TRUNCATED"
    assert failure.value.usage.input_tokens == 120 and failure.value.usage.output_tokens == 1024
