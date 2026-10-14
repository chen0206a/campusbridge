from typing import Protocol

from campusbridge.contracts import KnowledgeItem, ModelReply, ModelRequest


class ModelPort(Protocol):
    async def complete(self, request: ModelRequest) -> ModelReply: ...


class KnowledgePort(Protocol):
    async def search(self, query: str, campus_id: str) -> tuple[KnowledgeItem, ...]: ...
