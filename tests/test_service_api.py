from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from orbitlink import protocol as P
from orbitlink.api import create_app
from orbitlink.ccsds import Deframer, Frame, SpacePacket, split_packets
from orbitlink.commands import CmdState, Priority
from orbitlink.db import Database
from orbitlink.service import GroundSystem

T0 = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def tm_frame(*packets: SpacePacket, count: int = 0) -> bytes:
    return Frame(P.SPACECRAFT_ID, 0, count, b"".join(p.encode() for p in packets)).encode()


def hk(**kw) -> SpacePacket:
    base = {"mode": 1, "batt_v": 7.8, "current_a": 0.4, "temp_c": 20.0, "boot_count": 1}
    base.update(kw)
    return SpacePacket(P.APID_HK, 0, P.build_hk(base))


@pytest.fixture
def gs():
    return GroundSystem(Database(":memory:"))


def test_ingest_hk_limits_and_alerts(gs):
    s = gs.ingest(tm_frame(hk()), T0)
    assert s["hk"] == 1 and s["alerts"] == 0
    for k in range(2):  # batt below red low for two samples (persistence 2)
        gs.ingest(tm_frame(hk(batt_v=6.4), count=k + 1), T0 + timedelta(seconds=k + 1))
    assert gs.limit_states()["batt_v"] == "red"
    alerts = gs.db.query("SELECT * FROM alerts")
    assert any(a["severity"] == "critical" and "batt_v" in a["message"] for a in alerts)
    assert len(gs.history("batt_v")) == 3


def test_mode_change_reset_and_event_alerts(gs):
    gs.ingest(tm_frame(hk(mode=2)), T0)
    ev = SpacePacket(P.APID_EVENT, 0, bytes.fromhex("000003e8") + (14).to_bytes(2, "big") + (6700).to_bytes(2, "big"))
    gs.ingest(tm_frame(hk(mode=0, boot_count=2), ev), T0 + timedelta(seconds=1))
    msgs = [a["message"] for a in gs.db.query("SELECT message FROM alerts")]
    assert any("SAFE" in m for m in msgs)
    assert any("reset detected" in m for m in msgs)
    assert any("undervoltage" in m for m in msgs)
    assert gs.db.one("SELECT COUNT(*) AS n FROM events WHERE name = 'undervoltage'")["n"] == 1


def test_command_uplink_ack_cycle(gs):
    cmd = gs.submit_command("ping", {"token": 42}, Priority.HIGH, T0)
    frame = gs.next_uplink(T0)
    d = Deframer()
    (fr,) = d.feed(frame)
    (tc,) = split_packets(fr.data)
    assert tc.apid == P.APID_TC_CORE and tc.data[0] == P.Func.PING
    assert gs.next_uplink(T0 + timedelta(seconds=1)) is None  # stop-and-wait
    ack = SpacePacket(P.APID_ACK, 0, P.encode_ack(P.Ack(P.APID_TC_CORE, tc.seq, P.Func.PING, 0, 42)))
    gs.ingest(tm_frame(ack), T0 + timedelta(seconds=2))
    row = gs.db.one("SELECT * FROM commands WHERE id = ?", (cmd.id,))
    assert row["state"] == CmdState.ACKED and row["attempts"] == 1


def test_command_timeout_retransmits_same_seq_and_nack_alerts(gs):
    gs.submit_command("noop", {}, Priority.NORMAL, T0)
    f1 = gs.next_uplink(T0)
    f2 = gs.next_uplink(T0 + timedelta(seconds=20))  # ack timeout -> retry
    seq1 = split_packets(Deframer().feed(f1)[0].data)[0].seq
    seq = split_packets(Deframer().feed(f2)[0].data)[0].seq
    assert seq == seq1
    nack = SpacePacket(P.APID_ACK, 0, P.encode_ack(P.Ack(P.APID_TC_CORE, seq, P.Func.NOOP, P.AckStatus.REJECT_MODE, 0)))
    gs.ingest(tm_frame(nack), T0 + timedelta(seconds=21))
    assert gs.db.one("SELECT state FROM commands")["state"] == CmdState.NACKED
    assert gs.db.one("SELECT COUNT(*) AS n FROM alerts WHERE source = 'commands'")["n"] == 1


