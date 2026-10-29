"""Run the core service flows against PostgreSQL.

Skipped unless ORBITLINK_TEST_PG is set, e.g.
    ORBITLINK_TEST_PG=postgresql://orbitlink:orbitlink@localhost:5432/orbitlink pytest tests/test_postgres.py
(the database must have db/migrations/001_init.sql applied, as docker-compose does).
"""
import os
from datetime import timedelta

import pytest

from orbitlink import protocol as P
from orbitlink.api import DEFAULT_STATIONS
from orbitlink.ccsds import Deframer, SpacePacket, split_packets
from orbitlink.commands import CmdState, Priority
from orbitlink.db import Database
from orbitlink.service import GroundSystem

from .test_service_api import T0, hk, tm_frame

PG = os.environ.get("ORBITLINK_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="ORBITLINK_TEST_PG not set")

TABLES = ["timeline", "passes", "stations", "telemetry_points", "telemetry_frames", "limits", "alerts", "commands", "events"]


@pytest.fixture
def gs():
    db = Database(PG)
    db.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
    yield GroundSystem(db)
    db.close()


def test_pg_telemetry_and_alerts(gs):
    for k in range(3):
        gs.ingest(tm_frame(hk(batt_v=6.4 if k else 7.8), count=k), T0 + timedelta(seconds=k))
    assert gs.limit_states()["batt_v"] == "red"
    assert gs.db.one("SELECT COUNT(*) AS n FROM alerts WHERE severity = 'critical'")["n"] >= 1
    assert len(gs.history("batt_v")) == 3


def test_pg_command_cycle_and_reload(gs):
    cmd = gs.submit_command("set_mode", {"mode": "NOMINAL"}, Priority.CRITICAL, T0)
    (fr,) = Deframer().feed(gs.next_uplink(T0))
    (tc,) = split_packets(fr.data)
    ack = SpacePacket(P.APID_ACK, 0, P.encode_ack(P.Ack(P.APID_TC_CORE, tc.seq, P.Func.SET_MODE, 0, 0)))
    gs.ingest(tm_frame(ack), T0 + timedelta(seconds=2))
    row = gs.db.one("SELECT state, args_json FROM commands WHERE id = ?", (cmd.id,))
    assert row["state"] == CmdState.ACKED and row["args_json"] == {"mode": "NOMINAL"}
    gs.submit_command("noop", {}, Priority.BULK, T0)
    assert GroundSystem(gs.db).queue.depth() == 1


def test_pg_pass_planning(gs):
    for st in DEFAULT_STATIONS:
        gs.add_station(st)
    allp = gs.plan_passes(T0, hours=12)
    n_sched = sum(p.scheduled for p in allp)
    assert gs.db.one("SELECT COUNT(*) AS n FROM passes WHERE scheduled")["n"] == n_sched
    assert len(gs.timeline(T0, T0 + timedelta(hours=12))) == n_sched
