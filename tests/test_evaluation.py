import asyncio
import json
from pathlib import Path

import pytest

from campusbridge.contracts import AgentFailure, AgentName, ChatMessage, ModelRequest
from campusbridge.evaluation import ScenarioModel, load_cases, prepare_packet, score_case, sha, verify_packet
from campusbridge.models import MockModel
from scripts.evaluate import BudgetedProvider


ROOT = Path(__file__).resolve().parents[1]


def test_fixed_dataset_is_original_versioned_and_balanced():
    cases = load_cases(ROOT / "eval/stage2/20261012-fixed-v1/cases.jsonl")
    assert len(cases) == 30
    assert sum(bool(c.get("fault")) for c in cases) == 10
    assert sum(len(c["turns"]) > 1 for c in cases) == 5
    assert len({tuple(c["turns"]) for c in cases}) == 30


def test_freeze_packet_preserves_bytes_and_refuses_overwrite(tmp_path):
    output = tmp_path / "packet"
    prepare_packet(ROOT, output)
    verified = verify_packet(ROOT, output)
    assert verified["valid"] and verified["case_count"] == 30
    assert sha(output / "cases.jsonl") == sha(ROOT / "eval/stage2/20261012-fixed-v1/cases.jsonl")
    assert sha(output / "prompts_v2.py") == sha(ROOT / "src/campusbridge/prompts_v2.py")
    with pytest.raises(ValueError, match="already exists"):
        prepare_packet(ROOT, output)


def test_packet_tamper_detected(tmp_path):
    output = tmp_path / "packet"
    prepare_packet(ROOT, output)
    (output / "cases.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        verify_packet(ROOT, output)


def test_live_source_tamper_detected_without_editing_project(tmp_path):
    output = tmp_path / "packet"
    prepare_packet(ROOT, output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert "src/campusbridge/evaluation.py" in manifest["source_sha256"]
    # A separate root missing frozen files fails validation; the real project remains untouched.
    with pytest.raises((ValueError, FileNotFoundError)):
        verify_packet(tmp_path, output)


def test_faults_are_explicit_and_raw_failures_are_saved():
    model = ScenarioModel(MockModel())
    model.configure("response_http_error")
    req = ModelRequest(task=AgentName.RESPONSE, messages=(ChatMessage(role="user", content="{}"),))
    with pytest.raises(AgentFailure, match="PROVIDER_HTTP_ERROR"):
        asyncio.run(model.complete(req))
    assert model.records[0]["controlled_injection"] is True
    assert model.records[0]["error_code"] == "PROVIDER_HTTP_ERROR"


def test_scoring_failures_remain_failures():
    case = {"case_id": "synthetic-unit", "group": "general", "expected": {
        "route": "CONVERSATION", "risk": "LOW", "case_created": False, "state": "ACCEPTED"}}
    score = score_case(case, {}, {"events": []}, False, lambda sid: None, {})
    assert not score["case_pass"] and {"route", "risk", "state"} <= set(score["mismatch_fields"])


def test_scoring_checks_real_source_instead_of_claimed_citation():
    case = {"case_id": "synthetic-unit", "group": "general", "expected": {
        "route": "CONVERSATION", "risk": "LOW", "case_created": False, "state": "ACCEPTED"}}
    citation = {"source_id": "invented", "quote": "fabricated", "source_hash": "0" * 64}
    score = score_case(case, {"state": "ACCEPTED", "citations": [citation]}, {"events": []}, False, lambda sid: None, {})
    assert not score["checks"]["citation_grounding"] and score["grounded_citations"] == 0


def test_real_provider_cost_budget_blocks_before_http():
    class Delegate:
        model_name = "unit-test"
        requests_sent = 0

        async def complete(self, request):
            self.requests_sent += 1
            raise AssertionError("budget must block before HTTP")

        async def aclose(self):
            return None

    provider = BudgetedProvider(Delegate(), max_cost_usd=0.00000001)
    request = ModelRequest(task=AgentName.INTENT, messages=(ChatMessage(role="user", content="{}"),))
    with pytest.raises(AgentFailure, match="PROVIDER_COST_BUDGET"):
        asyncio.run(provider.complete(request))
    assert provider.requests_sent == 0


def test_real_provider_input_limit_blocks_before_http():
    class Delegate:
        model_name = "unit-test"
        requests_sent = 0

        async def complete(self, request):
            self.requests_sent += 1
            raise AssertionError("budget must block before HTTP")

        async def aclose(self):
            return None

    provider = BudgetedProvider(Delegate(), max_cost_usd=1, max_input_bytes=100)
    request = ModelRequest(task=AgentName.INTENT, messages=(ChatMessage(role="user", content="x" * 200),))
    with pytest.raises(AgentFailure, match="PROVIDER_INPUT_BUDGET"):
        asyncio.run(provider.complete(request))
    assert provider.requests_sent == 0