def test_queue_survives_restart(tmp_path):
    db = Database(str(tmp_path / "ol.db"))
    gs1 = GroundSystem(db)
    gs1.submit_command("capture", {"frames": 3}, Priority.BULK, T0)
    gs2 = GroundSystem(Database(str(tmp_path / "ol.db")))
    assert gs2.queue.depth() == 1 and gs2.queue.waiting()[0].args == b"\x03"


def test_plan_passes_and_timeline(gs):
    from orbitlink.api import DEFAULT_STATIONS

    for st in DEFAULT_STATIONS:
        gs.add_station(st)
    allp = gs.plan_passes(T0, hours=24)
    sched = [p for p in allp if p.scheduled]
    assert len(allp) >= 10 and 0 < len(sched) <= len(allp)
    tl = gs.timeline(T0, T0 + timedelta(hours=24))
    assert len(tl) == len(sched) and all(t["kind"] == "contact" for t in tl)
    mid = sched[0].aos + (sched[0].los - sched[0].aos) / 2
    assert gs.current_contact(mid)["station"] == sched[0].station
    gs.plan_passes(T0, hours=24)  # idempotent
    assert len(gs.timeline(T0, T0 + timedelta(hours=24))) == len(sched)


def test_api_end_to_end():
    client = TestClient(create_app(Database(":memory:")))
    assert client.get("/api/health").json()["ok"]
    assert len(client.get("/api/stations").json()) == 3
    r = client.post("/api/telemetry/frames", json={"hex": tm_frame(hk(batt_v=7.9, mode=2)).hex()})
    assert r.status_code == 200 and r.json()["hk"] == 1
    latest = client.get("/api/telemetry/latest").json()
    assert latest["hk"]["mode_name"] == "PAYLOAD"
    assert client.post("/api/telemetry/frames", json={"hex": "zz"}).status_code == 400

    r = client.post("/api/commands", json={"name": "set_mode", "args": {"mode": "SAFE"}, "priority": "critical"})
    assert r.status_code == 201
    assert client.post("/api/commands", json={"name": "bogus"}).status_code == 422
    assert client.post("/api/commands", json={"name": "noop", "priority": "urgent"}).status_code == 422
    up = client.post("/api/link/uplink", json={}).json()
    assert up["frame_hex"].startswith("1acffc1d")
    assert client.get(f"/api/commands/{r.json()['id']}").json()["state"] == "sent"

    assert client.put("/api/limits/temp_c", json={"yellow_high": 30, "red_high": 40}).status_code == 200
    assert any(row["param"] == "temp_c" and row["red_high"] == 40 for row in client.get("/api/limits").json())

    plan = client.post("/api/passes/plan", params={"hours": 12, "start": "2026-10-02T00:00:00Z"}).json()
    assert plan["predicted"] >= plan["scheduled"] > 0
    assert client.post("/api/stations", json={"name": "toronto", "lat_deg": 0, "lon_deg": 0}).status_code == 409
    assert client.post("/api/timeline", json={"start": "2026-10-02T01:00:00Z", "end": "2026-10-02T00:00:00Z",
                                              "kind": "payload", "title": "bad"}).status_code == 422
    alerts = client.get("/api/alerts").json()
    if alerts:
        assert client.post(f"/api/alerts/{alerts[0]['id']}/ack").status_code == 200
    assert client.post("/api/alerts/99999/ack").status_code == 404
    gt = client.get("/api/groundtrack").json()
    assert -90 <= gt["lat_deg"] <= 90 and 400 < gt["alt_km"] < 650
