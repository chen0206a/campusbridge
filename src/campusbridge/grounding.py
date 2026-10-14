"""Run-scoped exact source validation, shared by generation, review and delivery."""
import re

from campusbridge.contracts import AgentFailure, digest

MARKER = re.compile(r"\[source:([^\[\]]*)\]")


def source_markers(text):
    matches = list(MARKER.finditer(text))
    if text.count("[source:") != len(matches) or any(not m.group(1).strip() for m in matches):
        raise AgentFailure("CITATION_INVALID_FORMAT")
    return tuple(dict.fromkeys(m.group(1).strip() for m in matches))


def validate_citations(candidate, references):
    if candidate.origin == "SAFE_TEMPLATE":
        if candidate.citations or source_markers(candidate.text):
            raise AgentFailure("CITATION_TEMPLATE_INVALID")
        return
    allowed = {item.source_id: item for item in references}
    markers = source_markers(candidate.text)
    if any(sid not in allowed for sid in markers):
        raise AgentFailure("CITATION_UNKNOWN_SOURCE")
    if candidate.retrieval_status == "HIT" and not markers:
        raise AgentFailure("CITATION_REQUIRED")
    if candidate.retrieval_status != "HIT" and (markers or candidate.citations):
        raise AgentFailure("CITATION_WITHOUT_RETRIEVAL")
    if markers != tuple(c.source_id for c in candidate.citations):
        raise AgentFailure("CITATION_METADATA_MISMATCH")
    for citation in candidate.citations:
        item = allowed[citation.source_id]
        if (citation.quote != item.text or citation.source_hash != (item.source_hash or digest(item.text))
                or digest(citation.quote) != citation.source_hash or citation.document_id != item.document_id
                or citation.revision != item.revision or citation.title != item.title):
            raise AgentFailure("CITATION_METADATA_MISMATCH")
