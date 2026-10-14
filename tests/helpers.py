import asyncio
import json

from campusbridge.contracts import AgentName, ModelReply
from campusbridge.models import MockModel


class ControlledModel(MockModel):
    def __init__(self, stage=None, mode=None, texts=None, verdicts=None):
        self.stage, self.mode = stage, mode
        self.texts, self.verdicts = texts or [], verdicts or []
        self.requests = []
        self.responses = 0
        self.reviews = 0
        self.before_response = None

    async def complete(self, request):
        self.requests.append(request)
        if self.stage == request.task:
            if self.mode == "timeout":
                await asyncio.sleep(1)
            elif self.mode == "exception":
                raise RuntimeError("private upstream credential must not leak")
            elif self.mode == "invalid":
                return ModelReply(content={"unexpected": True})
        if request.task == AgentName.RESPONSE:
            if self.before_response:
                self.before_response()
            self.responses += 1
            if self.texts:
                return ModelReply(content=self.texts[min(self.responses - 1, len(self.texts) - 1)])
        if request.task == AgentName.REVIEW:
            self.reviews += 1
            result = await super().complete(request)
            value = dict(result.content)
            if self.verdicts:
                value["verdict"] = self.verdicts[min(self.reviews - 1, len(self.verdicts) - 1)]
                if value["verdict"] != "PASS":
                    value["reason_codes"] = ["MOCK_REJECTION"]
            if self.mode == "mismatch":
                value["candidate_id"] = "candidate-from-a-different-turn"
            return ModelReply(content=value)
        return await super().complete(request)
