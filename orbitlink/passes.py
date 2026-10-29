"""Pass prediction (AOS/LOS/max elevation) and contact scheduling."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .orbit import SEC_PER_DAY, Station, datetime_to_jd, elevation, jd_to_datetime


@dataclass
class Pass:
    station: str
    aos_jd: float
    los_jd: float
    tca_jd: float
    max_el_deg: float
    scheduled: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return (self.los_jd - self.aos_jd) * SEC_PER_DAY

    @property
    def aos(self) -> datetime:
        return jd_to_datetime(self.aos_jd)

    @property
    def los(self) -> datetime:
        return jd_to_datetime(self.los_jd)

    def to_dict(self) -> dict:
        return {
            "station": self.station,
            "aos": self.aos.isoformat(),
            "los": self.los.isoformat(),
            "tca": jd_to_datetime(self.tca_jd).isoformat(),
            "duration_s": round(self.duration_s, 1),
            "max_el_deg": round(self.max_el_deg, 2),
            "scheduled": self.scheduled,
        }


def _refine(prop, st: Station, lo: float, hi: float, rising: bool, tol_s: float = 0.5) -> float:
    """Bisection for the mask crossing between jd lo and hi."""
    mask = st.min_el_deg
    while (hi - lo) * SEC_PER_DAY > tol_s:
        mid = 0.5 * (lo + hi)
        above = float(elevation(prop, st, np.array([mid]))[0]) >= mask
        if above == rising:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def predict_passes(prop, st: Station, start: datetime, end: datetime, step_s: float = 30.0) -> list[Pass]:
    """Find all passes above the station's elevation mask in [start, end)."""
    jd0, jd1 = datetime_to_jd(start), datetime_to_jd(end)
    grid = np.arange(jd0, jd1, step_s / SEC_PER_DAY)
    out: list[Pass] = []
    chunk = 20000
    els = np.concatenate([elevation(prop, st, grid[i : i + chunk]) for i in range(0, len(grid), chunk)])
    above = els >= st.min_el_deg
    edges = np.flatnonzero(np.diff(above.astype(np.int8)))
    # Handle a pass already in progress at start
    starts = [0] if above[0] else []
    ends = []
    for e in edges:
        if above[e + 1]:
            starts.append(e + 1)
        else:
            ends.append(e)
    if len(ends) < len(starts):
        ends.append(len(grid) - 1)
    for s_idx, e_idx in zip(starts, ends):
        aos = grid[s_idx] if s_idx == 0 else _refine(prop, st, grid[s_idx - 1], grid[s_idx], rising=True)
        los = grid[e_idx] if e_idx == len(grid) - 1 else _refine(prop, st, grid[e_idx], grid[e_idx + 1], rising=False)
        # max elevation on a fine grid around the coarse peak
        k = s_idx + int(np.argmax(els[s_idx : e_idx + 1]))
        fine = np.linspace(grid[max(k - 1, 0)], grid[min(k + 1, len(grid) - 1)], 61)
        fe = elevation(prop, st, fine)
        j = int(np.argmax(fe))
        truncated = s_idx == 0 or e_idx == len(grid) - 1
        out.append(Pass(st.name, float(aos), float(los), float(fine[j]), float(fe[j]), meta={"truncated": truncated}))
    return out


def elevation_profile(prop, st: Station, p: Pass, step_s: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (seconds since AOS, elevation deg) sampled over the pass."""
    n = max(2, int(p.duration_s // step_s) + 1)
    jd = np.linspace(p.aos_jd, p.los_jd, n)
    return (jd - p.aos_jd) * SEC_PER_DAY, elevation(prop, st, jd)


def schedule_contacts(passes: list[Pass], min_duration_s: float = 120.0, handover_s: float = 60.0) -> list[Pass]:
    """Select a non-overlapping set of contacts for a single spacecraft.

    Greedy by quality (max elevation x duration): a candidate is accepted if it
    does not overlap an already accepted contact (with a hand-over margin).
    Returns the accepted passes sorted by AOS; flags them ``scheduled``.
    """
    margin = handover_s / SEC_PER_DAY
    cands = [p for p in passes if p.duration_s >= min_duration_s]
    cands.sort(key=lambda p: p.max_el_deg * p.duration_s, reverse=True)
    accepted: list[Pass] = []
    for p in cands:
        if all(p.los_jd + margin <= q.aos_jd or p.aos_jd >= q.los_jd + margin for q in accepted):
            p.scheduled = True
            accepted.append(p)
    accepted.sort(key=lambda p: p.aos_jd)
    return accepted
