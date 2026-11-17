"""TV4 — crash then resume on each team shape: one refund, one refund_committed audit.

lab8.crash_test only drives the swarm at point 20, so this is the crash evidence for our shapes.
"""

from __future__ import annotations

import pytest

from lab8.db import connect
from lab8.durability import InjectedCrash, committed_audit_count, get_refund
from lab8.shapes import orchestrator, single, workflow

SHAPES = {"workflow": workflow.run_claim, "single": single.run_claim, "orchestrator": orchestrator.run_claim}
POINTS = ["before_effect", "after_effect_before_checkpoint", "after_checkpoint"]


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_crash_then_resume_pays_once(public_db, shape, point):
    run = SHAPES[shape]
    with pytest.raises(InjectedCrash):
        run("C-012", seed=11, store_path=str(public_db), crash_at=point)
    result = run("C-012", seed=11, store_path=str(public_db))
    # a second resume must still be a replay, never a second payment
    again = run("C-012", seed=11, store_path=str(public_db))
    conn = connect(public_db)
    try:
        effect = get_refund(conn, "C-012")
        rows = conn.execute("SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-012'").fetchone()["n"]
        audits = committed_audit_count(conn, "C-012")
    finally:
        conn.close()
    assert result.status == again.status == "resolved"
    assert effect is not None and int(effect["amount_vnd"]) == 35000
    assert rows == 1 and audits == 1
