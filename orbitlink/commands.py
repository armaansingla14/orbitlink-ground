"""Command queues.

Both policies use the same stop-and-wait link discipline (one command in
flight, acknowledged by an ACK telemetry packet carrying its sequence count).
They differ only in ordering and failure handling:

* :class:`FifoQueue` (baseline): strict arrival order. A command whose ACK does
  not arrive in time is resubmitted at the tail with a new sequence count, as
  an operator re-queueing it by hand would.
* :class:`PriorityRetryQueue`: priority classes with aging, automatic
  retransmission of the same sequence count (so the spacecraft can suppress
  duplicates) with a retry budget, and pass-awareness: a multi-frame command is
  not started if it cannot finish before LOS.
"""
from __future__ import annotations

import bisect
import itertools
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class Priority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    BULK = 3


class CmdState:
    QUEUED = "queued"
    SENT = "sent"
    ACKED = "acked"
    NACKED = "nacked"
    FAILED = "failed"
    EXPIRED = "expired"


TERMINAL = {CmdState.ACKED, CmdState.NACKED, CmdState.FAILED, CmdState.EXPIRED}


@dataclass(eq=False)  # identity semantics: queues hold and remove specific instances
class Command:
    id: int
    name: str
    func: int
    args: bytes
    priority: Priority
    submitted_at: float
    frames: int = 1                  # uplink frames needed (bulk uploads use many)
    deadline: Optional[float] = None
    state: str = CmdState.QUEUED
    seq: Optional[int] = None
    attempts: int = 0
    resubmissions: int = 0
    sent_at: Optional[float] = None
    tx_end: Optional[float] = None    # end of uplink transmission of the current attempt
    hold_until: float = 0.0          # retry budget exhausted: wait (normally for the next pass)
    acked_at: Optional[float] = None
    ack_status: Optional[int] = None
    history: list = field(default_factory=list)

    @property
    def turnaround(self) -> Optional[float]:
        return None if self.acked_at is None else self.acked_at - self.submitted_at


class SeqAllocator:
    def __init__(self, start: int = 1):
        self._it = itertools.count(start)

    def next(self) -> int:
        return next(self._it) & 0x3FFF


class CommandQueue:
    """Common machinery: in-flight tracking, ACK matching and timeouts."""

    policy = "base"

    def __init__(self, ack_timeout_s: float = 8.0, seq: SeqAllocator | None = None):
        self.ack_timeout_s = ack_timeout_s
        self.seq = seq or SeqAllocator()
        self.pending: list[Command] = []
        self._has_deadlines = False
        self.in_flight: Optional[Command] = None
        self.done: list[Command] = []
        self.by_seq: dict[int, Command] = {}

    # --- container hooks (overridden by ordered queues)
    def _add(self, cmd: Command, front: bool = False) -> None:
        if front:
            self.pending.insert(0, cmd)
        else:
            self.pending.append(cmd)

    def _remove(self, cmd: Command) -> None:
        self.pending.remove(cmd)

    def _contains(self, cmd: Command) -> bool:
        return cmd.state == CmdState.QUEUED and any(c is cmd for c in self.pending)

    def waiting(self) -> list[Command]:
        return list(self.pending)

    def depth(self) -> int:
        return len(self.waiting()) + (1 if self.in_flight else 0)

    def next_hold_release(self, now: float) -> Optional[float]:
        times = [c.hold_until for c in self.waiting() if c.hold_until > now]
        return min(times) if times else None

    # --- hooks for subclasses
    def _select(self, now: float, remaining_s: float, frame_time_s: float) -> Optional[Command]:
        raise NotImplementedError

    def _on_timeout(self, cmd: Command, now: float) -> None:
        raise NotImplementedError

    # --- public API
    def submit(self, cmd: Command) -> Command:
        cmd.state = CmdState.QUEUED
        self._add(cmd)
        if cmd.deadline is not None:
            self._has_deadlines = True
        return cmd

    def next_to_send(self, now: float, remaining_s: float = float("inf"), frame_time_s: float = 0.0) -> Optional[Command]:
        """Pick the next command to transmit (None if busy or nothing fits)."""
        self.expire(now)
        if self.in_flight is not None:
            return None
        cmd = self._select(now, remaining_s, frame_time_s)
        if cmd is None:
            return None
        self._remove(cmd)
        if cmd.seq is None:
            cmd.seq = self.seq.next()
        self.by_seq[cmd.seq] = cmd
        cmd.state = CmdState.SENT
        cmd.attempts += 1
        cmd.sent_at = now
        cmd.tx_end = now + cmd.frames * frame_time_s
        cmd.history.append(("sent", now, cmd.seq))
        self.in_flight = cmd
        return cmd

    def on_ack(self, seq: int, status: int, now: float) -> Optional[Command]:
        cmd = self.by_seq.get(seq)
        if cmd is None or cmd.state in TERMINAL:
            return None
        cmd.acked_at = now
        cmd.ack_status = status
        cmd.state = CmdState.ACKED if status == 0 else CmdState.NACKED
        cmd.history.append(("ack", now, status))
        if self._contains(cmd):  # late ACK for an attempt we already gave up on
            self._remove(cmd)
        if self.in_flight is cmd:
            self.in_flight = None
        self.done.append(cmd)
        return cmd

    def tick(self, now: float) -> Optional[Command]:
        """Handle an ACK timeout for the in-flight command."""
        cmd = self.in_flight
        if cmd is None or cmd.tx_end is None:
            return None
        if now - cmd.tx_end >= self.ack_timeout_s:
            self.in_flight = None
            cmd.history.append(("timeout", now))
            self._on_timeout(cmd, now)
            return cmd
        return None

    def link_lost(self, now: float) -> None:
        """LOS while a command is in flight: treat as a timeout."""
        if self.in_flight is not None:
            cmd = self.in_flight
            self.in_flight = None
            cmd.history.append(("los", now))
            self._on_timeout(cmd, now)

    def expire(self, now: float) -> None:
        if not self._has_deadlines:
            return
        for cmd in [c for c in self.waiting() if c.deadline is not None and now > c.deadline]:
            self._remove(cmd)
            cmd.state = CmdState.EXPIRED
            self.done.append(cmd)

    def snapshot(self) -> list[Command]:
        return ([self.in_flight] if self.in_flight else []) + self.waiting()


