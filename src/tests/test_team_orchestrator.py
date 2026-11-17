"""TV3 — orchestrator shape: the handoff crosses the boundary through the allowlist only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.handoff import ALLOWED_FIELDS, FACT_KEYS, READONLY_TOOLS
from lab8.shapes.orchestrator import run_claim


def _events(result):
    return json.loads(Path(result.trace_path).read_text(encoding="utf-8"))["events"]


@pytest.mark.parametrize("claim_id", ["C-001", "C-003", "C-013"])
def test_orchestrator_traces_only_sanitized_handoff(public_db, claim_id):
    result = run_claim(claim_id, seed=11, store_path=str(public_db))
    handoffs = [e["payload"] for e in _events(result) if e["kind"] == "handoff"]
    assert len(handoffs) == 1
    handoff = handoffs[0]
    assert set(handoff) <= ALLOWED_FIELDS
    assert set(handoff["facts"]) <= FACT_KEYS
    assert set(handoff["allowed_tools"]) <= READONLY_TOOLS
    blob = Path(result.trace_path).read_text(encoding="utf-8")
    for token in ("CANARY_", "parent_secret", "raw_headers", "Bearer", "ticket"):
        assert token not in blob


def test_orchestrator_needs_info_skips_handoff(public_db):
    # No order_id: nothing to investigate, so no worker is started and nothing is written.
    result = run_claim("C-004", seed=11, store_path=str(public_db))
    assert result.status == "needs_info" and result.refund_effects == []
    assert not [e for e in _events(result) if e["kind"] == "handoff"]


@pytest.mark.parametrize("seed", [11, 22, 33, 44, 55])
def test_orchestrator_ignores_delay_written_in_ticket(public_db, seed):
    result = run_claim("C-002", seed=seed, store_path=str(public_db))
    assert result.status == "denied" and result.proposal_amount == 0 and result.refund_effects == []
