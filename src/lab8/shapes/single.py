"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from lab8.db import connect
from lab8.durability import issue_refund
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace

SHAPE = "single"
MAX_STEPS = 8


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

    # Raw outputs of the read tools the loop dispatched. The model's own words never set the amount.
    facts: dict[str, Any] = {}
    steps = 0

    def decide(seen: dict[str, Any]) -> dict[str, Any]:
        nonlocal steps
        steps += 1
        if "last_tool" in seen:
            facts[seen["last_tool"]] = seen["last_result"]
        # One model call per ReAct step, so cost stays comparable with the other shapes.
        complete(budget, seed=seed, purpose="react", prompt=json.dumps(seen, ensure_ascii=False, default=str))
        action = _next_action(claim, facts)
        trace.add("react_step", {"step": steps, "action": action.get("tool", "final")})
        return action

    loop = run_react(conn, budget, observation={"claim": claim}, decide=decide, max_steps=MAX_STEPS)
    if loop["stopped"] == "step_cap":
        return done("failed", reason="step_cap")

    # After the loop, code (not the model) checks the facts and is the only caller of the host write.
    if not claim["order_id"]:
        return done("needs_info", reason="missing_order_id")
    order_view = facts.get("lookup_order") or {"visible": False, "reason": "order_not_checked"}
    trace.add("lookup_order", order_view)
    if not order_view["visible"]:
        return done("escalated", reason=order_view["reason"])
    quote = facts.get("compute_compensation")
    if quote is None:
        return done("failed", reason="no_quote")
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


def _next_action(claim: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Stands in for the model's tool choice: read tools only, the order row beats the ticket.

    lookup_refund_history is skipped on purpose: the host caps per order anyway, and the
    shape must still send the full quote, so the extra step would only add cost.
    """
    if not claim["order_id"]:
        return {"final": {"status": "needs_info"}}
    if "lookup_order" not in facts:
        return {"tool": "lookup_order", "args": {"order_id": claim["order_id"], "principal_id": claim["principal_id"]}}
    order_view = facts["lookup_order"]
    if not order_view["visible"]:
        return {"final": {"status": "escalated"}}
    if "compute_compensation" not in facts:
        return {"tool": "compute_compensation", "args": {"order": order_view["order"]}}
    return {"final": {"status": "quoted"}}