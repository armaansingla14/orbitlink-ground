"""Feed a running OrbitLink API with simulated spacecraft telemetry.

Acts as the front-end processor + spacecraft: pulls TC frames from
/api/link/uplink, executes them on a toy spacecraft model, and posts TM frames
(housekeeping, ACKs, events) to /api/telemetry/frames.

    uvicorn orbitlink.api:app --port 8000 &
    python sim/demo_feed.py --url http://localhost:8000 --seconds 120
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orbitlink import protocol as P  # noqa: E402
from orbitlink.ccsds import Deframer, Frame, SpacePacket, split_packets  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--period", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    cli = httpx.Client(base_url=args.url, timeout=5)
    cli.post("/api/passes/plan", params={"hours": 24})

    mode, boot, tm_count, k = 1, 1, 0, 0
    last_seq = None
    pending: list[SpacePacket] = []
    t_end = time.time() + args.seconds
    while time.time() < t_end:
        # uplink: execute any TC the ground releases
        up = cli.post("/api/link/uplink", json={}).json()
        if up["frame_hex"]:
            for fr in Deframer().feed(bytes.fromhex(up["frame_hex"])):
                for tc in split_packets(fr.data):
                    func, a = tc.data[0], tc.data[1:]
                    status = P.AckStatus.OK
                    if func == P.Func.SET_MODE and tc.seq != last_seq:
                        if a[0] == 2 and mode != 1:
                            status = P.AckStatus.REJECT_MODE
                        else:
                            mode = a[0]
                    last_seq = tc.seq
                    if rng.random() > 0.1:  # 10 % of ACKs lost on the downlink
                        pending.append(SpacePacket(P.APID_ACK, 0, P.encode_ack(P.Ack(tc.apid, tc.seq, func, status, 0))))
        # housekeeping with a slow battery cycle and an occasional dip
        load = {0: 0.30, 1: 0.37, 2: 1.27}[mode]
        voc = 7.9 + 0.15 * math.sin(k / 40) - (0.9 if 60 <= k % 200 < 75 else 0.0)
        hk = {"time_ms": k * 1000, "mode": mode, "batt_v": voc - 0.45 * load + rng.gauss(0, 0.01),
              "current_a": load + rng.gauss(0, 0.01), "temp_c": 18 + 6 * math.sin(k / 30) + rng.gauss(0, 0.2),
              "imu_flags": 0b1001, "gnss_fix": 1, "gnss_sats": 9, "boot_count": boot}
        if hk["batt_v"] < 6.9 and mode != 0:
            mode = 0
            pending.append(SpacePacket(P.APID_EVENT, 0, bytes(4) + (14).to_bytes(2, "big") + int(hk["batt_v"] * 1000).to_bytes(2, "big")))
        pkts = [SpacePacket(P.APID_HK, k & 0x3FFF, P.build_hk(hk))] + pending
        pending = []
        frame = Frame(P.SPACECRAFT_ID, 0, tm_count, b"".join(p.encode() for p in pkts)).encode()
        tm_count = (tm_count + 1) & 0xFF
        cli.post("/api/telemetry/frames", json={"hex": frame.hex()})
        if k % 25 == 5:
            cli.post("/api/commands", json={"name": "ping", "args": {"token": k}, "priority": "NORMAL"})
        k += 1
        time.sleep(args.period)
    print(f"sent {k} housekeeping frames")


if __name__ == "__main__":
    main()
