"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    for who, rep in (("PR", pr), ("baseline", base)):
        problems += [f"{who} metric {k} = {v} (NaN/inf hides a failure: nan < min is False)"
                     for k, v in rep["metrics"].items() if isinstance(v, float) and not math.isfinite(v)]
    rows, man = pr["rows"], pr["manifest"]
    pairs = [(r["item_id"], r["seed"]) for r in rows]
    base_pairs = {(r["item_id"], r["seed"]) for r in base["rows"]}
    if len(rows) != man["items"] * len(man["seeds"]):
        problems.append(f"{len(rows)} rows, manifest says {man['items']} items x {len(man['seeds'])} seeds")
    if len(set(pairs)) != len(pairs):
        problems.append(f"{len(pairs) - len(set(pairs))} duplicated (item, seed) row(s)")
    if set(pairs) != base_pairs:
        problems.append(f"(item, seed) set differs from the baseline's: {len(base_pairs - set(pairs))} missing, "
                        f"{len(set(pairs) - base_pairs)} extra (dropped rows or cherry-picked seeds/items)")
    if man["items"] != base["manifest"]["items"] or sorted(man["seeds"]) != sorted(base["manifest"]["seeds"]):
        problems.append("manifest items/seeds differ from the baseline's")
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s): a crashed grader is not a pass")
    graded = [m["id"] for m in spec["metrics"] if m["id"] in GRADERS]
    for mid in graded:
        n = sum(r.get(mid) is None for r in rows)
        if n:
            problems.append(f"{n} row(s) with no value for graded metric {mid} (dropped out of the mean)")
    # Metrics must be what the rows say: a hand-edited number is caught here (cost is not re-derivable).
    redo = aggregate([{**r, "cost": r.get("cost", 0.0)} for r in rows], [m for m in graded if all(m in r for r in rows)],
                     k_values=spec.get("pass_k", []))
    for k, v in redo.items():
        got = pr["metrics"].get(k)
        if k != "cost_per_success" and k in pr["metrics"] and v is not None and isinstance(got, (int, float))                 and math.isfinite(got) and abs(got - v) > 1e-4:
            problems.append(f"metric {k} = {got} but the rows give {v} (report edited by hand)")
    base_slices = {r["item_id"]: sorted(r.get("slices") or []) for r in base["rows"]}
    relabelled = sorted({r["item_id"] for r in rows
                         if r["item_id"] in base_slices and sorted(r.get("slices") or []) != base_slices[r["item_id"]]})
    if relabelled:
        problems.append(f"slice tags differ from the baseline's for {', '.join(relabelled)}")
    if base_spec is not None and base_spec.get("eval_set_version") == spec.get("eval_set_version"):
        problems += ratchet(spec, base_spec)
    return problems


def _looser(new: dict, old: dict, what: str) -> list[str]:
    """Threshold `new` may not be laxer than `old` (same eval-set version)."""
    out = []
    for key in ("max", "max_increase"):
        if key in old and (key not in new or new[key] > old[key]):
            out.append(f"{what}: {key} {old[key]} -> {new.get(key)}")
    if "min" in old and ("min" not in new or new["min"] < old["min"]):
        out.append(f"{what}: min {old['min']} -> {new.get('min')}")
    if old.get("no_significant_drop") and not new.get("no_significant_drop"):
        out.append(f"{what}: no_significant_drop removed")
    return out


def ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Same eval-set version: every threshold, blocking flag, metric and slice may only get stricter."""
    out = []
    new_m = {m["id"]: m for m in spec["metrics"]}
    for m in base_spec["metrics"]:
        n = new_m.get(m["id"])
        if n is None:
            out.append(f"RATCHET metric {m['id']} removed")
            continue
        if m.get("blocking") and not n.get("blocking"):
            out.append(f"RATCHET metric {m['id']} no longer blocking")
        out += [f"RATCHET {p}" for p in _looser(n.get("threshold") or {}, m.get("threshold") or {}, m["id"])]
    new_s = {(s["name"], s["metric"]): s for s in spec.get("slices") or []}
    for s in base_spec.get("slices") or []:
        n = new_s.get((s["name"], s["metric"]))
        sid = f"slice:{s['name']}/{s['metric']}"
        if n is None:
            out.append(f"RATCHET {sid} removed")
            continue
        if s.get("blocking") and not n.get("blocking"):
            out.append(f"RATCHET {sid} no longer blocking")
        out += [f"RATCHET {p}" for p in _looser(n, s, sid)]
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        val, verdict, note = None, "missing", "no rows carry this slice"   # an empty slice gates nothing
        if a:
            val = round(sum(a.values()) / len(a), 4)
            verdict = "fail" if "min" in sl and val < sl["min"] else "ok"
            note = f"{len(a)} item"
            keys = sorted(set(a) & set(b))
            if sl.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        th = {k: sl[k] for k in ("min", "no_significant_drop") if k in sl}
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        lines.append(f"  {sid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
