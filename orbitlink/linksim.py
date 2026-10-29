"""Pass/link simulator used to compare command queueing policies.

Passes come from the real pass predictor (SGP4 orbit, three ground stations,
contact de-confliction). Inside each contact the uplink is a stop-and-wait
channel: a command is radiated as ``frames`` TC frames, the spacecraft model
executes it (suppressing duplicates by sequence count, like the flight
software does) and answers with an ACK on the downlink. Frame losses follow a
link-margin model that depends on slant range and elevation, plus per-pass
fading and per-frame scintillation.

Every model parameter is a field of :class:`LinkModel` / :class:`TrafficModel`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from .commands import Command, CmdState, Priority, make_queue
from .orbit import R_EARTH, SEC_PER_DAY, Elements, Station, make_propagator
from .passes import Pass, elevation_profile, predict_passes, schedule_contacts


@dataclass
class LinkModel:
    frame_time_s: float = 2.0         # 256-byte TC frame at ~1.2 kbit/s
    ack_delay_s: float = 1.0          # on-board processing + next TM frame
    ack_jitter_s: float = 0.4
    ack_timeout_s: float = 6.0
    base_margin_db: float = 18.0      # margin at zenith before atmospheric loss
    atm_loss_db: float = 0.5          # scaled by 1/sin(el)
    fer50_db: float = 3.0             # margin at which frame error rate = 50%
    fer_slope_db: float = 1.0
    pass_fade_sigma_db: float = 2.0
    frame_scint_sigma_db: float = 1.0
    alt_km: float = 525.0

    def margin_db(self, el_deg: float) -> float:
        el = math.radians(max(el_deg, 0.5))
        rs = R_EARTH + self.alt_km
        # slant range from elevation (spherical Earth)
        rng = -R_EARTH * math.sin(el) + math.sqrt((R_EARTH * math.sin(el)) ** 2 + rs**2 - R_EARTH**2)
        return self.base_margin_db - 20 * math.log10(rng / self.alt_km) - self.atm_loss_db / math.sin(el)

    def frame_error_rate(self, margin_db: float) -> float:
        return 1.0 / (1.0 + math.exp((margin_db - self.fer50_db) / self.fer_slope_db))


@dataclass
class TrafficModel:
    load: float = 0.6                 # offered load as a fraction of contact capacity
    mix: dict = field(default_factory=lambda: {
        Priority.CRITICAL: 0.10, Priority.HIGH: 0.20, Priority.NORMAL: 0.50, Priority.BULK: 0.20})
    normal_frames: tuple[int, int] = (1, 3)
    bulk_frames: tuple[int, int] = (10, 40)

    def mean_frames(self) -> float:
        return (self.mix[Priority.CRITICAL] + self.mix[Priority.HIGH]
                + self.mix[Priority.NORMAL] * sum(self.normal_frames) / 2
                + self.mix[Priority.BULK] * sum(self.bulk_frames) / 2)


@dataclass
class ContactPlan:
    passes: list[Pass]
    predicted: int
    profiles: list[tuple[np.ndarray, np.ndarray]]
    start: datetime
    end: datetime

    @property
    def contact_seconds(self) -> float:
        return sum(p.duration_s for p in self.passes)


def build_contact_plan(days: float, stations: list[Station], start: Optional[datetime] = None,
                       prefer: str = "sgp4") -> ContactPlan:
    start = start or datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=days)
    prop = make_propagator(Elements.circular_sso(start, 525.0, 30.0), prefer)
    allp: list[Pass] = []
    for st in stations:
        allp.extend(predict_passes(prop, st, start, end))
    sched = schedule_contacts(allp)
    by_name = {s.name: s for s in stations}
    profiles = [elevation_profile(prop, by_name[p.station], p, step_s=10.0) for p in sched]
    return ContactPlan(sched, len(allp), profiles, start, end)


@dataclass
class SimResult:
    policy: str
    load: float
    seed: int
    commands: list[Command]
    duplicate_executions: int
    attempts: int
    frames_sent: int
    passes: int


class _Spacecraft:
    """Executes TCs, suppressing a retransmission of the last executed sequence count."""

    def __init__(self) -> None:
        self.last_seq: Optional[int] = None
        self.executed: dict[int, int] = {}  # command id -> executions
        self.duplicates = 0

    def receive(self, cmd: Command) -> None:
        if cmd.seq == self.last_seq:
            return  # duplicate of the last command: re-ACK only
        self.last_seq = cmd.seq
        n = self.executed.get(cmd.id, 0) + 1
        self.executed[cmd.id] = n
        if n > 1:
            self.duplicates += 1


def _generate_traffic(plan: ContactPlan, traffic: TrafficModel, link: LinkModel, rng: np.random.Generator) -> list[Command]:
    horizon = (plan.end - plan.start).total_seconds()
    per_cmd_s = traffic.mean_frames() * link.frame_time_s + link.ack_delay_s
    capacity = plan.contact_seconds / per_cmd_s
    n = rng.poisson(traffic.load * capacity)
    t0 = plan.start.timestamp()
    times = np.sort(rng.uniform(0, horizon, n)) + t0
    prios = list(traffic.mix)
    probs = np.array([traffic.mix[p] for p in prios])
    cmds = []
    for i, t in enumerate(times):
        pr = prios[int(rng.choice(len(prios), p=probs))]
        if pr == Priority.BULK:
            frames = int(rng.integers(traffic.bulk_frames[0], traffic.bulk_frames[1] + 1))
        elif pr == Priority.NORMAL:
            frames = int(rng.integers(traffic.normal_frames[0], traffic.normal_frames[1] + 1))
        else:
            frames = 1
        cmds.append(Command(i + 1, pr.name, 0x01, b"", pr, float(t), frames=frames))
    return cmds


def simulate(plan: ContactPlan, policy: str, traffic: TrafficModel, link: LinkModel = LinkModel(),
             seed: int = 1) -> SimResult:
    """Run one campaign. Traffic and channel randomness depend only on ``seed``,
    so different policies see identical arrivals and identical per-pass fading."""
    traffic_rng = np.random.default_rng(seed)
    cmds = _generate_traffic(plan, traffic, link, traffic_rng)
    chan = np.random.default_rng(seed + 1_000_003)
    fades = chan.normal(0, link.pass_fade_sigma_db, len(plan.passes))

    q = make_queue(policy, ack_timeout_s=link.ack_timeout_s)
    sc = _Spacecraft()
    idx = 0
    attempts = frames_sent = 0

    for p, (prof_t, prof_el), fade in zip(plan.passes, plan.profiles, fades):
        aos = p.aos_jd
        aos_ts = (aos - 2440587.5) * SEC_PER_DAY
        los_ts = aos_ts + p.duration_s

        def el_at(ts: float) -> float:
            return float(np.interp(ts - aos_ts, prof_t, prof_el))

        def frame_ok(ts: float) -> bool:
            m = link.margin_db(el_at(ts)) + fade + chan.normal(0, link.frame_scint_sigma_db)
            return chan.random() >= link.frame_error_rate(m)

        t = aos_ts
        while t < los_ts:
            while idx < len(cmds) and cmds[idx].submitted_at <= t:
                q.submit(cmds[idx])
                idx += 1
            q.tick(t)
            cmd = q.next_to_send(t, remaining_s=los_ts - t, frame_time_s=link.frame_time_s)
            if cmd is None:
                if q.in_flight is not None:
                    t = q.in_flight.tx_end + link.ack_timeout_s
                    continue
                nxt = [cmds[idx].submitted_at] if idx < len(cmds) else []
                rel = q.next_hold_release(t)
                if rel is not None:
                    nxt.append(rel)
                t = min(nxt) if nxt else los_ts
                if t >= los_ts:
                    break
                continue
            attempts += 1
            tx_end = t + cmd.frames * link.frame_time_s
            ok = tx_end <= los_ts
            for k in range(cmd.frames):
                frames_sent += 1
                if not frame_ok(t + k * link.frame_time_s):
                    ok = False
            if ok:
                sc.receive(cmd)
                t_ack = tx_end + link.ack_delay_s + chan.uniform(0, link.ack_jitter_s)
                if t_ack < los_ts and frame_ok(t_ack):
                    q.on_ack(cmd.seq, 0, t_ack)
                    t = t_ack
                    continue
            t = min(tx_end + link.ack_timeout_s, los_ts)
            q.tick(t)
        q.link_lost(los_ts)

    # commands that arrived after the last pass are still pending
    while idx < len(cmds):
        q.submit(cmds[idx])
        idx += 1
    return SimResult(policy, traffic.load, seed, cmds, sc.duplicates, attempts, frames_sent, len(plan.passes))


def summarize(res: SimResult, horizon_end: float) -> list[dict]:
    """Per-priority-class turnaround statistics (minutes)."""
    rows = []
    groups = {"ALL": res.commands}
    for pr in Priority:
        groups[pr.name] = [c for c in res.commands if c.priority == pr or c.name == pr.name]
    for name, cs in groups.items():
        # Only commands submitted early enough to have had a chance (exclude the last 24 h).
        eligible = [c for c in cs if c.submitted_at <= horizon_end - SEC_PER_DAY]
        done = [c for c in eligible if c.state == CmdState.ACKED]
        ta = np.array([c.turnaround for c in done]) / 60.0 if done else np.array([np.nan])
        rows.append({
            "policy": res.policy, "load": res.load, "seed": res.seed, "class": name,
            "submitted": len(eligible), "acked": len(done),
            "failed": sum(c.state == CmdState.FAILED for c in eligible),
            "pending": sum(c.state in (CmdState.QUEUED, CmdState.SENT) for c in eligible),
            "mean_min": float(np.nanmean(ta)), "p50_min": float(np.nanpercentile(ta, 50)),
            "p95_min": float(np.nanpercentile(ta, 95)),
            "attempts_per_cmd": float(np.mean([c.attempts for c in eligible])) if eligible else float("nan"),
        })
    return rows
