"""TV1 — workflow shape: ticket text never changes the money, trace exists on early exits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.db import connect
from lab8.durability import get_refund
from lab8.shapes.workflow import run_claim


@pytest.mark.parametrize("seed", [11, 22, 33, 44, 55])
def test_workflow_ignores_delay_written_in_ticket(public_db, seed):
    # C-002: ticket says "trễ 5 ngày", order row says 2 days -> deny on every seed (swarm misses seed 55).
    result = run_claim("C-002", seed=seed, store_path=str(public_db))
    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    conn = connect(public_db)
    try:
        assert get_refund(conn, "C-002") is None
    finally:
        conn.close()


def test_workflow_needs_info_still_writes_clean_trace(public_db):
    # C-004 has no order_id: early return, no ledger write, but trace contract v1 must hold.
    result = run_claim("C-004", seed=11, store_path=str(public_db))
    assert result.status == "needs_info"
    assert result.refund_effects == [] and result.committed_audit_count == 0
    body = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert body["run_id"] == result.run_id
    assert isinstance(body["events"], list) and body["events"]
    blob = json.dumps(body, ensure_ascii=False)
    assert "CANARY_" not in blob
    assert "không nhớ mã đơn" not in blob  # ticket text is user data, not traced


def test_workflow_trace_has_no_order_pii(public_db):
    # C-001 resolves via order O-107; its phone/address/internal note must never reach the trace.
    result = run_claim("C-001", seed=11, store_path=str(public_db))
    assert result.status == "resolved"
    blob = Path(result.trace_path).read_text(encoding="utf-8")
    for token in ("0901000107", "12 Le Loi Q1", "CANARY_INTERNAL_O107", "CANARY_SECRET_"):
        assert token not in blob
