"""Orbit propagation and ground-station geometry.

Two propagators share one interface (``positions_teme(jd_array) -> (N, 3) km``):

* :class:`Sgp4Propagator` wraps the ``sgp4`` package (initialised from mean
  elements via ``sgp4init``, or from a TLE).
* :class:`KeplerPropagator` is a two-body propagator with J2 secular drift of
  RAAN and argument of perigee, used as a fallback and as a cross-check.

Frames: positions are TEME/ECI (treated as identical here), rotated to ECEF
with GMST (IAU-82). Polar motion and nutation are ignored, which is far below
the accuracy needed for pass scheduling at a 10 degree mask.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

MU_EARTH = 398600.4418  # km^3/s^2
R_EARTH = 6378.137  # km (WGS84)
F_EARTH = 1 / 298.257223563
J2 = 1.08262668e-3
SEC_PER_DAY = 86400.0

try:  # optional dependency
    from sgp4.api import WGS72, Satrec  # type: ignore

    HAVE_SGP4 = True
except ImportError:  # pragma: no cover - exercised only without sgp4 installed
    HAVE_SGP4 = False


# ------------------------------------------------------------------ time
def datetime_to_jd(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() / SEC_PER_DAY + 2440587.5


def jd_to_datetime(jd: float) -> datetime:
    return datetime.fromtimestamp((jd - 2440587.5) * SEC_PER_DAY, tz=timezone.utc)


def gmst_rad(jd: np.ndarray | float) -> np.ndarray:
    """Greenwich mean sidereal time (IAU-82), radians."""
    t = (np.asarray(jd) - 2451545.0) / 36525.0
    gmst_s = 67310.54841 + (876600.0 * 3600 + 8640184.812866) * t + 0.093104 * t**2 - 6.2e-6 * t**3
    return np.mod(gmst_s % SEC_PER_DAY / 240.0 * math.pi / 180.0, 2 * math.pi)


# ------------------------------------------------------------------ elements
@dataclass
class Elements:
    """Mean Keplerian elements at epoch (angles in degrees)."""

    epoch: datetime
    a_km: float
    ecc: float
    inc_deg: float
    raan_deg: float
    argp_deg: float
    mean_anomaly_deg: float
    bstar: float = 0.0

    @property
    def mean_motion_rad_s(self) -> float:
        return math.sqrt(MU_EARTH / self.a_km**3)

    @property
    def period_s(self) -> float:
        return 2 * math.pi / self.mean_motion_rad_s

    @classmethod
    def circular_sso(cls, epoch: datetime, alt_km: float, ltan_raan_deg: float = 0.0, ecc: float = 0.0012) -> "Elements":
        """Sun-synchronous orbit at the given altitude (inclination from the J2 condition)."""
        a = R_EARTH + alt_km
        n = math.sqrt(MU_EARTH / a**3)
        # RAAN rate must equal 360 deg / tropical year
        omega_dot = 2 * math.pi / (365.2422 * SEC_PER_DAY)
        cos_i = -omega_dot / (1.5 * n * J2 * (R_EARTH / a) ** 2)
        inc = math.degrees(math.acos(max(-1.0, min(1.0, cos_i))))
        return cls(epoch, a, ecc, inc, ltan_raan_deg, 90.0, 0.0, bstar=2.0e-5)


class KeplerPropagator:
    name = "kepler"

    def __init__(self, el: Elements):
        self.el = el
        self.jd0 = datetime_to_jd(el.epoch)
        n = el.mean_motion_rad_s
        p = el.a_km * (1 - el.ecc**2)
        k = 1.5 * n * J2 * (R_EARTH / p) ** 2
        ci = math.cos(math.radians(el.inc_deg))
        self.raan_dot = -k * ci
        self.argp_dot = k * (2 - 2.5 * (1 - ci**2))
        self.n = n

    def positions_teme(self, jd: np.ndarray) -> np.ndarray:
        el = self.el
        dt = (np.asarray(jd, dtype=float) - self.jd0) * SEC_PER_DAY
        m = math.radians(el.mean_anomaly_deg) + self.n * dt
        e = el.ecc
        ecc_anom = m.copy()
        for _ in range(8):  # Newton iterations on Kepler's equation
            ecc_anom -= (ecc_anom - e * np.sin(ecc_anom) - m) / (1 - e * np.cos(ecc_anom))
        nu = 2 * np.arctan2(math.sqrt(1 + e) * np.sin(ecc_anom / 2), math.sqrt(1 - e) * np.cos(ecc_anom / 2))
        r = el.a_km * (1 - e * np.cos(ecc_anom))
        raan = math.radians(el.raan_deg) + self.raan_dot * dt
        argp = math.radians(el.argp_deg) + self.argp_dot * dt
        inc = math.radians(el.inc_deg)
        u = argp + nu
        cr, sr = np.cos(raan), np.sin(raan)
        cu, su = np.cos(u), np.sin(u)
        ci, si = math.cos(inc), math.sin(inc)
        x = r * (cr * cu - sr * su * ci)
        y = r * (sr * cu + cr * su * ci)
        z = r * (su * si)
        return np.stack([x, y, z], axis=-1)


class Sgp4Propagator:
    name = "sgp4"

    def __init__(self, el: Elements | None = None, tle: tuple[str, str] | None = None):
        if not HAVE_SGP4:
            raise RuntimeError("sgp4 package not installed")
        if tle is not None:
            self.sat = Satrec.twoline2rv(tle[0], tle[1])
        elif el is not None:
            self.sat = Satrec()
            epoch_days = datetime_to_jd(el.epoch) - 2433281.5  # days since 1949-12-31 00:00 UT
            self.sat.sgp4init(
                WGS72, "i", 99001, epoch_days, el.bstar, 0.0, 0.0, el.ecc,
                math.radians(el.argp_deg), math.radians(el.inc_deg), math.radians(el.mean_anomaly_deg),
                el.mean_motion_rad_s * 60.0, math.radians(el.raan_deg),
            )
        else:
            raise ValueError("need elements or a TLE")

    def positions_teme(self, jd: np.ndarray) -> np.ndarray:
        jd = np.atleast_1d(np.asarray(jd, dtype=float))
        whole = np.floor(jd)
        err, r, _v = self.sat.sgp4_array(whole, jd - whole)
        if np.any(err):
            raise RuntimeError(f"sgp4 error codes: {set(err[err != 0].tolist())}")
        return r


def make_propagator(el: Elements, prefer: str = "sgp4"):
    if prefer == "sgp4" and HAVE_SGP4:
        return Sgp4Propagator(el)
    return KeplerPropagator(el)


# ------------------------------------------------------------------ geometry
@dataclass
class Station:
    name: str
    lat_deg: float
    lon_deg: float
    alt_m: float = 0.0
    min_el_deg: float = 10.0

    def ecef(self) -> np.ndarray:
        lat, lon = math.radians(self.lat_deg), math.radians(self.lon_deg)
        e2 = F_EARTH * (2 - F_EARTH)
        n = R_EARTH / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = self.alt_m / 1000.0
        return np.array([
            (n + h) * math.cos(lat) * math.cos(lon),
            (n + h) * math.cos(lat) * math.sin(lon),
            (n * (1 - e2) + h) * math.sin(lat),
        ])


def teme_to_ecef(r: np.ndarray, jd: np.ndarray) -> np.ndarray:
    th = gmst_rad(jd)
    c, s = np.cos(th), np.sin(th)
    x = c * r[..., 0] + s * r[..., 1]
    y = -s * r[..., 0] + c * r[..., 1]
    return np.stack([x, y, r[..., 2]], axis=-1)


def look_angles(sat_ecef: np.ndarray, st: Station) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Azimuth (deg, from north), elevation (deg) and slant range (km)."""
    lat, lon = math.radians(st.lat_deg), math.radians(st.lon_deg)
    d = sat_ecef - st.ecef()
    e = -math.sin(lon) * d[..., 0] + math.cos(lon) * d[..., 1]
    n = (-math.sin(lat) * math.cos(lon) * d[..., 0] - math.sin(lat) * math.sin(lon) * d[..., 1]
         + math.cos(lat) * d[..., 2])
    u = (math.cos(lat) * math.cos(lon) * d[..., 0] + math.cos(lat) * math.sin(lon) * d[..., 1]
         + math.sin(lat) * d[..., 2])
    rng = np.sqrt(e**2 + n**2 + u**2)
    el = np.degrees(np.arcsin(u / rng))
    az = np.mod(np.degrees(np.arctan2(e, n)), 360.0)
    return az, el, rng


def elevation(prop, st: Station, jd: np.ndarray) -> np.ndarray:
    jd = np.atleast_1d(np.asarray(jd, dtype=float))
    return look_angles(teme_to_ecef(prop.positions_teme(jd), jd), st)[1]


def subpoint(prop, jd: float) -> tuple[float, float, float]:
    """Geocentric latitude, longitude (deg) and altitude (km) of the satellite."""
    r = teme_to_ecef(prop.positions_teme(np.array([jd])), np.array([jd]))[0]
    rn = float(np.linalg.norm(r))
    return math.degrees(math.asin(r[2] / rn)), math.degrees(math.atan2(r[1], r[0])), rn - R_EARTH


def add_seconds(dt: datetime, s: float) -> datetime:
    return dt + timedelta(seconds=s)
