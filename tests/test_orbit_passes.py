import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from orbitlink.orbit import (HAVE_SGP4, Elements, KeplerPropagator, Sgp4Propagator, Station, datetime_to_jd,
                             elevation, jd_to_datetime, look_angles, subpoint, teme_to_ecef)
from orbitlink.passes import elevation_profile, predict_passes, schedule_contacts

EPOCH = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_time_roundtrip():
    dt = datetime(2026, 10, 29, 12, 30, tzinfo=timezone.utc)
    assert abs((jd_to_datetime(datetime_to_jd(dt)) - dt).total_seconds()) < 1e-3
    assert datetime_to_jd(datetime(2000, 1, 1, 12, tzinfo=timezone.utc)) == pytest.approx(2451545.0)


def test_sso_inclination_and_period():
    el = Elements.circular_sso(EPOCH, 525.0)
    assert 97.3 < el.inc_deg < 97.7
    assert el.period_s / 60 == pytest.approx(95.1, abs=0.2)


def test_kepler_radius_and_station_overhead():
    el = Elements.circular_sso(EPOCH, 525.0, ecc=0.0)
    kp = KeplerPropagator(el)
    jd = datetime_to_jd(EPOCH) + np.linspace(0, 1, 50)
    r = np.linalg.norm(kp.positions_teme(jd), axis=1)
    assert np.allclose(r, el.a_km, atol=1e-6)
    # a station directly under the satellite sees it at ~90 deg elevation
    lat, lon, _ = subpoint(kp, jd[10])
    st = Station("under", lat, lon)
    sat = teme_to_ecef(kp.positions_teme(jd[10:11]), jd[10:11])
    _, elv, rng = look_angles(sat, st)
    # subpoint() is geocentric while Station is geodetic: up to ~0.2 deg of latitude offset
    assert elv[0] > 87.0 and rng[0] == pytest.approx(525, abs=30)


@pytest.mark.skipif(not HAVE_SGP4, reason="sgp4 not installed")
def test_sgp4_and_kepler_agree_over_one_orbit():
    el = Elements.circular_sso(EPOCH, 525.0)
    el.bstar = 0.0  # no drag, to compare against two-body + J2
    sp, kp = Sgp4Propagator(el), KeplerPropagator(el)
    jd = datetime_to_jd(EPOCH) + np.linspace(0, el.period_s / 86400, 20)
    d = np.linalg.norm(sp.positions_teme(jd) - kp.positions_teme(jd), axis=1)
    # SGP4 mean elements (Kozai/Brouwer, short-period J2 terms) differ from pure two-body + secular J2
    assert d.max() < 60.0


@pytest.mark.skipif(not HAVE_SGP4, reason="sgp4 not installed")
def test_sgp4_from_tle():
    # ISS TLE (epoch 2008) from the sgp4 package documentation
    l1 = "1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927"
    l2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537"
    sp = Sgp4Propagator(tle=(l1, l2))
    r = sp.positions_teme(np.array([2454731.0]))
    assert 6600 < np.linalg.norm(r) < 6800


def test_pass_prediction_geometry():
    el = Elements.circular_sso(EPOCH, 525.0)
    kp = KeplerPropagator(el)
    st = Station("svalbard", 78.23, 15.41, 450, 5.0)
    passes = predict_passes(kp, st, EPOCH, EPOCH + timedelta(days=1))
    assert 8 <= len(passes) <= 16  # high-latitude station sees most SSO orbits
    for p in passes:
        if p.meta["truncated"]:
            continue  # pass in progress at the window edge
        assert p.aos_jd < p.tca_jd < p.los_jd
        assert 60 < p.duration_s < 20 * 60
        e = elevation(kp, st, np.array([p.aos_jd, p.los_jd]))
        assert np.allclose(e, st.min_el_deg, atol=0.2)
        t, prof = elevation_profile(kp, st, p)
        assert prof.max() <= p.max_el_deg + 0.05


def test_schedule_contacts_no_overlap():
    el = Elements.circular_sso(EPOCH, 525.0)
    kp = KeplerPropagator(el)
    stations = [Station("a", 64.8, -147.7), Station("b", 67.9, 21.1), Station("c", 78.2, 15.4, min_el_deg=5)]
    allp = [p for st in stations for p in predict_passes(kp, st, EPOCH, EPOCH + timedelta(days=2))]
    sched = schedule_contacts(allp, handover_s=60)
    assert 0 < len(sched) < len(allp)
    for a, b in zip(sched, sched[1:]):
        assert (b.aos_jd - a.los_jd) * 86400 >= 60 - 1e-6
    assert all(p.duration_s >= 120 for p in sched)
