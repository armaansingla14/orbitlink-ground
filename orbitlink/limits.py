"""Telemetry limit checking with yellow/red bands and persistence filtering."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Limit:
    param: str
    red_low: Optional[float] = None
    yellow_low: Optional[float] = None
    yellow_high: Optional[float] = None
    red_high: Optional[float] = None
    persistence: int = 1  # consecutive violating samples before a state change is reported
    enabled: bool = True

    def classify(self, value: float) -> str:
        if self.red_low is not None and value < self.red_low:
            return "red"
        if self.red_high is not None and value > self.red_high:
            return "red"
        if self.yellow_low is not None and value < self.yellow_low:
            return "yellow"
        if self.yellow_high is not None and value > self.yellow_high:
            return "yellow"
        return "nominal"


DEFAULT_LIMITS = [
    Limit("batt_v", red_low=6.6, yellow_low=7.0, yellow_high=8.3, red_high=8.5, persistence=2),
    Limit("current_a", yellow_high=1.4, red_high=1.8, persistence=2),
    Limit("temp_c", red_low=-20, yellow_low=-10, yellow_high=45, red_high=60, persistence=3),
    Limit("tc_crc_errors", yellow_high=50, red_high=200),
    Limit("task_stale", yellow_high=0.5, persistence=1),
]

_SEVERITY = {"nominal": 0, "yellow": 1, "red": 2}


@dataclass
class LimitTransition:
    param: str
    value: float
    old_state: str
    new_state: str

    @property
    def escalation(self) -> bool:
        return _SEVERITY[self.new_state] > _SEVERITY[self.old_state]


@dataclass
class LimitChecker:
    limits: dict[str, Limit] = field(default_factory=lambda: {lim.param: lim for lim in DEFAULT_LIMITS})
    state: dict[str, str] = field(default_factory=dict)
    _pending: dict[str, tuple[str, int]] = field(default_factory=dict)

    def set_limit(self, lim: Limit) -> None:
        self.limits[lim.param] = lim

    def check(self, sample: dict[str, float]) -> list[LimitTransition]:
        """Evaluate a decoded HK sample; returns confirmed state transitions."""
        out: list[LimitTransition] = []
        for name, lim in self.limits.items():
            if not lim.enabled or name not in sample:
                continue
            value = float(sample[name])
            cls = lim.classify(value)
            cur = self.state.get(name, "nominal")
            if cls == cur:
                self._pending.pop(name, None)
                continue
            pend_cls, count = self._pending.get(name, (cls, 0))
            count = count + 1 if pend_cls == cls else 1
            # Returning to nominal is reported immediately; escalations need persistence.
            needed = 1 if _SEVERITY[cls] < _SEVERITY[cur] else lim.persistence
            if count >= needed:
                self.state[name] = cls
                self._pending.pop(name, None)
                out.append(LimitTransition(name, value, cur, cls))
            else:
                self._pending[name] = (cls, count)
        return out
