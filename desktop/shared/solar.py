"""solar.py — sunrise / sunset / civil twilight for the show scheduler (#954).

The NOAA Solar Calculator equations (Meeus, *Astronomical Algorithms*), pure
Python, no network and no dependency. Accuracy is about ±1 minute at
mid-latitudes, which is what an unattended "15 minutes before sunset" needs.

All results are timezone-aware UTC datetimes; the scheduler converts to local
wall-clock time itself. Longitude is degrees EAST positive (Toronto is -79.38).
"""

import math
from datetime import date as _date, datetime, timedelta, timezone

SUNRISE_ZENITH = 90.833      # geometric horizon + refraction + solar radius
CIVIL_ZENITH = 96.0          # civil twilight: sun 6° below the horizon


def _jd(d):
    """Julian day at 0h UT of calendar date *d*."""
    return d.toordinal() + 1721424.5


def _t(jd):
    return (jd - 2451545.0) / 36525.0


def _mean_long(t):
    return (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0


def _mean_anom(t):
    return 357.52911 + t * (35999.05029 - 0.0001537 * t)


def _ecc(t):
    return 0.016708634 - t * (0.000042037 + 0.0000001267 * t)


def _eq_center(t):
    m = math.radians(_mean_anom(t))
    return (math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
            + math.sin(2 * m) * (0.019993 - 0.000101 * t)
            + math.sin(3 * m) * 0.000289)


def _app_long(t):
    omega = 125.04 - 1934.136 * t
    return _mean_long(t) + _eq_center(t) - 0.00569 - 0.00478 * math.sin(math.radians(omega))


def _obliquity(t):
    seconds = 21.448 - t * (46.8150 + t * (0.00059 - t * 0.001813))
    e0 = 23.0 + (26.0 + seconds / 60.0) / 60.0
    return e0 + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * t))


def _declination(t):
    s = math.sin(math.radians(_obliquity(t))) * math.sin(math.radians(_app_long(t)))
    return math.degrees(math.asin(s))


def _eq_time(t):
    """Equation of time, minutes."""
    eps = math.radians(_obliquity(t))
    l0 = math.radians(_mean_long(t))
    e = _ecc(t)
    m = math.radians(_mean_anom(t))
    y = math.tan(eps / 2) ** 2
    et = (y * math.sin(2 * l0) - 2 * e * math.sin(m)
          + 4 * e * y * math.sin(m) * math.cos(2 * l0)
          - 0.5 * y * y * math.sin(4 * l0) - 1.25 * e * e * math.sin(2 * m))
    return math.degrees(et) * 4.0


def _hour_angle(lat, dec, zenith):
    """Hour angle (radians) at *zenith*, or None when the sun never reaches
    it that day (polar day / night)."""
    la, de = math.radians(lat), math.radians(dec)
    arg = (math.cos(math.radians(zenith)) / (math.cos(la) * math.cos(de))
           - math.tan(la) * math.tan(de))
    if arg < -1.0 or arg > 1.0:
        return None
    return math.acos(arg)


def _event_minutes(d, lat, lon, zenith, rising):
    """Minutes after 0h UT of *d* at which the event happens, or None."""
    jd = _jd(d)

    def once(minutes):
        t = _t(jd + minutes / 1440.0)
        ha = _hour_angle(lat, _declination(t), zenith)
        if ha is None:
            return None
        delta = lon + (math.degrees(ha) if rising else -math.degrees(ha))
        return 720.0 - 4.0 * delta - _eq_time(t)

    first = once(720.0 - 4.0 * lon)          # start from local solar noon
    if first is None:
        return None
    return once(first)                        # one refinement is plenty


def _at(d, minutes):
    if minutes is None:
        return None
    base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes)


def sun_times(d, lat, lon):
    """All four events for calendar date *d* at (lat, lon), as UTC datetimes
    (None where the event doesn't happen that day):
    ``{"sunrise", "sunset", "civilDawn", "civilDusk"}``."""
    if not isinstance(d, _date):
        raise TypeError("d must be a date")
    return {
        "sunrise": _at(d, _event_minutes(d, lat, lon, SUNRISE_ZENITH, True)),
        "sunset": _at(d, _event_minutes(d, lat, lon, SUNRISE_ZENITH, False)),
        "civilDawn": _at(d, _event_minutes(d, lat, lon, CIVIL_ZENITH, True)),
        "civilDusk": _at(d, _event_minutes(d, lat, lon, CIVIL_ZENITH, False)),
    }


def sunrise_sunset(d, lat, lon):
    """(sunrise_utc, sunset_utc) for date *d*."""
    s = sun_times(d, lat, lon)
    return s["sunrise"], s["sunset"]
