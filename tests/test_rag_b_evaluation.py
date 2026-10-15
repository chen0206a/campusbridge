import json
from pathlib import Path

import pytest

from campusbridge.db import Database
from campusbridge.rag_b_evaluation import aggregate, cases_labels, freeze, scores, validate_checksums, verify
from campusbridge.rag_evaluation import load_cases, write_json
from campusbridge.retrieval import SQLiteKnowledge

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "eval/rag/20261015-stage-b-v1"


def test_recall5_requires_requested_candidate_depth():
    label = {"no_answer": False, "relevant_source_ids": ["target"], "relevant_document_ids": ["doc"], "evidence_groups": []}
    hits = [{"source_id": str(i), "document_id": "wrong"} for i in range(4)] + [{"source_id": "target", "document_id": "doc"}]
    assert scores(hits, label, requested_depth=5)["recall@5"] == 1
    assert scores(hits, label, requested_depth=5)["recall@3"] == 0
    assert scores(hits[:3], label, requested_depth=3)["recall@5"] is None
    assert scores([], label, requested_depth=5)["recall@5"] == 0


def test_separate_missing_material_from_insufficient_facts():
    label = {"no_answer": True, "relevant_source_ids": [], "relevant_document_ids": [], "evidence_groups": []}
    rows = [{"reference": label, "no_answer_kind": kind, "scores": {"ranking_top5": scores(
        [{"source_id": "x", "document_id": "d"}] if kind == "unsupported_fact" else [], label, requested_depth=5)}}
        for kind in ("outside_scope", "campus_isolation", "unsupported_fact")]
    summary = aggregate(rows, "ranking_top5")
    assert summary["no_material"]["count"] == 2 and summary["no_material"]["empty_rejection_rate"] == 1
    assert summary["insufficient_evidence"]["empty_rejection_rate"] == 0


def test_frozen_datasets_binding_and_no_cross_split_duplicates(tmp_path):
    validate_checksums(DATASET)
    db = Database(tmp_path / "labels.sqlite3")
    try:
        SQLiteKnowledge(db).import_documents(json.loads((DATASET / "corpus.json").read_text(encoding="utf-8")))
        keys = set()
        for split, expected in (("stage-a", 72), ("dev", 32), ("holdout", 32)):
            cases, labels = cases_labels(db, DATASET, split)
            assert len(cases) == len(labels) == expected
            for case in cases:
                key = (case.campus_id, case.query, tuple(h.text for h in case.history))
                assert key not in keys
                keys.add(key)
        assert (DATASET / "stage-a.jsonl").read_bytes() == (ROOT / "eval/rag/20261014-stage-a-v1/queries.jsonl").read_bytes()
    finally: db.close()


def test_packet_freeze_verify_tamper_and_overwrite(tmp_path):
    target = tmp_path / "packet"
    assert freeze(ROOT, target)["passed"]
    assert verify(ROOT, target)["passed"]
    with pytest.raises(ValueError, match="ALREADY_EXISTS"): freeze(ROOT, target)
    (target / "dev.jsonl").write_bytes(b"changed")
    with pytest.raises(ValueError, match="HASH_MISMATCH"): verify(ROOT, target)


def test_checksum_rejects_extra_files(tmp_path):
    target = tmp_path / "packet"
    freeze(ROOT, target)
    (target / "extra.json").write_text("{}")
    with pytest.raises(ValueError, match="FILE_SET"): validate_checksums(target)
