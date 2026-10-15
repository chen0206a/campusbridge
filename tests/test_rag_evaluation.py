import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from campusbridge.agents import ContextAgent, ResponseAgent
from campusbridge.contracts import (
    AgentFailure, ArbitrationInput, ContextInput, HistoryItem, IntentOutput,
    ModelReply, ResponseInput, RiskOutput, StateConflict,
)
from campusbridge.db import Database
from campusbridge.orchestration import Arbitrator
from campusbridge.rag_evaluation import (
    CONFIG, SLICES, RetrievalCase, aggregate, assert_hit_identity, load_cases, percentile,
    prepare, resolve_labels, run, safe_child, score_hits, verify,
)
from campusbridge.retrieval import SQLiteKnowledge, bundled_documents

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "eval/rag/20261014-stage-a-v1"


@pytest.fixture
def dataset_index(tmp_path):
    db = Database(tmp_path / "index.sqlite3")
    knowledge = SQLiteKnowledge(db, top_k=5)
    docs = bundled_documents() + json.loads((DATASET / "documents.json").read_text(encoding="utf-8"))
    knowledge.import_documents(docs)
    yield db, knowledge
    db.close()


def test_dataset_schema_labels_and_unique_cases(dataset_index):
    db, _ = dataset_index
    cases = load_cases(DATASET / "queries.jsonl")
    assert len(cases) == 72 and sum(c.no_answer for c in cases) == 16
    assert set(c.group for c in cases) == set(SLICES)
    labels = resolve_labels(db, cases)
    for case, label in zip(cases, labels):
        assert bool(label["relevant_source_ids"]) != case.no_answer
        for evidence in label["evidence_groups"]:
            assert evidence["end"] - evidence["start"] == len(evidence["quote"])
    lab = next(l for l in labels if l["case_id"] == "rag-a-cross-paragraph-01")
    assert len(lab["relevant_source_ids"]) >= 2  # Actual multi-chunk evidence, not merely two sentences.


@pytest.mark.parametrize("changes", [
    {"no_answer": True}, {"evidence": []}, {"no_answer_kind": "unsupported_fact"},
    {"case_id": "../escape"}, {"group": "invented"}, {"extra": 1},
])
def test_reject_invalid_reference_schema(changes):
    case = load_cases(DATASET / "queries.jsonl")[0].model_dump()
    with pytest.raises(ValidationError):
        RetrievalCase.model_validate({**case, **changes})


def test_gold_binding_rejects_forgery_and_wrong_campus(dataset_index):
    db, _ = dataset_index
    case = load_cases(DATASET / "queries.jsonl")[0]
    for changed in (case.model_copy(update={"campus_id": "different-campus"}),
                    RetrievalCase.model_validate({**case.model_dump(), "evidence": [{"document_id": "campus-library", "quote": "虚构不存在的证据"}]})):
        with pytest.raises(ValueError, match="LABEL_NOT_UNIQUE"):
            resolve_labels(db, [changed])


def test_duplicate_queries_and_id_rejected(tmp_path):
    p = tmp_path / "queries.jsonl"
    first = (DATASET / "queries.jsonl").read_text(encoding="utf-8").splitlines()[0]
    p.write_text(first + "\n" + first + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="DUPLICATE"):
        load_cases(p)


def test_exact_metric_math_and_alternative_chunk_labels():
    label = {"no_answer": False, "relevant_source_ids": ["a", "b"],
             "relevant_document_ids": ["doc"], "evidence_groups": [{"acceptable_source_ids": ["a", "b"]}]}
    result = score_hits([{"source_id": "x", "document_id": "wrong"}, {"source_id": "b", "document_id": "doc"}], label)
    assert result["recall@1"] == 0 and result["recall@3"] == .5
    assert result["document_recall@3"] == 1 and result["reciprocal_rank"] == .5
    assert result["evidence_group_coverage@3"] == 1  # Overlapping chunk alternatives remain one requirement.


def test_no_answer_exception_is_not_success():
    label = {"no_answer": True, "relevant_source_ids": [], "relevant_document_ids": [], "evidence_groups": []}
    assert score_hits([], label)["no_answer_correct"] is True
    assert score_hits([], label, error=True)["no_answer_correct"] is False
    assert score_hits([{"source_id": "x", "document_id": "x"}], label)["no_answer_correct"] is False
    assert score_hits([], label)["recall@3"] is None


def test_macro_answerable_and_micro_precision_denominators():
    good = {"no_answer": False, "relevant_source_ids": ["a"], "relevant_document_ids": ["d"],
            "evidence_groups": [{"acceptable_source_ids": ["a"]}]}
    negative = {"no_answer": True, "relevant_source_ids": [], "relevant_document_ids": [], "evidence_groups": []}
    rows = [{"scores": {"stage": score_hits([{"source_id": "a", "document_id": "d"}], good)}},
            {"scores": {"stage": score_hits([{"source_id": "z", "document_id": "z"}], negative)}}]
    metric = aggregate(rows, "stage")
    assert metric["recall@1"] == 1 and metric["retrieved_source_precision_micro"] == .5
    assert metric["no_answer_rejection_accuracy"] == 0


def test_nearest_rank_latency():
    assert percentile([1, 2, 3, 4, 5], .5) == 3
    assert percentile([1, 2, 3, 4, 5], .95) == 5
    assert percentile([], .5) is None


@pytest.mark.parametrize("query", ["", "? AND * \"", "量子舰船校准许可"])
def test_empty_and_no_result_queries(dataset_index, query):
    _, knowledge = dataset_index
    assert asyncio.run(knowledge.search(query, "fictional-campus-a")) == ()


