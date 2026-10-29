"""Mission protocol: APIDs, telecommand catalogue, telemetry packet layouts.

Mirrors ``include/fsw/cmd/protocol.hpp`` in the flight software repository.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from .ccsds import PacketType, SpacePacket

SPACECRAFT_ID = 0x2C5
APID_TC_CORE = 0x064
APID_HK = 0x010
APID_ACK = 0x011
APID_EVENT = 0x012


class Func(IntEnum):
    NOOP = 0x01
    SET_MODE = 0x02
    SET_PARAM = 0x03
    CAPTURE = 0x04
    RESET_COUNTERS = 0x05
    POWER_RAIL = 0x06
    PING = 0x07


class AckStatus(IntEnum):
    OK = 0
    REJECT_MODE = 2
    REJECT_ARG = 3
    UNKNOWN_FUNC = 4
    REJECT_GUARD = 5


MODES = {0: "SAFE", 1: "NOMINAL", 2: "PAYLOAD"}
MODE_IDS = {v: k for k, v in MODES.items()}
RAILS = ["sensors", "imu_a", "imu_b", "gnss", "modem", "payload"]

EVENTS = {
    1: "boot",
    2: "mode_change",
    10: "imu_switch",
    11: "imu_fault",
    12: "i2c_recover",
    13: "i2c_power_cycle",
    14: "undervoltage",
    15: "task_restart",
    16: "gnss_power_cycle",
    17: "modem_power_cycle",
    18: "watchdog_escalation",
    19: "tc_crc_error",
}
# Events that should raise an operator alert, with severity.
ALERT_EVENTS = {
    "undervoltage": "critical",
    "watchdog_escalation": "critical",
    "imu_fault": "warning",
    "i2c_power_cycle": "warning",
    "task_restart": "warning",
    "modem_power_cycle": "info",
    "gnss_power_cycle": "info",
}
RESET_CAUSES = {0: "power_on", 1: "watchdog", 2: "brownout", 3: "fdir", 4: "commanded"}

FDIR_FLAGS = [
    "undervoltage",
    "imu_a_degraded",
    "imu_b_degraded",
    "i2c_fault",
    "gnss_fault",
    "modem_fault",
    "task_fault",
    "i2c_failed",
]

# Housekeeping layout: (name, struct format, offset, scale)
HK_FIELDS: list[tuple[str, str, int, float]] = [
    ("time_ms", "I", 0, 1),
    ("mode", "B", 4, 1),
    ("batt_v", "H", 5, 1e-3),
    ("current_a", "H", 7, 1e-3),
    ("temp_c", "h", 9, 1e-2),
    ("imu_flags", "B", 11, 1),
    ("gnss_fix", "B", 12, 1),
    ("gnss_sats", "B", 13, 1),
    ("lat_deg", "i", 14, 1e-7),
    ("lon_deg", "i", 18, 1e-7),
    ("tc_accepted", "H", 22, 1),
    ("tc_rejected", "H", 24, 1),
    ("tc_crc_errors", "H", 26, 1),
    ("boot_count", "H", 28, 1),
    ("reset_cause", "B", 30, 1),
    ("fdir_flags", "H", 31, 1),
    ("modem_state", "B", 33, 1),
    ("payload_tx", "H", 34, 1),
    ("task_stale", "H", 36, 1),
]
HK_LEN = 38


def decode_hk(data: bytes) -> dict[str, Any]:
    if len(data) < HK_LEN:
        raise ValueError("housekeeping packet too short")
    out: dict[str, Any] = {}
    for name, fmt, off, scale in HK_FIELDS:
        (v,) = struct.unpack_from(">" + fmt, data, off)
        out[name] = v * scale if scale != 1 else v
    out["mode_name"] = MODES.get(out["mode"], "?")
    out["imu_attitude_valid"] = bool(out["imu_flags"] & 8)
    out["imu_active"] = "B" if out["imu_flags"] & 4 else "A"
    out["fdir_active"] = [n for i, n in enumerate(FDIR_FLAGS) if out["fdir_flags"] & (1 << i)]
    out["reset_cause_name"] = RESET_CAUSES.get(out["reset_cause"], "?")
    return out


@dataclass
class Ack:
    tc_apid: int
    tc_seq: int
    func: int
    status: int
    extra: int

    @property
    def ok(self) -> bool:
        return self.status == AckStatus.OK


def decode_ack(data: bytes) -> Ack:
    if len(data) < 10:
        raise ValueError("ack packet too short")
    apid, seq, func, status, extra = struct.unpack_from(">HHBBI", data)
    return Ack(apid, seq, func, status, extra)


def encode_ack(ack: Ack) -> bytes:
    return struct.pack(">HHBBI", ack.tc_apid, ack.tc_seq, ack.func, ack.status, ack.extra)


def decode_event(data: bytes) -> dict[str, Any]:
    t, eid, arg = struct.unpack_from(">IHH", data)
    return {"time_ms": t, "id": eid, "name": EVENTS.get(eid, f"event_{eid}"), "arg": arg}


# ------------------------------------------------------------------ telecommands
def command_args(name: str, args: dict[str, Any]) -> tuple[Func, bytes]:
    """Translate an operator command (name + JSON args) into function code + argument bytes."""
    key = name.upper()
    if key not in Func.__members__:
        raise ValueError(f"unknown command {name!r}")
    func = Func[key]
    if func == Func.SET_MODE:
        mode = args.get("mode")
        mode_id = MODE_IDS.get(str(mode).upper()) if isinstance(mode, str) else mode
        if mode_id not in MODES:
            raise ValueError("mode must be SAFE, NOMINAL or PAYLOAD")
        return func, bytes([mode_id])
    if func == Func.SET_PARAM:
        return func, struct.pack(">Bi", int(args["id"]), int(args["value"]))
    if func == Func.CAPTURE:
        frames = int(args.get("frames", 1))
        if not 1 <= frames <= 255:
            raise ValueError("frames must be 1..255")
        return func, bytes([frames])
    if func == Func.POWER_RAIL:
        rail = args["rail"]
        rail_id = RAILS.index(rail) if isinstance(rail, str) else int(rail)
        return func, bytes([rail_id, 1 if args.get("on", True) else 0])
    if func == Func.PING:
        return func, struct.pack(">I", int(args.get("token", 0)) & 0xFFFFFFFF)
    return func, b""


def build_tc(seq: int, func: int, arg_bytes: bytes) -> SpacePacket:
    return SpacePacket(apid=APID_TC_CORE, seq=seq, data=bytes([func]) + arg_bytes, ptype=PacketType.TC)


def build_hk(fields: dict[str, float]) -> bytes:
    """Encode a housekeeping data field (used by tests and the link simulator)."""
    buf = bytearray(HK_LEN)
    for name, fmt, off, scale in HK_FIELDS:
        v = fields.get(name, 0)
        struct.pack_into(">" + fmt, buf, off, int(round(v / scale)) if scale != 1 else int(v))
    return bytes(buf)
