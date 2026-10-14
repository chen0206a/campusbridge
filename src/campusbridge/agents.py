import json

from campusbridge.contracts import (
    AgentFailure, AgentName, CandidateOutput, ChatMessage, Citation, ContextOutput, IntentOutput, ModelRequest,
    ReviewOutput, RiskOutput, Route, digest, grounding_digest, identifier,
)
from campusbridge.policy import combine_risk, explicit_human_request, output_violation
from campusbridge.prompts_v3 import ROLE_PROMPTS
from campusbridge.grounding import MARKER, source_markers, validate_citations
from campusbridge.serialization import strict_json_object


def request_for(agent, data):
    schemas = {AgentName.INTENT: IntentOutput, AgentName.RISK: RiskOutput, AgentName.REVIEW: ReviewOutput}
    instruction = ROLE_PROMPTS[agent.value]
    if agent in schemas:
        instruction += "\nJSON schema（拒绝额外字段）：\n" + json.dumps(schemas[agent].model_json_schema(), ensure_ascii=False)
    payload = data.model_dump(mode="json")
    if agent == AgentName.RESPONSE:
        payload["allowed_source_ids"] = [item.source_id for item in data.context.knowledge]
    return ModelRequest(task=agent, messages=(ChatMessage(role="system", content=instruction),
        ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False))))


def object_reply(reply):
    return strict_json_object(reply.content)


class IntentAgent:
    async def run(self, data, invoke):
        reply = await invoke(request_for(AgentName.INTENT, data))
        output = IntentOutput.model_validate(object_reply(reply))
        return output.model_copy(update={"needs_human_support": output.needs_human_support or explicit_human_request(data.message)})


class RiskAgent:
    async def run(self, data, invoke):
        reply = await invoke(request_for(AgentName.RISK, data))
        assessment = RiskOutput.model_validate(object_reply(reply))
        return combine_risk(data.message, assessment, data.intent)


class ContextAgent:
    def __init__(self, knowledge):
        self.knowledge = knowledge

    async def run(self, data):
        route = Route.HUMAN_SUPPORT if data.risk.needs_human else (
            Route.KNOWLEDGE_SUPPORT if data.intent.needs_knowledge else Route.CONVERSATION)
        query = data.message
        if any(word in query for word in ("那", "刚才", "上述", "这个", "它")):
            previous = [item.text for item in data.history if item.role == "user"]
            if previous:
                query = previous[-1][:500] + " " + query
        items = await self.knowledge.search(query, data.campus_id) if route == Route.KNOWLEDGE_SUPPORT else ()
        status = "SKIPPED" if route != Route.KNOWLEDGE_SUPPORT else "HIT" if items else "NO_HIT"
        return ContextOutput(route=route, history=data.history, knowledge=items, retrieval_status=status,
                             query_hash=digest(query) if route == Route.KNOWLEDGE_SUPPORT else None)


class ResponseAgent:
    async def run(self, data, invoke):
        reply = await invoke(request_for(AgentName.RESPONSE, data))
        if not isinstance(reply.content, str) or not reply.content.strip():
            raise ValueError("Response must be a complete nonempty string")
        references = {item.source_id: item for item in data.context.knowledge}
        markers = source_markers(reply.content)
        if any(source not in references for source in markers):
            raise AgentFailure("CITATION_UNKNOWN_SOURCE")
        if data.context.retrieval_status == "HIT" and not markers:
            raise AgentFailure("CITATION_REQUIRED")
        citations = tuple(Citation(source_id=item.source_id, document_id=item.document_id, title=item.title,
            revision=item.revision, quote=item.text, source_hash=item.source_hash or digest(item.text))
            for source in dict.fromkeys(markers) for item in (references[source],))
        text = MARKER.sub(lambda match: "[source:" + match.group(1).strip() + "]", reply.content)
        candidate = CandidateOutput(candidate_id=identifier(), text=text, content_hash=digest(text),
            attempt=data.attempt, origin="MODEL", citations=citations, retrieval_status=data.context.retrieval_status,
            citations_hash=grounding_digest(citations, data.context.retrieval_status))
        validate_citations(candidate, data.context.knowledge)
        return candidate


class OutputReviewer:
    async def run(self, data, invoke):
        validate_citations(data.candidate, data.references)
        blocked = output_violation(data.candidate, data.risk)
        if (not blocked and data.candidate.retrieval_status in {"NO_HIT", "ERROR"}
                and not any(term in data.candidate.text for term in ("未找到", "没有检索到", "没有找到", "未检索到", "暂无"))):
            return ReviewOutput(candidate_id=data.candidate.candidate_id, content_hash=data.candidate.content_hash,
                citations_hash=data.candidate.citations_hash, verdict="RETRY", reason_codes=("UNSUPPORTED_CLAIM",), authority="RULE")
        if blocked:
            return blocked
        reply = await invoke(request_for(AgentName.REVIEW, data))
        output = ReviewOutput.model_validate(object_reply(reply))
        if output.authority != "MODEL":
            raise ValueError("A model cannot grant template approval")
        return output
