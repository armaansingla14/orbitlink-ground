"""Ground system core: telemetry ingestion, health monitoring, command
queueing with ACK tracking, pass planning, mission timeline, events and alerts.

All methods take an explicit ``now`` (UTC datetime) so that the same code can
be driven by the API (wall clock) or by simulations.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import protocol as P
from .ccsds import Deframer, Frame
from .commands import Command, CmdState, Priority, PriorityRetryQueue
from .db import Database
from .limits import Limit, LimitChecker
from .orbit import Elements, Station, datetime_to_jd, make_propagator
from .passes import Pass, predict_passes, schedule_contacts


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> float:
    return dt.timestamp()


class GroundSystem:
    def __init__(self, db: Database, elements: Optional[Elements] = None, ack_timeout_s: float = 8.0):
        self.db = db
        self.deframer = Deframer()
        self.limits = LimitChecker()
        self.queue = PriorityRetryQueue(ack_timeout_s=ack_timeout_s)
        self.latest_hk: dict[str, Any] = {}
        self.latest_hk_at: Optional[datetime] = None
        self.elements = elements or Elements.circular_sso(datetime(2026, 10, 1, tzinfo=timezone.utc), 525.0, 30.0)
        self.prop = make_propagator(self.elements)
        self.uplink_frame_count = 0
        self._load_limits()
        self._reload_queue()

    # ------------------------------------------------------------ events/alerts
    def log_event(self, category: str, name: str, detail: str = "", now: Optional[datetime] = None) -> int:
        now = now or utcnow()
        return self.db.execute("INSERT INTO events (t, category, name, detail) VALUES (?, ?, ?, ?)",
                               (now.isoformat(), category, name, detail))

    def raise_alert(self, severity: str, source: str, message: str, now: Optional[datetime] = None) -> int:
        now = now or utcnow()
        aid = self.db.execute(
            "INSERT INTO alerts (created_at, severity, source, message, acknowledged) VALUES (?, ?, ?, ?, ?)",
            (now.isoformat(), severity, source, message, False))
        self.log_event("alert", severity, f"{source}: {message}", now)
        return aid

    def ack_alert(self, alert_id: int, now: Optional[datetime] = None) -> bool:
        row = self.db.one("SELECT id, acknowledged FROM alerts WHERE id = ?", (alert_id,))
        if row is None:
            return False
        self.db.execute("UPDATE alerts SET acknowledged = ?, ack_at = ? WHERE id = ?",
                        (True, (now or utcnow()).isoformat(), alert_id))
        return True

    # ------------------------------------------------------------ limits
    def _load_limits(self) -> None:
        rows = self.db.query("SELECT * FROM limits")
        if not rows:
            for lim in self.limits.limits.values():
                self.save_limit(lim)
        else:
            for r in rows:
                self.limits.set_limit(Limit(r["param"], r["red_low"], r["yellow_low"], r["yellow_high"],
                                            r["red_high"], int(r["persistence"]), bool(r["enabled"])))

    def save_limit(self, lim: Limit) -> None:
        self.limits.set_limit(lim)
        self.db.execute("DELETE FROM limits WHERE param = ?", (lim.param,))
        self.db.execute(
            "INSERT INTO limits (param, red_low, yellow_low, yellow_high, red_high, persistence, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (lim.param, lim.red_low, lim.yellow_low, lim.yellow_high, lim.red_high, lim.persistence, lim.enabled))

    # ------------------------------------------------------------ telemetry
    def ingest(self, raw: bytes, now: Optional[datetime] = None) -> dict[str, Any]:
        """Feed raw downlink bytes; returns a summary of what was decoded."""
        now = now or utcnow()
        frames = self.deframer.feed(raw)
        summary = {"frames": 0, "hk": 0, "acks": 0, "events": 0, "alerts": 0, "bad_frames": self.deframer.stats.crc_errors}
        for fr in frames:
            summary["frames"] += 1
            self.db.execute(
                "INSERT INTO telemetry_frames (received_at, scid, vcid, frame_count, raw) VALUES (?, ?, ?, ?, ?)",
                (now.isoformat(), fr.scid, fr.vcid, fr.count, fr.data))
            if fr.scid != P.SPACECRAFT_ID:
                self.log_event("telemetry", "unknown_scid", hex(fr.scid), now)
                continue
            self._ingest_frame(fr, now, summary)
        return summary

    def _ingest_frame(self, fr: Frame, now: datetime, summary: dict[str, int]) -> None:
        from .ccsds import split_packets

        for pkt in split_packets(fr.data):
            if pkt.apid == P.APID_HK:
                hk = P.decode_hk(pkt.data)
                summary["hk"] += 1
                self._ingest_hk(hk, now, summary)
            elif pkt.apid == P.APID_ACK:
                ack = P.decode_ack(pkt.data)
                summary["acks"] += 1
                self.handle_ack(ack, now)
            elif pkt.apid == P.APID_EVENT:
                ev = P.decode_event(pkt.data)
                summary["events"] += 1
                self.log_event("spacecraft", ev["name"], f"arg={ev['arg']} obt_ms={ev['time_ms']}", now)
                sev = P.ALERT_EVENTS.get(ev["name"])
                if sev:
                    self.raise_alert(sev, "fdir", f"spacecraft event {ev['name']} (arg {ev['arg']})", now)
                    summary["alerts"] += 1

    def _ingest_hk(self, hk: dict[str, Any], now: datetime, summary: dict[str, int]) -> None:
        prev_mode = self.latest_hk.get("mode")
        prev_boot = self.latest_hk.get("boot_count")
        self.latest_hk, self.latest_hk_at = hk, now
        numeric = {k: float(v) for k, v in hk.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        self.db.executemany("INSERT INTO telemetry_points (t, param, value) VALUES (?, ?, ?)",
                            [(now.isoformat(), k, v) for k, v in numeric.items()])
        for tr in self.limits.check(numeric):
            msg = f"{tr.param}={tr.value:.3g} {tr.old_state} -> {tr.new_state}"
            self.log_event("limit", tr.param, msg, now)
            if tr.escalation:
                self.raise_alert("critical" if tr.new_state == "red" else "warning", "limits", msg, now)
                summary["alerts"] += 1
        if prev_mode is not None and hk["mode"] != prev_mode:
            self.log_event("spacecraft", "mode", f"{P.MODES.get(prev_mode)} -> {hk['mode_name']}", now)
            if hk["mode"] == 0:
                self.raise_alert("warning", "mode", "spacecraft entered SAFE mode", now)
                summary["alerts"] += 1
        if prev_boot is not None and hk["boot_count"] != prev_boot:
            self.raise_alert("critical", "reset", f"spacecraft reset detected (cause {hk['reset_cause_name']})", now)
            summary["alerts"] += 1

    def history(self, param: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT t, value FROM telemetry_points WHERE param = ? ORDER BY id DESC LIMIT ?",
                             (param, limit))
        return list(reversed(rows))

    def limit_states(self) -> dict[str, str]:
        return {p: self.limits.state.get(p, "nominal") for p in self.limits.limits}

    # ------------------------------------------------------------ commands
    def _reload_queue(self) -> None:
        for r in self.db.query("SELECT * FROM commands WHERE state IN ('queued', 'sent') ORDER BY id"):
            cmd = Command(r["id"], r["name"], r["func"], bytes.fromhex(r["args_hex"]), Priority(r["priority"]),
                          datetime.fromisoformat(r["created_at"]).timestamp())
            cmd.seq = r["seq"]
            cmd.attempts = r["attempts"]
            self.queue.submit(cmd)
            if cmd.seq is not None:
                self.queue.by_seq[cmd.seq] = cmd

    def submit_command(self, name: str, args: dict[str, Any], priority: Priority = Priority.NORMAL,
                       now: Optional[datetime] = None) -> Command:
        now = now or utcnow()
        func, arg_bytes = P.command_args(name, args)
        cid = self.db.execute(
            "INSERT INTO commands (created_at, name, func, args_json, args_hex, priority, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now.isoformat(), name.upper(), int(func), json.dumps(args), arg_bytes.hex(), int(priority), CmdState.QUEUED))
        cmd = Command(cid, name.upper(), int(func), arg_bytes, priority, _ts(now))
        self.queue.submit(cmd)
        self.log_event("command", "submitted", f"#{cid} {name.upper()} prio={priority.name}", now)
        return cmd

    def _persist(self, cmd: Command) -> None:
        def iso(t: Optional[float]) -> Optional[str]:
            return None if t is None else datetime.fromtimestamp(t, timezone.utc).isoformat()

        self.db.execute(
            "UPDATE commands SET state = ?, seq = ?, attempts = ?, sent_at = ?, acked_at = ?, ack_status = ? WHERE id = ?",
            (cmd.state, cmd.seq, cmd.attempts, iso(cmd.sent_at), iso(cmd.acked_at), cmd.ack_status, cmd.id))

    def next_uplink(self, now: Optional[datetime] = None, remaining_s: float = float("inf"),
                    frame_time_s: float = 0.0) -> Optional[bytes]:
        """Return the next uplink frame (or None). Call repeatedly while a pass is active."""
        now = now or utcnow()
        expired = self.queue.tick(_ts(now))
        if expired is not None:
            self._persist(expired)
            self.log_event("command", "ack_timeout", f"#{expired.id} seq={expired.seq}", now)
        cmd = self.queue.next_to_send(_ts(now), remaining_s, frame_time_s)
        if cmd is None:
            return None
        self._persist(cmd)
        pkt = P.build_tc(cmd.seq, cmd.func, cmd.args).encode()
        frame = Frame(P.SPACECRAFT_ID, 0, self.uplink_frame_count, pkt).encode()
        self.uplink_frame_count = (self.uplink_frame_count + 1) & 0xFF
        self.log_event("command", "uplinked", f"#{cmd.id} seq={cmd.seq} attempt={cmd.attempts}", now)
        return frame

    def handle_ack(self, ack: P.Ack, now: datetime) -> Optional[Command]:
        cmd = self.queue.on_ack(ack.tc_seq, ack.status, _ts(now))
        if cmd is None:
            self.log_event("command", "unmatched_ack", f"seq={ack.tc_seq} status={ack.status}", now)
            return None
        self._persist(cmd)
        name = "acked" if ack.ok else "nacked"
        self.log_event("command", name, f"#{cmd.id} seq={ack.tc_seq} status={ack.status}", now)
        if not ack.ok:
            self.raise_alert("warning", "commands", f"command #{cmd.id} {cmd.name} rejected (status {ack.status})", now)
        return cmd

    def commands(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM commands ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------ stations, passes, timeline
    def add_station(self, st: Station) -> int:
        return self.db.execute(
            "INSERT INTO stations (name, lat_deg, lon_deg, alt_m, min_el_deg) VALUES (?, ?, ?, ?, ?)",
            (st.name, st.lat_deg, st.lon_deg, st.alt_m, st.min_el_deg))

    def stations(self) -> list[Station]:
        return [Station(r["name"], r["lat_deg"], r["lon_deg"], r["alt_m"], r["min_el_deg"])
                for r in self.db.query("SELECT * FROM stations ORDER BY id")]

    def plan_passes(self, start: datetime, hours: float = 24.0) -> list[Pass]:
        """Predict passes for all stations, schedule contacts and write passes + timeline."""
        end = start + timedelta(hours=hours)
        ids = {r["name"]: r["id"] for r in self.db.query("SELECT id, name FROM stations")}
        allp: list[Pass] = []
        for st in self.stations():
            allp.extend(predict_passes(self.prop, st, start, end))
        schedule_contacts(allp)
        for p in sorted(allp, key=lambda p: p.aos_jd):
            d = p.to_dict()
            existing = self.db.one("SELECT id FROM passes WHERE station_id = ? AND aos = ?", (ids[p.station], d["aos"]))
            if existing:
                continue
            pid = self.db.execute(
                "INSERT INTO passes (station_id, aos, los, tca, max_el_deg, duration_s, scheduled) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ids[p.station], d["aos"], d["los"], d["tca"], d["max_el_deg"], d["duration_s"], p.scheduled))
            if p.scheduled:
                self.add_timeline(p.aos, p.los, "contact", f"{p.station} pass, max el {d['max_el_deg']} deg", pid)
        self.log_event("planning", "passes", f"{len(allp)} predicted, {sum(p.scheduled for p in allp)} scheduled")
        return allp

    def passes(self, start: Optional[datetime] = None, limit: int = 200) -> list[dict[str, Any]]:
        start = start or utcnow() - timedelta(hours=1)
        return self.db.query(
            "SELECT p.*, s.name AS station FROM passes p JOIN stations s ON s.id = p.station_id "
            "WHERE p.los >= ? ORDER BY p.aos LIMIT ?", (start.isoformat(), limit))

    def add_timeline(self, start: datetime, end: datetime, kind: str, title: str, ref_id: Optional[int] = None) -> int:
        if end < start:
            raise ValueError("timeline entry ends before it starts")
        return self.db.execute("INSERT INTO timeline (start_t, end_t, kind, title, ref_id) VALUES (?, ?, ?, ?, ?)",
                               (start.isoformat(), end.isoformat(), kind, title, ref_id))

    def timeline(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM timeline WHERE end_t >= ? AND start_t <= ? ORDER BY start_t",
                             (start.isoformat(), end.isoformat()))

    def current_contact(self, now: datetime) -> Optional[dict[str, Any]]:
        return self.db.one(
            "SELECT p.*, s.name AS station FROM passes p JOIN stations s ON s.id = p.station_id "
            "WHERE p.scheduled = ? AND p.aos <= ? AND p.los >= ? ORDER BY p.aos LIMIT 1",
            (True, now.isoformat(), now.isoformat()))

    def ground_track(self, now: datetime) -> dict[str, float]:
        from .orbit import subpoint

        lat, lon, alt = subpoint(self.prop, datetime_to_jd(now))
        return {"lat_deg": lat, "lon_deg": lon, "alt_km": alt}

    def status(self, now: Optional[datetime] = None) -> dict[str, Any]:
        now = now or utcnow()
        open_alerts = self.db.one("SELECT COUNT(*) AS n FROM alerts WHERE acknowledged = ?", (False,))["n"]
        return {
            "time": now.isoformat(),
            "latest_hk_at": self.latest_hk_at.isoformat() if self.latest_hk_at else None,
            "mode": self.latest_hk.get("mode_name"),
            "open_alerts": open_alerts,
            "queue_depth": len(self.queue.pending) + (1 if self.queue.in_flight else 0),
            "limit_states": self.limit_states(),
            "contact": self.current_contact(now),
            "elements": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in asdict(self.elements).items()},
            "propagator": self.prop.name,
        }
