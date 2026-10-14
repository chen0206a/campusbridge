import asyncio
import re

import httpx

from campusbridge.contracts import AgentFailure, AgentName, ModelReply
from campusbridge.serialization import strict_json_object


class OpenAICompatibleProvider:
    """One request per invocation; no automatic retries, redirect forwarding or logged bodies."""
    provider_name = "openai-compatible"

    def __init__(self, settings, client=None, max_requests=None):
        self.settings = settings
        base = settings.endpoint.rstrip("/")
        self.url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        self.model_name = settings.model
        self.client = client or httpx.AsyncClient(timeout=settings.model_timeout, trust_env=False, follow_redirects=False)
        self.owns_client = client is None
        self.max_requests = max_requests
        self.requests_sent = 0

    async def complete(self, request):
        if self.max_requests is not None and self.requests_sent >= self.max_requests:
            raise AgentFailure("PROVIDER_REQUEST_BUDGET")
        body = {"model": self.model_name, "messages": [message.model_dump() for message in request.messages],
                "temperature": request.temperature, "stream": False,
                self.settings.provider_max_tokens_parameter: request.max_output_tokens}
        if self.settings.provider_json_mode and request.task != AgentName.RESPONSE:
            body["response_format"] = {"type": "json_object"}
        if self.settings.provider_thinking_mode is not None:
            body["thinking"] = {"type": self.settings.provider_thinking_mode}
        self.requests_sent += 1
        reported_usage = None
        try:
            async with self.client.stream("POST", self.url, json=body, headers={
                    "Authorization": "Bearer " + self.settings.api_key.get_secret_value()},
                    follow_redirects=False, timeout=self.settings.model_timeout) as response:
                if response.status_code != 200:
                    code = {401: "PROVIDER_AUTH", 403: "PROVIDER_AUTH", 429: "PROVIDER_RATE_LIMIT"}.get(
                        response.status_code, "PROVIDER_HTTP_ERROR")
                    raise AgentFailure(code)
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 1_000_000:
                        raise AgentFailure("PROVIDER_RESPONSE_TOO_LARGE")
                    chunks.append(chunk)
                envelope = strict_json_object(b"".join(chunks).decode("utf-8"))
                usage = envelope.get("usage")
                if usage is not None and not isinstance(usage, dict):
                    raise AgentFailure("PROVIDER_INVALID_USAGE")
                usage = usage or {}
                reported_usage = ModelReply(content="", input_tokens=usage.get("prompt_tokens"),
                                            output_tokens=usage.get("completion_tokens"))
                choices = envelope.get("choices")
                if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                    raise AgentFailure("PROVIDER_INVALID_RESPONSE")
                choice = choices[0]
                if choice.get("finish_reason") == "length":
                    raise AgentFailure("PROVIDER_TRUNCATED")
                if choice.get("finish_reason") != "stop":
                    raise AgentFailure("PROVIDER_UNSUPPORTED_FINISH")
                message = choice.get("message")
                if not isinstance(message, dict) or message.get("refusal") or message.get("tool_calls"):
                    raise AgentFailure("PROVIDER_REFUSAL_OR_TOOL_CALL")
                content = message.get("content")
                if not isinstance(content, str) or not content.strip() or len(content) > 16000:
                    raise AgentFailure("PROVIDER_INVALID_RESPONSE")
                if self.settings.api_key.get_secret_value() in content:
                    raise AgentFailure("PROVIDER_SECRET_REFLECTION")
                if request.task != AgentName.RESPONSE:
                    strict_json_object(content)
                request_id = envelope.get("id")
                safe_id = request_id if isinstance(request_id, str) and re.fullmatch(r"chatcmpl-[a-zA-Z0-9_-]{1,100}", request_id) else None
                return ModelReply(content=content, input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"), finish_reason="stop", request_id=safe_id)
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            raise AgentFailure("PROVIDER_TIMEOUT") from None
        except httpx.TransportError:
            raise AgentFailure("PROVIDER_NETWORK_ERROR") from None
        except AgentFailure as exc:
            exc.usage = reported_usage
            raise
        except (ValueError, TypeError, UnicodeError):
            raise AgentFailure("PROVIDER_INVALID_RESPONSE", usage=reported_usage) from None

    async def aclose(self):
        if self.owns_client:
            await self.client.aclose()