def test_topk_prefix_stability_identity_and_campus_isolation(dataset_index):
    db, knowledge = dataset_index
    hits = asyncio.run(knowledge.search("图书馆座位预约", "fictional-campus-a"))
    assert hits
    production = SQLiteKnowledge(db, top_k=3)
    assert hits[:3] == asyncio.run(production.search("图书馆座位预约", "fictional-campus-a"))
    assert hits == asyncio.run(knowledge.search("图书馆座位预约", "fictional-campus-a"))
    for hit in hits:
        assert_hit_identity(knowledge, hit, "fictional-campus-a")
    assert asyncio.run(knowledge.search("图书馆预约", "fictional-campus-b")) == ()


def test_revision_switch_does_not_reactivate_old_index(dataset_index):
    _, knowledge = dataset_index
    original = bundled_documents()[0]
    old_ids = {h.source_id for h in asyncio.run(knowledge.search("图书馆预约", "fictional-campus-a")) if h.document_id == original.document_id}
    revised = original.model_copy(update={"revision": "rag-test-next", "text": "图书馆预约座位请向工作人员核对新的日期。"})
    knowledge.import_documents([revised])
    knowledge.import_documents([original])
    hits = asyncio.run(knowledge.search("图书馆预约座位", "fictional-campus-a"))
    assert not old_ids.intersection(h.source_id for h in hits)
    assert all(h.revision == "rag-test-next" for h in hits if h.document_id == original.document_id)
    with pytest.raises(StateConflict):
        knowledge.import_documents([revised.model_copy(update={"text": "同版本不得改写"})])


def test_history_context_and_arbitration_keep_query_and_source_binding(dataset_index):
    db, _ = dataset_index
    case = next(c for c in load_cases(DATASET / "queries.jsonl") if c.case_id == "rag-a-history-01")
    intent = IntentOutput(intent="CAMPUS_SERVICE", needs_knowledge=True)
    risk = RiskOutput(level="LOW", needs_human_support=False, reason_codes=("NO_SIGNAL",))
    context = asyncio.run(ContextAgent(SQLiteKnowledge(db)).run(ContextInput(message=case.query,
        history=case.history, intent=intent, risk=risk, campus_id=case.campus_id)))
    decision = Arbitrator().decide(ArbitrationInput(message=case.query, history=case.history,
        intent=intent, risk=risk, context=context))
    assert context.knowledge and context.knowledge[0].document_id == "campus-library"
    assert set(h.source_id for h in decision.context.knowledge).issubset(h.source_id for h in context.knowledge)


def test_retrieval_failure_propagates_instead_of_fabricating_sources():
    class FailedKnowledge:
        async def search(self, query, campus_id):
            raise RuntimeError("offline index unavailable")
    data = ContextInput(message="图书馆预约", history=(), campus_id="fictional-campus-a",
        intent=IntentOutput(intent="CAMPUS_SERVICE", needs_knowledge=True),
        risk=RiskOutput(level="LOW", needs_human_support=False, reason_codes=("NO_SIGNAL",)))
    with pytest.raises(RuntimeError):
        asyncio.run(ContextAgent(FailedKnowledge()).run(data))


@pytest.mark.parametrize("reply", ["[source:invented]", "[source:foreign-campus-source]", "[source:old-session-source]"])
def test_forged_or_cross_scope_citation_is_rejected(dataset_index, reply):
    db, _ = dataset_index
    risk = RiskOutput(level="LOW", needs_human_support=False, reason_codes=("NO_SIGNAL",))
    context = asyncio.run(ContextAgent(SQLiteKnowledge(db)).run(ContextInput(message="图书馆预约", history=(),
        intent=IntentOutput(intent="CAMPUS_SERVICE", needs_knowledge=True), risk=risk, campus_id="fictional-campus-a")))
    async def invoke(request):
        return ModelReply(content=reply)
    with pytest.raises(AgentFailure, match="UNKNOWN_SOURCE"):
        asyncio.run(ResponseAgent().run(ResponseInput(message="图书馆预约", context=context, risk=risk, attempt=1), invoke))


def test_packet_integrity_refuse_overwrite_and_no_model_execution(tmp_path, monkeypatch):
    from campusbridge.models import MockModel
    async def forbidden(*args, **kwargs):
        raise AssertionError("Model execution is not allowed in retrieval evaluation")
    monkeypatch.setattr(MockModel, "complete", forbidden)
    packet, output = tmp_path / "packet", tmp_path / "run"
    assert prepare(ROOT, DATASET, packet)["cases"] == 72
    assert verify(ROOT, packet)["passed"]
    with pytest.raises(ValueError, match="ALREADY_EXISTS"):
        prepare(ROOT, DATASET, packet)
    summary = run(ROOT, packet, output)
    assert summary["model_api_calls"] == 0
    assert summary["overall"]["ranking_top5"]["queries"] == 72
    assert summary["overall"]["ranking_top5"]["exceptions"] == 0
    assert summary["source_identity_valid_rate"] == 1
    with pytest.raises(ValueError, match="ALREADY_EXISTS"):
        run(ROOT, packet, output)
    p = packet / "queries.jsonl"
    p.write_bytes(p.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        verify(ROOT, packet)


def test_packet_manifest_cannot_escape_root(tmp_path):
    with pytest.raises(ValueError, match="UNSAFE"):
        safe_child(tmp_path, "../outside")
