import pytest

from orbitlink.api import DEFAULT_STATIONS
from orbitlink.commands import CmdState
from orbitlink.linksim import LinkModel, TrafficModel, build_contact_plan, simulate, summarize


@pytest.fixture(scope="module")
def plan():
    return build_contact_plan(3, DEFAULT_STATIONS)


def test_link_model_monotonic():
    lm = LinkModel()
    m = [lm.margin_db(e) for e in (5, 10, 30, 60, 90)]
    assert m == sorted(m)
    assert lm.frame_error_rate(lm.margin_db(90)) < 1e-3 < lm.frame_error_rate(lm.margin_db(5))


def test_simulation_conserves_commands_and_is_deterministic(plan):
    a = simulate(plan, "priority_retry", TrafficModel(load=0.5), seed=3)
    b = simulate(plan, "priority_retry", TrafficModel(load=0.5), seed=3)
    assert [c.state for c in a.commands] == [c.state for c in b.commands]
    states = {c.state for c in a.commands}
    assert states <= {CmdState.ACKED, CmdState.FAILED, CmdState.QUEUED, CmdState.SENT}
    acked = [c for c in a.commands if c.state == CmdState.ACKED]
    assert acked and all(c.turnaround >= 0 for c in acked)


def test_policies_see_identical_traffic(plan):
    f = simulate(plan, "fifo", TrafficModel(load=0.5), seed=4)
    p = simulate(plan, "priority_retry", TrafficModel(load=0.5), seed=4)
    assert [(c.submitted_at, c.priority, c.frames) for c in f.commands] == \
           [(c.submitted_at, c.priority, c.frames) for c in p.commands]


def test_summary_rows(plan):
    r = simulate(plan, "fifo", TrafficModel(load=0.3), seed=5)
    rows = summarize(r, plan.end.timestamp())
    assert {row["class"] for row in rows} == {"ALL", "CRITICAL", "HIGH", "NORMAL", "BULK"}
    allrow = next(row for row in rows if row["class"] == "ALL")
    assert allrow["acked"] + allrow["failed"] + allrow["pending"] == allrow["submitted"]
