"""Command turnaround experiment: naive FIFO vs priority + retry queueing.

Builds a contact plan from SGP4 pass predictions over three ground stations,
then replays identical command traffic and channel noise through both queue
policies at several offered loads and seeds.

    python sim/run_turnaround.py --days 55 --seeds 3 --loads 0.25 0.4 0.55 0.7
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orbitlink.api import DEFAULT_STATIONS  # noqa: E402
from orbitlink.linksim import LinkModel, TrafficModel, build_contact_plan, simulate, summarize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=55.0)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--loads", type=float, nargs="+", default=[0.25, 0.4, 0.55, 0.7])
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(exist_ok=True)

    t0 = time.time()
    plan = build_contact_plan(args.days, DEFAULT_STATIONS)
    t_plan = time.time() - t0
    link = LinkModel()
    durations = np.array([p.duration_s for p in plan.passes]) / 60
    max_els = np.array([p.max_el_deg for p in plan.passes])
    per_station = {s.name: sum(p.station == s.name for p in plan.passes) for s in DEFAULT_STATIONS}
    print(f"contact plan: {plan.predicted} predicted passes, {len(plan.passes)} scheduled contacts "
          f"({plan.contact_seconds / 3600:.1f} h) in {t_plan:.1f} s")

    rows, extra = [], []
    for load in args.loads:
        for seed in range(1, args.seeds + 1):
            for policy in ("fifo", "priority_retry"):
                t = time.time()
                res = simulate(plan, policy, TrafficModel(load=load), link, seed=seed)
                rows += summarize(res, plan.end.timestamp())
                extra.append({"policy": policy, "load": load, "seed": seed, "commands": len(res.commands),
                              "attempts": res.attempts, "frames_sent": res.frames_sent,
                              "duplicate_executions": res.duplicate_executions, "passes": res.passes,
                              "wall_s": round(time.time() - t, 2)})
                print(f"  load={load} seed={seed} {policy:15s} cmds={len(res.commands)} "
                      f"dups={res.duplicate_executions} ({time.time() - t:.1f} s)")

    with open(out / "turnaround_by_class.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(out / "turnaround_runs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(extra[0]))
        w.writeheader()
        w.writerows(extra)

    # ---- markdown summary: pooled over seeds
    def pooled(policy, load, cls, key, agg=np.mean):
        vals = [r[key] for r in rows if r["policy"] == policy and r["load"] == load and r["class"] == cls]
        return agg(vals)

    md = ["# Command turnaround: FIFO vs priority + retry (simulated)", "",
          f"Command: `python sim/run_turnaround.py --days {args.days:g} --seeds {args.seeds} "
          f"--loads {' '.join(f'{x:g}' for x in args.loads)}`", "",
          f"- Contact plan: {plan.predicted} predicted passes over {args.days:g} days at 3 stations "
          f"({', '.join(f'{k}: {v}' for k, v in per_station.items())} scheduled), "
          f"{len(plan.passes)} scheduled contacts after de-confliction, {plan.contact_seconds / 3600:.1f} h of contact.",
          f"- Contact duration median {np.median(durations):.1f} min, max elevation median {np.median(max_els):.1f} deg.",
          f"- Link: {link.frame_time_s:g} s per TC frame, ACK timeout {link.ack_timeout_s:g} s, "
          f"frame loss from a slant-range/elevation margin model with per-pass fading.",
          "- Turnaround = submission to ACK received, in minutes. Values are means over seeds of per-run statistics; "
          "commands submitted in the final 24 h are excluded.", ""]
    for load in args.loads:
        md += [f"## Offered load {load:g}", "",
               "| Class | Policy | Submitted | ACKed | Failed | Pending | Mean (min) | P50 (min) | P95 (min) | Attempts/cmd |",
               "|---|---|---|---|---|---|---|---|---|---|"]
        for cls in ("ALL", "CRITICAL", "HIGH", "NORMAL", "BULK"):
            for policy in ("fifo", "priority_retry"):
                md.append(f"| {cls} | {policy} | {pooled(policy, load, cls, 'submitted', np.sum):.0f} | "
                          f"{pooled(policy, load, cls, 'acked', np.sum):.0f} | {pooled(policy, load, cls, 'failed', np.sum):.0f} | "
                          f"{pooled(policy, load, cls, 'pending', np.sum):.0f} | {pooled(policy, load, cls, 'mean_min'):.1f} | "
                          f"{pooled(policy, load, cls, 'p50_min'):.1f} | {pooled(policy, load, cls, 'p95_min'):.1f} | "
                          f"{pooled(policy, load, cls, 'attempts_per_cmd'):.2f} |")
        dups = {p: sum(e["duplicate_executions"] for e in extra if e["policy"] == p and e["load"] == load)
                for p in ("fifo", "priority_retry")}
        md += ["", f"Duplicate executions on board (all seeds): fifo {dups['fifo']}, "
                   f"priority_retry {dups['priority_retry']}.", ""]
    md.append(f"Total wall time: {time.time() - t0:.0f} s.")
    (out / "turnaround_summary.md").write_text("\n".join(md) + "\n")
    print(f"wrote {out}/turnaround_summary.md")


if __name__ == "__main__":
    main()
