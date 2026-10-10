"""Where the sun is, worked out locally (NOAA's solar equations - no service, no library).

Used by the Sun connection (a device with sunrise / sunset events) and by automations ("at sunset",
"only when it's dark"). Times are for the home's spot: the Home place first, else the Weather
connection's location. `use_store` is called once at startup with the database; tests call
`set_location` instead.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

# Zenith angles: the sun's edge on the horizon (with refraction), and civil twilight (6° below).
ZENITH = {"sunrise": 90.833, "sunset": 90.833, "dawn": 96.0, "dusk": 96.0}
EVENTS = ("dawn", "sunrise", "sunset", "dusk")
MORNING = ("dawn", "sunrise")

_source: Callable[[], tuple[float, float] | None] | None = None
_fixed: tuple[float, float] | None = None
_tz: tzinfo | None = None  # None = the computer's own time zone (what the scheduler's clock uses)


def set_location(lat: float | None, lon: float | None, tz: tzinfo | None = None) -> None:
    """Pin the location (and optionally the time zone) - for tests, or None to go back to the store."""
    global _fixed, _tz
    _fixed = (float(lat), float(lon)) if lat is not None and lon is not None else None
    _tz = tz


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def use_store(store: Any) -> None:
    """Read the home's spot from the database: the Home place, else the Weather connection's."""
    global _source

    def read() -> tuple[float, float] | None:
        try:
            saved = store.settings().get("homePlace") or {}
            if _num(saved.get("lat")) is not None and _num(saved.get("lon")) is not None:
                return float(saved["lat"]), float(saved["lon"])
            for rec in store.integrations():
                if rec.get("type") == "weather":
                    lat, lon = _num(rec["config"].get("latitude")), _num(rec["config"].get("longitude"))
                    if lat is not None and lon is not None:
                        return lat, lon
        except Exception:
            return None
        return None

    _source = read


def location() -> tuple[float, float] | None:
    if _fixed:
        return _fixed
    return _source() if _source else None


def _local(t: datetime) -> datetime:
    """An aware time in the home's time zone (the computer's own unless pinned)."""
    return t.astimezone(_tz) if _tz else t.astimezone()


# -- the maths -------------------------------------------------------------------------------

def _solar(jd: float) -> tuple[float, float]:
    """(declination in degrees, equation of time in minutes) at a Julian date."""
    jc = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc)) + math.sin(2 * mr) * (0.019993 - 0.000101 * jc)
         + math.sin(3 * mr) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * jc)
    app_long = l0 + c - 0.00569 - 0.00478 * math.sin(omega)
    obliq = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    obliq += 0.00256 * math.cos(omega)
    decl = math.degrees(math.asin(math.sin(math.radians(obliq)) * math.sin(math.radians(app_long))))
    y = math.tan(math.radians(obliq / 2)) ** 2
    l0r = math.radians(l0)
    eq = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                          - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    return decl, eq


def _jd(t: datetime) -> float:
    return t.timestamp() / 86400.0 + 2440587.5


def _event_utc(day: date, lat: float, lon: float, event: str) -> datetime | None:
    """When an event happens on a calendar day (UTC), or None when the sun never gets there
    (midsummer / midwinter near the poles)."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    guess = midnight + timedelta(minutes=720 - 4 * lon)  # solar noon, roughly
    for _ in range(3):  # recompute with the sun's position at the event itself
        decl, eq = _solar(_jd(guess))
        noon = 720 - 4 * lon - eq
        if event == "noon":
            minutes = noon
        else:
            la, de = math.radians(lat), math.radians(decl)
            cos_ha = math.cos(math.radians(ZENITH[event])) / (math.cos(la) * math.cos(de)) - math.tan(la) * math.tan(de)
            if not -1 <= cos_ha <= 1:
                return None
            ha = math.degrees(math.acos(cos_ha))
            minutes = noon - 4 * ha if event in MORNING else noon + 4 * ha
        guess = midnight + timedelta(minutes=minutes)
    return guess


