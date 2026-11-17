"""Shape A — deterministic workflow. Student work.

Signature (do not change):

    run_claim(claim_id, *, seed, store_path, crash_at=None) -> RunResult

Suggested steps. Money stays in code (`lab8.tools.compute_compensation`).

1. Open the store. Read the claim through `get_claim_view` (not SELECT *).
2. Missing order_id -> status `needs_info`. Do not write the ledger.
3. `lookup_order`. If not visible -> `escalated`. Do not copy phone, address, item, internal note.
4. Quote from the order row. Ignore delay numbers written in the ticket.
5. Quote amount 0 -> `denied`. Do not call issue_refund.
6. Quote amount > 0 -> `durability.issue_refund(conn, claim_id, <full quote>)`.
   Pass the FULL quote. The host checks the claim's own approval and subtracts
   what earlier claims already got for the same order (T13, T14). Do not
   subtract it yourself or the host subtracts twice.
   Auth failure or order already refunded -> `denied` (the host writes `policy_denied`).
   `proposal_amount` stays the full quote. Amount conflict -> `failed`.
7. Success -> `resolved`. One ledger row, one `refund_committed` audit.
8. Use MockLLM only to draft the reply text, if you want a model call at all.

The model must not choose the refund amount and must not commit the refund.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import issue_refund
from lab8.mock_llm import complete
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import compute_compensation, get_claim_view, lookup_order, tool_cost
from lab8.trace import Trace

SHAPE = "workflow"


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def read(tool, *args):
        budget.add_tool(tool_cost())
        return tool(conn, *args)

    def done(status: str, proposal: int = 0, reason: str | None = None) -> RunResult:
        # MockLLM only drafts the customer reply; the prompt carries no ticket text or PII.
        complete(budget, seed=seed, purpose="draft", prompt=f"{status}:{proposal}")
        trace.add("decision", {"status": status, "proposal_amount": proposal, "reason": reason})
        return finish(
            conn,
            shape=SHAPE,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    # Step 1: allowlisted claim view. The ticket is user data: never traced, never parsed for numbers.
    claim = read(get_claim_view, claim_id)
    if not claim["found"]:
        return done("failed", reason="unknown_claim")
    trace.add("claim", {"claim_id": claim_id, "has_order_id": bool(claim["order_id"])})

    # Step 2: no order_id -> ask the customer; do not guess it from the ticket.
    if not claim["order_id"]:
        return done("needs_info", reason="missing_order_id")

    # Step 3: lookup_order already drops phone, address, item, internal note.
    order_view = read(lookup_order, claim["order_id"], claim["principal_id"])
    trace.add("lookup_order", order_view)
    if not order_view["visible"]:
        return done("escalated", reason=order_view["reason"])

    # Steps 4-5: money comes from the order row via compute_compensation, not from the model.
    quote = read(compute_compensation, order_view["order"])
    trace.add("quote", quote)
    proposal = int(quote["amount_vnd"])
    if proposal <= 0:
        return done("denied", reason=quote["reason"])

    # Step 6: full quote to the host gate; InjectedCrash propagates so resume is tested for real.
    outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    trace.add("refund", {"ok": outcome["ok"], "reason": outcome["reason"], "conflict": outcome["conflict"]})
    if not outcome["ok"]:
        return done("failed" if outcome["conflict"] else "denied", proposal, outcome["reason"])

    # Step 7: committed or idempotent replay -> one ledger row, one refund_committed audit.
    return done("resolved", proposal)
