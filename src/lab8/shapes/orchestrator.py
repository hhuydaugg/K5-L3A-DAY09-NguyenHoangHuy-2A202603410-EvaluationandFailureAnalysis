"""Shape C — orchestrator plus read-only investigators. Student work.

1. Build a handoff with `lab8.handoff.make_handoff`. Allowed tools are read-only.
   Never put parent_secret, raw headers, or the full claim row into facts.
2. `lab8.runtime.run_readonly_investigators` runs those tools.
3. The orchestrator quotes from the investigator's order view and calls
   `durability.issue_refund`. Investigators have no write registry.
   Pass the full quote; the host subtracts earlier refunds on the same order.
   `lookup_refund_history` is a read-only tool you may hand to an investigator.
4. Trace the sanitized handoff, not the parent context.

An LLM summary of the parent context is not a sanitizer. Use the allowlist.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import json
import sqlite3

from lab8.db import connect
from lab8.durability import issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import compute_compensation, get_claim_view, tool_cost
from lab8.trace import Trace

SHAPE = "orchestrator"
# Investigator gets the one read tool it needs. The orchestrator quotes itself (docstring step 3).
INVESTIGATOR_TOOLS = ["lookup_order"]


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

    def done(status: str, proposal: int = 0, reason: str | None = None) -> RunResult:
        # MockLLM drafts the customer reply; the prompt carries no ticket text or PII.
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

    budget.add_tool(tool_cost())
    claim = get_claim_view(conn, claim_id)
    if not claim["found"]:
        return done("failed", reason="unknown_claim")
    trace.add("claim", {"claim_id": claim_id, "has_order_id": bool(claim["order_id"])})
    if not claim["order_id"]:
        return done("needs_info", reason="missing_order_id")

    # The orchestrator model plans the delegation; its output never reaches the handoff.
    complete(budget, seed=seed, purpose="plan", prompt=f"delegate order check for {claim_id}")
    # Allowlist, not an LLM summary: no ticket, parent_secret, headers or claim row cross over.
    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="verify_order_for_compensation",
        facts={},
        allowed_tools=INVESTIGATOR_TOOLS,
    )
    trace.add("handoff", handoff)
    investigated = run_readonly_investigators(conn, budget, handoff, principal_id=claim["principal_id"])
    order_view = investigated["tools"].get("lookup_order") or {"visible": False, "reason": "not_investigated"}
    # Investigator reports back (one model call); lookup_order already allowlists the order fields.
    complete(budget, seed=seed, purpose="investigate", prompt=json.dumps(handoff, ensure_ascii=False))
    trace.add("investigator", {"lookup_order": order_view})
    if not order_view["visible"]:
        return done("escalated", reason=order_view["reason"])

    # Money comes from the order row via compute_compensation, in orchestrator code.
    budget.add_tool(tool_cost())
    quote = compute_compensation(conn, order_view["order"])
    trace.add("quote", quote)
    proposal = int(quote["amount_vnd"])
    if proposal <= 0:
        return done("denied", reason=quote["reason"])

    # Full quote to the host gate; InjectedCrash propagates so resume is tested for real.
    outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    trace.add("refund", {"ok": outcome["ok"], "reason": outcome["reason"], "conflict": outcome["conflict"]})
    if not outcome["ok"]:
        return done("failed" if outcome["conflict"] else "denied", proposal, outcome["reason"])
    return done("resolved", proposal)