def event_on(day: date, event: str, where: tuple[float, float] | None = None) -> datetime | None:
    """The local (time-zone aware) time of dawn / sunrise / noon / sunset / dusk on a local day."""
    where = where or location()
    if not where:
        return None
    t = _event_utc(day, where[0], where[1], event)
    return _local(t) if t else None


def times(day: date, where: tuple[float, float] | None = None) -> dict[str, datetime | None]:
    return {e: event_on(day, e, where) for e in (*EVENTS, "noon")}


def position(when: datetime | None = None, where: tuple[float, float] | None = None) -> tuple[float, float] | None:
    """(elevation, azimuth) in degrees; elevation includes the usual refraction near the horizon."""
    where = where or location()
    if not where:
        return None
    lat, lon = where
    t = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
    decl, eq = _solar(_jd(t))
    minutes = t.hour * 60 + t.minute + t.second / 60
    tst = (minutes + eq + 4 * lon) % 1440
    ha = tst / 4 - 180
    la, de = math.radians(lat), math.radians(decl)
    cos_z = math.sin(la) * math.sin(de) + math.cos(la) * math.cos(de) * math.cos(math.radians(ha))
    zen = math.degrees(math.acos(max(-1.0, min(1.0, cos_z))))
    elev = 90 - zen
    if elev > -0.575:  # atmospheric refraction (NOAA's approximation)
        te = math.tan(math.radians(elev))
        if elev > 85:
            r = 0
        elif elev > 5:
            r = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5
        else:
            r = 1735 + elev * (-518.2 + elev * (103.4 + elev * (-12.79 + elev * 0.711)))
        elev += r / 3600
    zr = math.radians(zen)
    denom = math.cos(la) * math.sin(zr)
    if abs(denom) < 1e-9:
        az = 180.0
    else:
        a = math.degrees(math.acos(max(-1.0, min(1.0, (math.sin(la) * math.cos(zr) - math.sin(de)) / denom))))
        az = (a + 180) % 360 if ha > 0 else (540 - a) % 360
    return round(elev, 2), round(az, 2)


def next_event(event: str, after: datetime | None = None) -> datetime | None:
    now = _naive(after)
    for offset in range(0, 3):
        t = event_on(now.date() + timedelta(days=offset), event)
        if t and t > now:
            return t
    return None


# -- what automations ask ----------------------------------------------------------------------

def _naive(now: datetime | None) -> datetime:
    """The scheduler works in naive local time; compare in the sun's time zone."""
    if now is None:
        return _local(datetime.now(timezone.utc))
    if now.tzinfo is None:
        return now.replace(tzinfo=_tz) if _tz else now.astimezone()
    return _local(now)


def item_moment(event: str, offset_min: float, day: date) -> datetime | None:
    """sunset + offset on a local day (aware), for "at 30 min before sunrise"."""
    t = event_on(day, event)
    return t + timedelta(minutes=float(offset_min or 0)) if t else None


def item_hhmm(event: str, offset_min: float, day: date) -> str | None:
    t = item_moment(event, offset_min, day)
    return t.strftime("%H:%M") if t else None


def is_dark(spec: dict[str, Any], now: datetime | None = None) -> bool | None:
    """Between (sunset + its offset) and (sunrise + its offset) - across midnight. None when the
    home's location isn't known."""
    if not location():
        return None
    t = _naive(now)
    rise = item_moment("sunrise", spec.get("sunrise") or 0, t.date())
    sett = item_moment("sunset", spec.get("sunset") or 0, t.date())
    if not rise or not sett:  # polar day / night: go by where the sun is
        pos = position(t)
        return bool(pos and pos[0] < -0.833)
    return t >= sett or t < rise


def sun_ok(spec: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """The "only when it's dark / during daylight" check (True when there's none, or no location)."""
    if not spec or spec.get("only") not in ("dark", "light"):
        return True
    dark = is_dark(spec, now)
    if dark is None:
        return True
    if spec["only"] == "dark":
        return dark
    # Daylight: between (sunrise + offset) and (sunset + offset) - the same edges, the other side.
    return not dark
