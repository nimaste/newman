"""Where the household is.

* Each person's phone shares its location with the hub - opt-in per account, kept on the hub only,
  and only the newest fix (no history).
* An admin sets each account's sharing: "off" (not shared), "choice" (the person switches it on or
  off in their app; the household sees them) or "required" (always on; the admin picks who sees them).
* Places name where someone is: the built-in Home (the weather connection's spot unless moved,
  150 m) and any others ("School"). Inside Home a person is "home"; otherwise "place" is the
  smallest place they're inside, and "distanceM" is how far they are from Home.
* Phones report on their own about every 5 minutes (and when they arrive / leave somewhere). There's
  no "live" following - it drained batteries. Instead anyone who can see a person may ask for a fresh
  fix (POST /api/people/<id>/refresh): ONE locate push to their phones - an APNs location push to an
  iPhone that gave the hub a location push token (its LocationPushExtension answers even when the app
  was swiped away), else the silent "locate" push; Android a data message. At most once per 2 minutes
  per person, whoever asks.
* Guests never see anyone's location, and guests aren't people at all.
* A wall tablet's account has "Can see where people are" (on unless an admin turns it off). With it
  on, the tablet sees everyone sharing by their choice, and "required" people whose "who can see
  them" is the whole household or names that tablet (the list holds person ids and tablet account
  ids). Tablets never report a location themselves.
"""
from __future__ import annotations

import json
import math
import secrets
import time
from typing import Any, Callable