class FifoQueue(CommandQueue):
    policy = "fifo"

    def _select(self, now, remaining_s, frame_time_s):
        return self.pending[0] if self.pending else None

    def _on_timeout(self, cmd, now):
        # Operator resubmits at the back of the queue as a new command instance.
        cmd.state = CmdState.QUEUED
        cmd.seq = None
        cmd.resubmissions += 1
        self._add(cmd)


class PriorityRetryQueue(CommandQueue):
    policy = "priority_retry"

    def __init__(self, ack_timeout_s: float = 8.0, max_retries: int = 3, aging_s: float = 6 * 3600,
                 hold_s: float = 900.0, max_attempts: int = 20, seq: SeqAllocator | None = None):
        super().__init__(ack_timeout_s, seq)
        self.max_retries = max_retries      # immediate retransmissions per burst
        self.aging_s = aging_s
        self.hold_s = hold_s                # back-off after a failed burst
        self.max_attempts = max_attempts    # give up (FAILED) after this many attempts
        self._burst: dict[int, int] = {}
        self.retry: list[Command] = []      # immediate retransmissions, served first
        self._keys: list[float] = []        # sort keys parallel to self.pending

    # Aging adds (now - submitted)/aging_s to every waiting command alike, so the
    # ordering by effective priority equals ordering by this static key.
    def _key(self, cmd: Command) -> float:
        return float(cmd.priority) + cmd.submitted_at / self.aging_s

    def _add(self, cmd: Command, front: bool = False) -> None:
        if front:
            self.retry.insert(0, cmd)
            return
        k = self._key(cmd)
        i = bisect.bisect_right(self._keys, k)
        self._keys.insert(i, k)
        self.pending.insert(i, cmd)

    def _remove(self, cmd: Command) -> None:
        for i, c in enumerate(self.retry):
            if c is cmd:
                del self.retry[i]
                return
        k = self._key(cmd)
        i = bisect.bisect_left(self._keys, k)
        while self.pending[i] is not cmd:
            i += 1
        del self.pending[i]
        del self._keys[i]

    def _contains(self, cmd: Command) -> bool:
        return cmd.state == CmdState.QUEUED and (any(c is cmd for c in self.retry) or any(c is cmd for c in self.pending))

    def waiting(self) -> list[Command]:
        return self.retry + self.pending

    def effective_priority(self, cmd: Command, now: float) -> float:
        # One priority class of promotion per `aging_s` waited (prevents starvation).
        return float(cmd.priority) - (now - cmd.submitted_at) / self.aging_s

    def _select(self, now, remaining_s, frame_time_s):
        for lst in (self.retry, self.pending):
            for c in lst:
                if c.hold_until > now:
                    continue
                if frame_time_s > 0 and c.frames * frame_time_s + self.ack_timeout_s > remaining_s:
                    continue  # would not complete before LOS
                return c
        return None

    def _on_timeout(self, cmd, now):
        if cmd.attempts >= self.max_attempts:
            cmd.state = CmdState.FAILED
            self.done.append(cmd)
            return
        burst = self._burst.get(cmd.id, 0) + 1
        cmd.state = CmdState.QUEUED
        if burst <= self.max_retries:
            # Retransmit with the same sequence count, first in line.
            self._burst[cmd.id] = burst
            self._add(cmd, front=True)
        else:
            # Link looks bad: back off (usually until the next pass), keep the sequence count.
            self._burst[cmd.id] = 0
            cmd.hold_until = now + self.hold_s
            self._add(cmd)


def make_queue(policy: str, **kw) -> CommandQueue:
    if policy == "fifo":
        kw.pop("max_retries", None)
        kw.pop("aging_s", None)
        kw.pop("hold_s", None)
        kw.pop("max_attempts", None)
        return FifoQueue(**kw)
    if policy == "priority_retry":
        return PriorityRetryQueue(**kw)
    raise ValueError(policy)
