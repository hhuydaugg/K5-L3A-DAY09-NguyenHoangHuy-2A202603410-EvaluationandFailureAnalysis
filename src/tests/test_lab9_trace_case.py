"""Regression tests from docs/trace_case.md (C-002@11) and the team graders for FM-05 / FM-08.
Run: python -m pytest -q -m student -o addopts= -k lab9"""
import pytest

pytestmark = pytest.mark.student


def _ctx(trial, label):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=label, output=trial,
                            duration=0.0, _span_tree=None, attributes={}, metrics={})


def _grade(shape, claim, seed=11):
    from lab9.graders import GRADERS
    from lab9.trace_case import investigate

    case = investigate(shape, claim, seed)
    ctx = _ctx(case["trial"], case["label"])
    return case["trial"], {k: g().evaluate(ctx).value for k, g in GRADERS.items()}


def test_trace_case_c002_ticket_trust_caught_at_override_step():
    trial, got = _grade("seeded_regression", "C-002")
    kinds = [e["kind"] for e in trial["events"]]
    # First wrong step: the ticket's 5 days overwrite the DB's 2 days, before any quote or refund.
    assert kinds.index("model_override") < kinds.index("quote")
    assert got["db_fact_kept"] is False and got["refund_amount_correct"] is False and got["pass_rate"] is False


def test_trace_case_c002_fixed_control_passes():
    trial, got = _grade("fixed", "C-002")
    assert "model_override" not in [e["kind"] for e in trial["events"]]
    assert got["db_fact_kept"] and got["refund_amount_correct"] and got["pass_rate"]


def test_misreport_is_caught_only_by_reply_matches_ledger():
    from lab8.shapes import SHAPES
    from lab9.variant_agent import make_shape

    SHAPES.setdefault("t_misreport", make_shape("t_misreport", {"no_override", "misreport"}))
    _, got = _grade("t_misreport", "C-008")      # host caps the refund at 20.000đ, reply says 35.000đ
    assert got["pass_rate"] is True              # Lab 8's grader does not read the reply
    assert got["reply_matches_ledger"] is False


def _trial(text, refund_vnd, proposal=35000):
    return {"snapshot": {"refund_vnd": refund_vnd, "refund_count": int(refund_vnd > 0)}, "unexpected_rows": 0,
            "blob": "", "events": [{"kind": "reply", "payload": {"text": text}}] if text is not None else [],
            "result": {"proposal_amount": proposal}, "lab8_pass": True, "lab8_reason": None}


@pytest.mark.parametrize("text,ledger,ok", [
    ("Dạ, LogiX đã hoàn 35.000đ cho đơn O-107.", 35000, True),
    ("Dạ, LogiX đã hoàn 35.000đ cho đơn O-808.", 20000, False),
    ("Dạ, LogiX đã hoàn 120.000đ cho đơn O-515.", 0, False),
    ("Dạ, đơn O-202 chưa đủ điều kiện hoàn theo chính sách hiện hành.", 0, True),
    (None, 0, True),
])
def test_reply_matches_ledger(text, ledger, ok):
    from lab9.graders import ReplyMatchesLedger

    assert ReplyMatchesLedger().evaluate(_ctx(_trial(text, ledger), {})).value is ok


def test_proposal_correct_flags_self_deduction():
    from lab9.graders import ProposalCorrect

    label = {"proposal_amount": 35000}
    assert ProposalCorrect().evaluate(_ctx(_trial(None, 0, proposal=35000), label)).value is True
    assert ProposalCorrect().evaluate(_ctx(_trial(None, 0, proposal=0), label)).value is False