MODES = ("off", "choice", "required")
EVERY_S = 300          # how often a phone reports on its own
REFRESH_GAP_S = 120    # at most one "refresh" (locate push) per person this often, whoever asks
HOME_ID = "home"
HOME_RADIUS_M = 150.0
RADIUS_RANGE = (25.0, 50_000.0)
SOURCES = ("background", "live", "app", "refresh")  # "live": older apps
PEOPLE_ROLES = ("admin", "family")  # accounts that are household members (see and can be seen)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(min(1.0, math.sqrt(a)))


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def clean_place(data: dict[str, Any], *, home: bool = False) -> dict[str, Any]:
    """A place from the API ({name, lat, lon, radiusM}); ValueError says what's wrong."""
    lat, lon = _num(data.get("lat")), _num(data.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Pick the place's centre on the map")
    radius = _num(data.get("radiusM"))
    radius = HOME_RADIUS_M if radius is None and home else radius
    if radius is None or not RADIUS_RANGE[0] <= radius <= RADIUS_RANGE[1]:
        raise ValueError("The radius is 25 m to 50 km")
    out = {"lat": round(lat, 6), "lon": round(lon, 6), "radiusM": round(radius)}
    if not home:
        name = str(data.get("name") or "").strip()[:40]
        if not name:
            raise ValueError("Give the place a name")
        if name.lower() == "home":
            raise ValueError("Home is already a place - move it instead")
        out["name"] = name
    return out


class Locator:
    def __init__(self, store: Any, notifier: Any, clock: Callable[[], float] = time.time) -> None:
        self.store, self.notifier, self.clock = store, notifier, clock
        self._refreshed: dict[str, float] = {}  # person id -> when a refresh last went out

    # -- places ---------------------------------------------------------------------------------

    def _weather_spot(self) -> tuple[float, float] | None:
        for rec in self.store.integrations():
            if rec["type"] == "weather":
                lat, lon = _num(rec["config"].get("latitude")), _num(rec["config"].get("longitude"))
                if lat is not None and lon is not None:
                    return lat, lon
        return None

    def home(self) -> dict[str, Any]:
        """The built-in Home place. lat/lon are null until there's a weather connection or it's set."""
        saved = self.store.settings().get("homePlace") or {}
        spot = (saved["lat"], saved["lon"]) if saved.get("lat") is not None else self._weather_spot()
        return {"id": HOME_ID, "name": "Home", "lat": spot[0] if spot else None, "lon": spot[1] if spot else None,
                "radiusM": saved.get("radiusM") or HOME_RADIUS_M, "builtIn": True}

    def places(self) -> list[dict[str, Any]]:
        """Home first, then the named places."""
        return [self.home(), *(dict(p, builtIn=False) for p in self.store.settings().get("places") or [])]

    def add_place(self, data: dict[str, Any]) -> dict[str, Any]:
        p = {"id": "pl_" + secrets.token_hex(4), **clean_place(data)}
        self.store.set_setting("places", [*(self.store.settings().get("places") or []), p])
        return dict(p, builtIn=False)

    def change_place(self, pid: str, data: dict[str, Any]) -> dict[str, Any]:
        if pid == HOME_ID:
            cur = self.home()
            spot = clean_place({**cur, **data}, home=True)
            self.store.set_setting("homePlace", spot)
            return self.home()
        places = self.store.settings().get("places") or []
        cur = next((p for p in places if p["id"] == pid), None)
        if not cur:
            raise KeyError(pid)
        new = {"id": pid, **clean_place({**cur, **data})}
        self.store.set_setting("places", [new if p["id"] == pid else p for p in places])
        return dict(new, builtIn=False)

    def remove_place(self, pid: str) -> None:
        if pid == HOME_ID:
            raise ValueError("Home can be moved but not removed")
        places = self.store.settings().get("places") or []
        if not any(p["id"] == pid for p in places):
            raise KeyError(pid)
        self.store.set_setting("places", [p for p in places if p["id"] != pid])

    def where(self, lat: float, lon: float) -> dict[str, Any]:
        """{home, place, distanceM} for a spot."""
        h = self.home()
        dist = distance_m(lat, lon, h["lat"], h["lon"]) if h["lat"] is not None else None
        inside = [p for p in self.places()[1:] if distance_m(lat, lon, p["lat"], p["lon"]) <= p["radiusM"]]
        inside.sort(key=lambda p: p["radiusM"])
        return {"home": dist is not None and dist <= h["radiusM"], "place": inside[0]["name"] if inside else None,
                "distanceM": round(dist, 1) if dist is not None else None}

    # -- accounts' sharing --------------------------------------------------------------------------

    @staticmethod
    def mode(u: dict[str, Any] | None) -> str:
        """An account's sharing mode; "off" for anyone who isn't a household member (guest, tablet)."""
        if not u or u.get("role") not in PEOPLE_ROLES or not u.get("person_id") or u.get("disabled"):
            return "off"
        return u.get("loc_mode") if u.get("loc_mode") in MODES else "choice"

    def sharing(self, u: dict[str, Any] | None) -> bool:
        m = self.mode(u)
        return m == "required" or (m == "choice" and bool(u and u.get("loc_enabled")))

    @staticmethod
    def visible_to(u: dict[str, Any]) -> list[str] | None:
        try:
            v = json.loads(u.get("loc_visible") or "null")
        except ValueError:
            v = None
        return [str(x) for x in v] if isinstance(v, list) else None

    def settings_json(self, u: dict[str, Any]) -> dict[str, Any]:
        """What the admin's Accounts page shows and edits."""
        m = self.mode(u)
        return {"mode": m, "enabled": self.sharing(u), "visibleTo": self.visible_to(u) if m == "required" else None,
                "requiredBy": (u.get("loc_required_by") or None) if m == "required" else None}

    def account(self, person_id: str) -> dict[str, Any] | None:
        """The household account a person shares from (a sharing one first, if they have two)."""
        mine = [u for u in self.store.users() if u.get("person_id") == person_id and self.mode(u) != "off"]
        mine.sort(key=lambda u: not self.sharing(u))
        return mine[0] if mine else None

    # -- who sees whom -------------------------------------------------------------------------------

    @staticmethod
    def tablet_sees_people(u: dict[str, Any] | None) -> bool | None:
        """A wall tablet account's "Can see where people are" (None for any other account)."""
        if not u or u.get("role") != "tablet":
            return None
        return bool(u.get("tablet_people", 1))

    @staticmethod
    def can_view(viewer: dict[str, Any], session: dict[str, Any]) -> bool:
        """May this sign-in see anyone's location at all? Not guests; a wall tablet's account only
        with its "Can see where people are" on; a household member's account only off the wall."""
        if viewer.get("disabled"):
            return False
        if viewer.get("role") == "tablet":
            return bool(viewer.get("tablet_people", 1))
        return viewer.get("role") in PEOPLE_ROLES and session.get("kind") != "tablet"

    def can_see(self, viewer: dict[str, Any], session: dict[str, Any], person_id: str) -> bool:
        if not self.can_view(viewer, session):
            return False
        acct = self.account(person_id)
        if not acct or not self.sharing(acct):
            return False
        tablet = viewer.get("role") == "tablet"
        if (not tablet and viewer.get("person_id") == person_id) or self.mode(acct) == "choice":
            return True
        allowed = self.visible_to(acct)
        return allowed is None or (viewer.get("id") if tablet else viewer.get("person_id")) in allowed


    def person_json(self, p: dict[str, Any], viewer: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"id": p["id"], "name": p["name"], "sharing": False, "home": False, "place": None,
                               "distanceM": None, "lat": None, "lon": None, "accuracyM": None, "at": None,
                               "live": False}  # always false now (no live following); older apps read it
        if not self.can_see(viewer, session, p["id"]):
            return out
        out.update(sharing=True)
        loc = self.store.location(p["id"])
        if loc:
            out.update(self.where(loc["lat"], loc["lon"]), lat=loc["lat"], lon=loc["lon"],
                       accuracyM=loc["accuracy_m"], at=loc["at"])
        return out

    # -- a phone reporting, someone asking for a fresh fix --------------------------------------------------

    def me_json(self, u: dict[str, Any]) -> dict[str, Any]:
        m = self.mode(u)
        return {"mode": m, "enabled": self.sharing(u),
                "requiredBy": (u.get("loc_required_by") or None) if m == "required" else None,
                "everySeconds": EVERY_S, "live": False}

    def report(self, u: dict[str, Any], data: dict[str, Any]) -> bool:
        """Keep a phone's fix (ValueError for a bad one). False when it's older than the one kept."""
        lat, lon = _num(data.get("lat")), _num(data.get("lon"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("lat and lon must be numbers (degrees)")
        extra: dict[str, float | None] = {}
        for key in ("accuracyM", "speedMps"):
            v = data.get(key)
            extra[key] = None if v is None else _num(v)
            if v is not None and (extra[key] is None or extra[key] < 0):  # type: ignore[operator]
                raise ValueError(f"{key} must be a positive number or null")
        now = self.clock()
        at = data.get("at")
        at = now if at is None else _num(at)
        if at is None or at <= 0:
            raise ValueError("at must be epoch seconds")
        at = min(at, now)  # a phone clock running ahead doesn't get to pin "now"
        source = data.get("source") or "app"
        if source not in SOURCES:
            raise ValueError("source must be background, refresh or app")
        pid = u["person_id"]
        kept = self.store.location(pid)
        if kept and kept["at"] > at:
            return False
        self.store.save_location(pid, lat, lon, extra["accuracyM"], at, extra["speedMps"], source)
        return True

    async def refresh(self, person_id: str) -> tuple[bool, float]:
        """Ask a person's phones for one fresh fix. (sent, next allowed at): not sent when someone
        asked less than 2 minutes ago."""
        now = self.clock()
        last = self._refreshed.get(person_id)
        if last is not None and now - last < REFRESH_GAP_S:
            return False, last + REFRESH_GAP_S
        self._refreshed[person_id] = now
        await self.notifier.locate(person_id)
        return True, now + REFRESH_GAP_S
    # -- iPhones' location push tokens -------------------------------------------------------------

    def _tokens(self) -> Any:
        return getattr(self.notifier, "location_tokens", None)

    def set_push_token(self, u: dict[str, Any], data: dict[str, Any]) -> bool:
        """POST /api/me/location/push-token {deviceId, token, env}: keep (or, with a null / empty
        token, forget) this phone's location push token. True when one is kept. ValueError for a bad
        body, LookupError when deviceId isn't this person's registered iPhone, PermissionError when
        their account isn't sharing."""
        from .push.location_tokens import clean_token
        device_id = str(data.get("deviceId") or "").strip()
        if not device_id:
            raise ValueError("deviceId is required")
        tokens = self._tokens()
        phone = self.store.phone(device_id)
        mine = phone is not None and phone.person_id == u.get("person_id") and phone.platform == "ios"
        token = data.get("token")
        if token in (None, ""):
            if phone is not None and not mine:
                raise LookupError("That isn't one of your iPhones")
            if tokens:
                tokens.forget(device_id)
            return False
        token = clean_token(token)
        if not self.sharing(u):
            raise PermissionError("Location sharing isn't on for your account")
        if not mine:
            raise LookupError("Register this iPhone first (POST /api/devices/register)")
        if tokens:
            tokens.set(device_id, token, "sandbox" if data.get("env") == "sandbox" else "production")
        return True

    def forget(self, person_id: str | None) -> None:
        """Sharing stopped: drop the kept fix and their iPhones' location push tokens."""
        if person_id:
            self.store.remove_location(person_id)
            if tokens := self._tokens():
                tokens.forget_person(person_id)
