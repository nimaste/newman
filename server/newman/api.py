"""HTTP API (see API.md) + the web dashboard's static files.

/api/*         - dashboard + app API; needs a signed-in session (cookie or Bearer token),
                 with the away-from-home sign-in policy applied (see auth.py).
/api/public/*  - token-authenticated endpoints a notification needs (snapshot,
                 action button); safe to expose on a token-only host.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import ipaddress
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from yarl import URL

from . import auth as A
from . import categories as CT
from . import backup as B
from . import connect as CN
from . import controls as C
from . import groups as G
from . import home as HM
from . import house as HS
from . import muting as M
from . import today as T
from . import dashboards as D
from . import phone_layouts as PL
from . import press_reports as PRS
from . import profiles as PF
from . import replace as R
from . import tokens
from . import wall_pictures as WP
from .automations import describe as d
from .charging import SmartCharging, clean_settings as charging_settings
from .automations.engine import ActionError
from .automations.templates import templates
from .config import config
from .integrations.base import CommandError
from .integrations.frigate import FrigateIntegration
from .integrations.tablets import clean_report as tablet_report
from .integrations.virtual import VirtualIntegration
from .kinds import KINDS, meta_json, properties_for
from .live import LiveStatuses, add_live_routes
from .locations import Locator
from .mapkit import MapKitTokens
from .push.notifier import Notification
from .quiet import QuietHours
from .push.widgets import WidgetPush
from .state import Entity
from . import widgets as W
from .store import Phone, new_id
from .video import Video
from .music import add_music_routes
from .recipes import add_recipe_routes
from .voice.api import add_voice_routes

if TYPE_CHECKING:
    from .app import Newman

log = logging.getLogger(__name__)

SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# Frigate ids look like 1790723343.68906-748iee; alerts are "r:<review id>"
CLIP_ID = re.compile(r"^(r:)?[A-Za-z0-9._-]{1,64}$")
STARTED = time.time()
LOCATOR = web.AppKey("locator", Locator)  # tests reach the app's Locator through this

# Map data: OpenFreeMap (openfreemap.org - free, no key) through the hub, which names itself, fetches
# each file once and keeps it. The only server it ever fetches map data from.
MAP_UPSTREAM = "https://tiles.openfreemap.org/"
MAP_USER_AGENT = "Newman-home-hub/1.0 (self-hosted, one household; https://github.com/)"
MAP_KEEP_S = 30 * 86400  # tiles, fonts, sprites: their paths are versioned
MAP_STYLE_KEEP_S = 6 * 3600  # styles and the TileJSON (they name the current tile set)
MAP_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._,@+-]{0,79}$")  # one path segment
MAP_TYPES = {"": "application/json", ".json": "application/json", ".pbf": "application/x-protobuf",
             ".png": "image/png", ".webp": "image/webp"}
MAP_RAW = (".png", ".webp")  # already compressed: kept and served as they come


def _map_rewrite(v: Any) -> Any:
    """Every link into OpenFreeMap (style, TileJSON) points back at the hub instead."""
    if isinstance(v, str):
        return "/api/map/ofm/" + v[len(MAP_UPSTREAM):] if v.startswith(MAP_UPSTREAM) else v
    if isinstance(v, list):
        return [_map_rewrite(x) for x in v]
    if isinstance(v, dict):
        return {k: _map_rewrite(x) for k, x in v.items()}
    return v


def _map_prepare(data: bytes, gz: bool, ext: str) -> bytes:
    """What's kept on disk: JSON with its links rewritten; gzip-compressed unless it's a picture."""
    if ext in MAP_RAW:
        return gzip.decompress(data) if gz else data
    if ext in ("", ".json"):
        doc = json.loads(gzip.decompress(data) if gz else data)
        return gzip.compress(json.dumps(_map_rewrite(doc), separators=(",", ":")).encode(), 6)
    return data if gz else gzip.compress(data, 6)


WEB_ASSET = re.compile(r"""<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*["'][^"']*?\bassets/([^"'/?#]+\.(?:js|css))["']""", re.I)


def web_build_id(web_dir: Path) -> str | None:
    """Which web build the hub serves: the hashed script / style names its index.html loads, sorted and
    space-joined ("index-Cg0z-opQ.js index-D4f2a.css"). The page works out the same from what it loaded
    (web/src/build.js) and reloads itself when they differ. None without a build."""
    try:
        html = (web_dir / "index.html").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    names = sorted(set(WEB_ASSET.findall(html)))
    return " ".join(names) or None


def _err(status: int, message: str) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def build_app(h: "Newman") -> web.Application:
    r = web.RouteTableDef()
    loc = Locator(h.store, h.notifier)  # where people are (locations.py)
    # Per-device video settings: streams, All Cameras, "Video from" (video.py).
    video = h.video = getattr(h, "video", None) or Video(h.store, h.hub, h.integrations, h.snapshot_dir)
    h.engine.video = video
    # Wall tablets' smart charging (charging.py): watches the tablets' batteries and their plugs.
    charging = h.charging = getattr(h, "charging", None) or SmartCharging(h.hub, h.store, h.engine, h.notifier)
    web_build = web_build_id(Path(config.web_dir))  # read once: a new build means a new image / restart
    # Phones' widgets (widgets.py): reload pushes when what a widget shows changes (push/widgets.py).
    widget_push = h.widget_push = WidgetPush(h.store, h.notifier, view=W.state_json, showable=W.showable)
    # Live statuses (live.py): iPhone Live Activities / Android ongoing notifications while devices matter.
    live = h.live = getattr(h, "live", None) or LiveStatuses(h.hub, h.store, h.notifier)
    h.engine.live = live
    h.engine.listeners.append(lambda: live.automations_changed(h.engine.autos))
    # Categories and the category / icon a device was given (categories.py) - before anything reads one.
    cats = h.categories = getattr(h, "categories", None) or CT.Categories(h.store, h.hub)
    # Problems someone muted ("No AC power" on a keypad that isn't meant to stay plugged in): muting.py.
    muting = h.muting = getattr(h, "muting", None) or M.Muting(h.store, h.hub)
    # House settings (the web's Settings page): battery level, dimming, cameras, how long history is kept.
    house = h.house = HS.House(h.store, camera_ok=lambda i: bool((e := h.hub.get(i)) and HM.is_camera(e)))
    h.engine.house = house
    # Light/switch groups and rooms (groups.py): groups are devices of their own, connection "groups".
    groups = h.groups = G.Groups(h.store, h.hub, h.integrations)
    # Quiet hours, per person (quiet.py): their phones get everyday notifications silently.
    quiet = QuietHours(h.store)
    h.notifier.quiet = quiet.silent

    def can_automate(u: dict[str, Any] | None) -> bool:
        """May make and change automations: admins always, wall tablets never, anyone else if allowed."""
        if not u:
            return False
        return u["role"] == "admin" or (u["role"] != "tablet" and bool(u.get("can_automate")))

    def entity_json(e: Entity, origins: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        # origins: video.origins(), when building many devices at once (else worked out per device).
        inst = h.integrations.instances.get(e.integration)
        eid = quote(e.id, safe="")
        return {"id": e.id, "name": e.name, "kind": e.kind, "category": e.category, "icon": cats.icon_of(e.id),
                "integration": e.integration, "integrationName": inst.display_name if inst else e.integration,
                "state": e.state, "stateLabel": KINDS[e.kind].label_for(e.state, e.attrs), "available": e.available,
                "attrs": e.attrs, "health": muting.health(e),
                # Every value it reports that automations can use (with its current value).
                "properties": properties_for(e),
                # Where to get its picture / recent alerts - null when its integration has none.
                # Both follow the device's "Video from" link when it has a working one.
                "still": f"api/entities/{eid}/still.jpg" if video.picture_of(e.id) else None,
                "alerts": f"api/entities/{eid}/alerts" if inst and inst.has_alerts(e.id) else None,
                # Real-time video: a websocket speaking go2rtc's MSE protocol (?quality=sub|main).
                "live": f"api/entities/{eid}/live" if video.has_live(e.id) else None,
                # Its video settings: streams, All Cameras, "Video from" and its choices (video.py).
                "video": video.to_json(e.id, origins),
                # "Live status while…" (null when its kind has no states to pick): live.py.
                "liveStatus": live.setting_json(e),
                # A remote's Controls - its lights and what each press does (controls.py); null when none.
                "controls": h.engine.controls.json(e),
                # Its room (set in Newman, else its connection's) and the group it's in (groups.py).
                "room": groups.room_of(e), "group": groups.member_group(e.id),
                "usedBy": h.engine.used_by(e.id), "updatedAt": e.updated_at}

    def person_json(p: dict[str, Any], viewer: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        # Where they are comes only for someone this viewer may see (else sharing false, nulls).
        # Their colour and photo go to everyone who sees their name.
        if not p.get("color"):
            PF.ensure_colors(h.store)
            p = h.store.person(p["id"]) or p
        return {**loc.person_json(p, viewer, session), "color": p.get("color") or PF.PALETTE[0],
                "photo": PF.photo_url(p),
                "phones": [ph.to_json() for ph in h.store.phones(p["id"])],
                "automations": [a["id"] for a in h.engine.autos.values() if p["id"] in a.get("recipients", [])]}

    def snapshot_response(snapshot_id: str) -> web.StreamResponse:
        path = h.snapshot_dir / f"{snapshot_id}.jpg"
        if not SNAPSHOT_ID.match(snapshot_id) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Content-Type": "image/jpeg", "Cache-Control": "private, max-age=86400"})

    async def body(req: web.Request) -> dict[str, Any]:
        if not req.can_read_body:
            return {}
        try:
            data = await req.json()
        except ValueError:
            raise web.HTTPBadRequest(text="invalid JSON")
        return data if isinstance(data, dict) else {}

    # -- accounts & sign-in ---------------------------------------------------------------------

    throttle = A.Throttle()
    local_token_path = Path(config.data_dir) / ".local_token"
    if not local_token_path.exists():
        local_token_path.write_text(secrets.token_urlsafe(32))
        local_token_path.chmod(0o600)
    local_token = local_token_path.read_text().strip()
    MAINTENANCE = {"id": "local", "username": "local", "name": "Newman", "role": "admin", "person_id": None,
                   "pin_hash": None, "totp_enabled": 0}
    OPEN = {"/health", "/api/meta", "/api/hello", "/api/auth/state", "/api/auth/login", "/api/auth/setup"}
    WS_PATHS = ("/api/ws",)

    def auth_settings() -> dict[str, Any]:
        return {**A.DEFAULT_SETTINGS, **h.store.settings()}

    def origin(req: web.Request) -> A.Origin:
        return A.origin_of(req.remote, req.headers, auth_settings())

    def user_json(u: dict[str, Any]) -> dict[str, Any]:
        return {"id": u["id"], "username": u["username"], "name": u["name"], "role": u["role"],
                "personId": u.get("person_id"), "twoFactor": bool(u.get("totp_enabled")),
                "hasPin": bool(u.get("pin_hash")), "disabled": bool(u.get("disabled")),
                "location": loc.settings_json(u), "canAutomate": can_automate(u),
                "seesPeople": loc.tablet_sees_people(u)}  # a wall tablet's "Can see where people are"

    def session_json(s: dict[str, Any], current: str | None) -> dict[str, Any]:
        return {"id": s["id"], "kind": s["kind"], "deviceName": s["device_name"], "createdAt": s["created_at"],
                "lastSeen": s["last_seen"], "lastIp": s["last_ip"], "where": s["where_from"],
                "remember": bool(s["remember"]), "certificate": bool(s["cert"]), "current": s["id"] == current,
                "dashboardId": s.get("dashboard_id"), "live": A.session_live(s, time.time())}

    def token_of(req: web.Request) -> str | None:
        head = req.headers.get("Authorization", "")
        if head.startswith("Bearer "):
            return head[7:].strip()
        photo = req.method == "GET" and ((req.path.startswith(("/api/people/", "/api/recipes/")) and
                                          req.path.endswith(("/photo", "/image"))) or
                                         (req.path.startswith("/api/dashboards/") and "/pictures/" in req.path))
        if req.path in WS_PATHS or req.path.endswith("/live") or photo or req.path.startswith(("/api/music/stream/",
                                                                                                  "/api/music/cover/")):
            if req.query.get("token"):
                return req.query["token"]  # native apps' websockets can't always send headers
        # The dashboard's cookie first, so the dashboard is always its own sign-in in a browser that also
        # has a wall page's; the wall page sends its token (above) and needs its cookie only without one
        # (its pictures and video, storage blocked). A wall tablet signed in before the wall cookie keeps
        # its sign-in in the dashboard's cookie - it still works.
        return req.cookies.get(A.SESSION_COOKIE) or req.cookies.get(A.WALL_COOKIE)

    def find_session(req: web.Request) -> tuple[dict[str, Any], dict[str, Any]] | None:
        tok = token_of(req)
        s = h.store.session_by_token(A.token_hash(tok)) if tok else None
        if not s:
            return None
        u = h.store.user(s["user_id"])
        if not A.session_accepted(s, u, time.time()):
            return None
        if time.time() - s["last_seen"] > 300:  # sliding expiry, written at most every 5 minutes
            o = origin(req)
            life = A.SESSION_DAYS[s["kind"]] * 86400 if s["remember"] or s["kind"] != "browser" \
                else A.SHORT_BROWSER_HOURS * 3600
            h.store.touch_session(s["id"], o.ip, "home" if o.home else "away", time.time() + life)
        return s, u

    def allowed(req: web.Request) -> bool:
        m, p, u, s = req.method, req.path, req["user"], req["session"]
        # Using the house (a device command, an alert's button) - which family and wall tablets may do.
        command = (p.startswith("/api/entities/") and (p.endswith("/command") or "/mute" in p)) or \
            (p.startswith("/api/runs/") and p.endswith("/press")) or \
            (p.startswith("/api/upkeep/") and p.rsplit("/", 1)[-1] in ("done", "undo", "snooze")) or \
            p in ("/api/today/dismiss", "/api/tablet/battery", "/api/music/report", "/api/music/play", "/api/cook") or \
            (p.startswith("/api/voice/") and not p.startswith("/api/voice/settings"))  # talking to Newman, its timers
        # Asking for someone's fresh location (a wall tablet's Family map too; checked inside).
        follow = p.startswith("/api/people/") and p.endswith(("/refresh", "/follow"))
        if p.startswith("/api/auth/"):
            return True  # own account; admin-only parts check inside
        if p == "/api/automations/describe" and s["kind"] != "tablet":
            return True  # wording an automation changes nothing
        if p.startswith("/api/automations") and m not in ("GET", "HEAD"):
            return can_automate(u) and s["kind"] != "tablet"  # make / change / switch / run: if allowed
        if s["kind"] == "tablet" and m not in ("GET", "HEAD") and not command and not follow:
            return False  # no configuration from a wall tablet
        if u["role"] == "admin":
            return True
        if p.startswith("/api/dashboards") or p.startswith("/api/phone-layout") or p.startswith("/api/home"):
            return True  # everyone arranges their own dashboards and phone (checked inside)
        if m in ("GET", "HEAD"):
            return not (p.startswith("/api/test/") or p.startswith("/api/users"))
        # Their own location sharing / profile, and refreshing someone on the map (checked inside).
        own = p.startswith("/api/me/") or follow
        return command or own or p in ("/api/devices/register", "/api/widgets/push-token", "/api/live/tokens")

    @web.middleware
    async def auth_middleware(req: web.Request, handler: Any) -> web.StreamResponse:
        p = req.path
        if not p.startswith("/api/") or p in OPEN or p.startswith("/api/public/"):
            return await handler(req)
        if req.headers.get("X-Newman-Local-Token") == local_token and req.remote in ("127.0.0.1", "::1"):
            req["user"], req["session"] = MAINTENANCE, {"id": None, "kind": "browser"}  # scripts on the hub itself
        else:
            found = find_session(req)
            if not found:
                return web.json_response({"error": "Please sign in", "signIn": True}, status=401)
            req["session"], req["user"] = found
        if not allowed(req):
            if p.startswith("/api/automations") and req["session"]["kind"] != "tablet":
                return _err(403, "Your account can't change automations")
            return _err(403, "Your account can't do that" if req["session"]["kind"] != "tablet"
                        else "Change this from the Newman dashboard, not the wall tablet")
        return await handler(req)

    def require_admin(req: web.Request) -> None:
        if req["user"]["role"] != "admin" or req["session"]["kind"] == "tablet":
            raise web.HTTPForbidden(text='{"error": "Only an admin can do that"}', content_type="application/json")

    def link_person(name: str) -> str:
        """Each account is also a person notifications can go to; reuse one with the same name."""
        for p in h.store.people():
            if p["name"].strip().lower() == name.strip().lower():
                return p["id"]
        return h.store.add_person(name)["id"]

    def start_session(req: web.Request, u: dict[str, Any], o: A.Origin, kind: str, remember: bool,
                      device: str) -> tuple[web.Response, str]:
        token, now = secrets.token_urlsafe(32), time.time()
        life = A.SESSION_DAYS[kind] * 86400 if remember or kind != "browser" else A.SHORT_BROWSER_HOURS * 3600
        h.store.add_session({"id": new_id("s"), "token_hash": A.token_hash(token), "user_id": u["id"], "kind": kind,
                             "device_name": device[:64], "created_at": now, "last_seen": now, "last_ip": o.ip,
                             "where_from": "home" if o.home else "away", "expires_at": now + life,
                             "remember": int(remember or kind != "browser"), "cert": o.cert})
        resp = web.json_response({"ok": True, "user": user_json(u), "token": token if kind != "browser" else None})
        if kind in ("browser", "tablet"):  # a tablet page's pictures and video authenticate by cookie too
            resp.set_cookie(A.cookie_for(kind), token, httponly=True, samesite="Lax", path="/",
                            secure=req.secure or o.relayed, max_age=life if remember or kind == "tablet" else None)
        old = req.cookies.get(A.SESSION_COOKIE)
        was = h.store.session_by_token(A.token_hash(old)) if kind == "tablet" and old else None
        if was and was["kind"] == "tablet":
            resp.del_cookie(A.SESSION_COOKIE, path="/")  # a wall tablet's sign-in from before the wall cookie
        return resp, token

    @r.get("/api/auth/state")
    async def auth_state(req: web.Request) -> web.Response:
        o, found = origin(req), find_session(req)
        return web.json_response({
            "setupNeeded": not h.store.users(), "signedIn": bool(found),
            "user": user_json(found[1]) if found else None,
            "session": session_json(found[0], found[0]["id"]) if found else None,
            "where": "home" if o.home else "away", "certificate": bool(o.cert),
            "awayPolicy": auth_settings()["awayPolicy"], "tabletSensitive": auth_settings()["tabletSensitive"],
            "time": time.time(), "version": config.app_version})

    @r.post("/api/auth/setup")
    async def auth_setup(req: web.Request) -> web.Response:
        """First run: create the first admin (only from home, only while there are no accounts)."""
        o, data = origin(req), await body(req)
        if h.store.users():
            return _err(409, "Newman is already set up - sign in instead")
        if not o.home:
            return _err(403, "Set Newman up from a device at home")
        username, name, pw = str(data.get("username", "")).strip().lower(), str(data.get("name", "")).strip(), \
            str(data.get("password", ""))
        if not re.fullmatch(r"[a-z0-9._-]{2,32}", username):
            return _err(400, "Usernames are 2-32 letters, digits, dots, dashes or underscores")
        if problem := A.password_problem(pw):
            return _err(400, problem)
        u = h.store.add_user(username, name or username, "admin", A.hash_secret(pw), link_person(name or username))
        h.store.log_event("account", f"{u['name']} set up Newman", "First admin account created", None)
        resp, _ = start_session(req, u, o, "browser", True, str(data.get("deviceName", "")) or "Browser")
        return resp

    @r.post("/api/auth/login")
    async def auth_login(req: web.Request) -> web.Response:
        o, data = origin(req), await body(req)
        username = str(data.get("username", "")).strip().lower()
        settings = auth_settings()
        wait = throttle.blocked(o.ip, username, o.home)
        if wait > 0:
            return web.json_response({"error": f"Too many attempts. Try again in {max(1, round(wait / 60))} min.",
                                      "retryAfter": round(wait)}, status=429)
        u = h.store.user_by_name(username)
        if not u or u["disabled"] or not A.check_secret(str(data.get("password", "")), u["password_hash"]):
            throttle.fail(o.ip, username, o.home)
            if not o.home:
                h.store.log_event("signin", "Failed sign-in from outside", f"“{username}” from {o.ip}", None,
                                  {"ip": o.ip, "failed": True})
            return _err(401, "Wrong username or password")
        need = A.needs_second_factor(u, o, settings)
        if need and need.startswith("error:"):
            return _err(403, need[6:])
        if need == "totp":
            code, rec = str(data.get("code", "")).strip(), str(data.get("recoveryCode", "")).strip().lower()
            if not code and not rec:
                return web.json_response({"needs": "totp"}, status=401)
            if code:
                step = A.totp_match(u["totp_secret"] or "", code)
                if step is None or step <= u["totp_last_step"]:
                    throttle.fail(o.ip, username, o.home)
                    return web.json_response({"needs": "totp", "error": "That code didn't work"}, status=401)
                h.store.update_user(u["id"], totp_last_step=step)
            else:
                codes = json.loads(u["recovery"] or "[]")
                hit = next((c for c in codes if A.token_hash(rec) == c), None)
                if not hit:
                    throttle.fail(o.ip, username, o.home)
                    return web.json_response({"needs": "totp", "error": "That recovery code didn't work"}, status=401)
                h.store.update_user(u["id"], recovery=json.dumps([c for c in codes if c != hit]))
        kind = data.get("kind") if data.get("kind") in A.KINDS else "browser"
        if u["role"] == "tablet" and kind != "tablet":
            return _err(403, "This is a wall tablet's account - sign in with it on the wall tablet")
        if kind == "tablet" and u["role"] != "tablet":
            return _err(403, "This page is for wall tablet accounts - sign in with the tablet's own account "
                             "(add one under Accounts)")
        throttle.clear(o.ip, username, o.home)
        device = str(data.get("deviceName", "")).strip() or {"browser": "Browser", "app": "Newman app",
                                                             "tablet": "Wall tablet"}[kind]
        resp, _ = start_session(req, u, o, kind, bool(data.get("remember")), device)
        h.store.log_event("signin", f"{u['name']} signed in", f"{device} · {'at home' if o.home else 'from outside'}"
                          + ("" if o.home else f" · {o.ip}"), None, {"ip": o.ip, "kind": kind, "home": o.home})
        return resp

    @r.post("/api/auth/logout")
    async def auth_logout(req: web.Request) -> web.Response:
        if req["session"].get("id"):
            h.store.remove_session(req["session"]["id"])
            widget_push.tokens.prune()  # its widgets get no more pushes
            live.tokens.prune()  # nor its live statuses
            forget_signed_out_tablets()  # the account's last wall tablet signed out: its devices go
        resp = web.json_response({"ok": True})
        for name in (A.SESSION_COOKIE, A.WALL_COOKIE):  # only this sign-in's: the browser's other one stays
            tok = req.cookies.get(name)
            if tok and A.token_hash(tok) == req["session"].get("token_hash"):
                resp.del_cookie(name, path="/")
        return resp

    @r.post("/api/auth/password")
    async def auth_password(req: web.Request) -> web.Response:
        u, data = req["user"], await body(req)
        if not A.check_secret(str(data.get("current", "")), u.get("password_hash")):
            return _err(403, "Your current password is wrong")
        if problem := A.password_problem(str(data.get("new", ""))):
            return _err(400, problem)
        h.store.update_user(u["id"], password_hash=A.hash_secret(str(data["new"])), password_changed_at=time.time())
        h.store.remove_sessions(u["id"], keep=req["session"]["id"])  # sign out every other device
        forget_signed_out_tablets()
        return web.json_response({"ok": True})

    @r.post("/api/auth/pin")
    async def auth_pin(req: web.Request) -> web.Response:
        """The PIN a wall tablet asks for before unlocking, opening or disarming ("" removes it)."""
        u, data = req["user"], await body(req)
        if not A.check_secret(str(data.get("password", "")), u.get("password_hash")):
            return _err(403, "Your password is wrong")
        pin = str(data.get("pin", ""))
        if pin and not re.fullmatch(r"\d{4,8}", pin):
            return _err(400, "A PIN is 4 to 8 digits")
        h.store.update_user(u["id"], pin_hash=A.hash_secret(pin) if pin else None)
        return web.json_response({"ok": True, "hasPin": bool(pin)})

    @r.post("/api/auth/2fa/start")
    async def auth_2fa_start(req: web.Request) -> web.Response:
        u, data = req["user"], await body(req)
        if not A.check_secret(str(data.get("password", "")), u.get("password_hash")):
            return _err(403, "Your password is wrong")
        secret = A.new_totp_secret()
        h.store.update_user(u["id"], totp_secret=secret, totp_enabled=0)
        uri = A.totp_uri(secret, u["username"])
        import io
        import segno
        buf = io.BytesIO()
        segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, dark="#111111", light="#ffffff",
                                        xmldecl=False)
        return web.json_response({"secret": secret, "uri": uri, "qrSvg": buf.getvalue().decode()})

    @r.post("/api/auth/2fa/enable")
    async def auth_2fa_enable(req: web.Request) -> web.Response:
        u, data = req["user"], await body(req)
        step = A.totp_match(u.get("totp_secret") or "", str(data.get("code", "")))
        if step is None:
            return _err(400, "That code didn't match - check the time on your phone and try the newest code")
        codes = A.new_recovery_codes()
        h.store.update_user(u["id"], totp_enabled=1, totp_last_step=step,
                            recovery=json.dumps([A.token_hash(c) for c in codes]))
        return web.json_response({"ok": True, "recoveryCodes": codes})

    @r.post("/api/auth/2fa/disable")
    async def auth_2fa_disable(req: web.Request) -> web.Response:
        u, data = req["user"], await body(req)
        if not A.check_secret(str(data.get("password", "")), u.get("password_hash")):
            return _err(403, "Your password is wrong")
        h.store.update_user(u["id"], totp_enabled=0, totp_secret=None, recovery="[]")
        return web.json_response({"ok": True})

    @r.get("/api/auth/sessions")
    async def auth_sessions(req: web.Request) -> web.Response:
        everyone = req.query.get("all") and req["user"]["role"] == "admin"
        h.store.purge_sessions()  # expired sign-ins are gone for good; nothing lists them
        names = {u["id"]: u["name"] for u in h.store.users()}
        return web.json_response([{**session_json(s, req["session"]["id"]), "user": names.get(s["user_id"], "")}
                                  for s in h.store.sessions(None if everyone else req["user"]["id"])])

    @r.patch("/api/auth/sessions/{sid}")
    async def auth_session_update(req: web.Request) -> web.Response:
        """Choose which dashboard a device (say, the wall tablet) shows; null = your default."""
        sid, data = req.match_info["sid"], await body(req)
        s = next((x for x in h.store.sessions() if x["id"] == sid), None)
        if not s or (s["user_id"] != req["user"]["id"] and req["user"]["role"] != "admin"):
            return _err(404, "Unknown device")
        did = data.get("dashboardId") or None
        if did:
            d = h.store.dashboard(did)
            if not d or d["userId"] != s["user_id"]:
                return _err(400, "That dashboard belongs to someone else")
        h.store.set_session_dashboard(sid, did)
        await broadcast({"type": "dashboard", "userId": s["user_id"]})
        return web.json_response({"ok": True})

    @r.delete("/api/auth/sessions/{sid}")
    async def auth_revoke(req: web.Request) -> web.Response:
        sid = req.match_info["sid"]
        mine = {s["id"] for s in h.store.sessions(req["user"]["id"])}
        if sid not in mine and req["user"]["role"] != "admin":
            return _err(403, "That isn't your device")
        h.store.remove_session(sid)
        widget_push.tokens.prune()
        live.tokens.prune()
        forget_signed_out_tablets()
        return web.json_response({"ok": True})

    def settings_json() -> dict[str, Any]:
        """Sign-in settings, plus this hub's name and outside address (what the apps connect with)."""
        outside = identity()[2]
        return {**auth_settings(), "homeName": str(h.store.settings().get("homeName") or ""),
                "outsideUrl": outside or ""}

    @r.get("/api/auth/settings")
    async def auth_get_settings(req: web.Request) -> web.Response:
        require_admin(req)
        return web.json_response(settings_json())

    @r.patch("/api/auth/settings")
    async def auth_set_settings(req: web.Request) -> web.Response:
        require_admin(req)
        data = await body(req)
        if "homeName" in data or "outsideUrl" in data:
            try:
                name = CN.clean_name(data["homeName"]) if "homeName" in data else None
                outside = CN.normalize_outside(data["outsideUrl"]) if "outsideUrl" in data else None
            except ValueError as e:
                return _err(400, str(e))
            if name is not None:
                h.store.set_setting("homeName", name)
            if outside is not None:
                h.store.set_setting("outsideUrl", outside)
            if callable(getattr(h, "reannounce", None)):
                h.reannounce()
        if "awayPolicy" in data:
            if data["awayPolicy"] not in A.AWAY_POLICIES:
                return _err(400, "Unknown policy")
            h.store.set_setting("awayPolicy", data["awayPolicy"])
        if "tabletSensitive" in data:
            if data["tabletSensitive"] not in A.TABLET_SENSITIVE:
                return _err(400, "Unknown wall-tablet setting")
            h.store.set_setting("tabletSensitive", data["tabletSensitive"])
            await broadcast({"type": "settings"})  # tablets pick it up straight away
        if "homeTwoFactor" in data:
            h.store.set_setting("homeTwoFactor", bool(data["homeTwoFactor"]))
        for key in ("certHeader", "certSubjectHeader"):
            if key in data and re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(data[key])):
                h.store.set_setting(key, str(data[key]))
        if "trustedProxies" in data:
            try:
                nets = [str(ipaddress.ip_network(str(n), strict=False)) for n in data["trustedProxies"]]
            except ValueError:
                return _err(400, "Trusted proxies must be addresses or ranges like 172.16.0.0/12")
            h.store.set_setting("trustedProxies", nets)
        return web.json_response(settings_json())

    @r.get("/api/users")
    async def users(req: web.Request) -> web.Response:
        require_admin(req)
        return web.json_response([user_json(u) for u in h.store.users()])

    @r.post("/api/users")
    async def add_user(req: web.Request) -> web.Response:
        require_admin(req)
        data = await body(req)
        username, name = str(data.get("username", "")).strip().lower(), str(data.get("name", "")).strip()
        if not re.fullmatch(r"[a-z0-9._-]{2,32}", username):
            return _err(400, "Usernames are 2-32 letters, digits, dots, dashes or underscores")
        if h.store.user_by_name(username):
            return _err(409, "That username is taken")
        if data.get("role") not in A.ROLES:
            return _err(400, "Role must be admin, family, guest or tablet")
        if problem := A.password_problem(str(data.get("password", ""))):
            return _err(400, problem)
        if data["role"] in A.NOT_PEOPLE:
            person = None  # a wall tablet or a guest isn't someone to notify or locate
        else:
            person = data.get("personId") if data.get("personId") and h.store.person(data["personId"]) \
                else link_person(name or username)
        u = h.store.add_user(username, name or username, data["role"], A.hash_secret(str(data["password"])), person)
        return web.json_response(user_json(u))

    async def rename_account_devices(uid: str, old: str, new: str) -> None:
        """Devices named after an account (a wall tablet's "Tablet" battery, "Tablet music") take the new
        name now rather than at their next report - unless someone renamed that device themselves."""
        for inst in list(h.integrations.instances.values()):
            keep = getattr(inst, "rename_account", None)  # what they keep for after a restart
            if keep:
                keep(uid, new)
        own = h.store.entity_names()
        for e in h.hub.all():
            if e.id.split(".", 1)[-1] != uid or e.id in own:
                continue
            if e.name == old or e.name.startswith(old + " "):
                await h.hub.update(e.id, name=new + e.name[len(old):])

    def forget_signed_out_tablets() -> None:
        """A wall tablet's devices (its battery, its music player) belong to the account it's signed in with,
        and exist only while that account has a wall-tablet sign-in that sign-in still accepts - however
        quiet or asleep the tablet is. Called on sign-out, an ending sign-in, an account removed,
        disabled or no longer a wall tablet's, at start and every hour (sign-ins that expired). Each
        connection that keeps devices per tablet account says which accounts (tablet_accounts) and drops
        one's devices and saved data (forget_account). Smart charging settings stay: signing the account
        in again brings them back. A wall-tablet sign-in of an account that isn't a wall tablet's (made
        with a person's account before the wall page refused those, or the account's role changed) ends
        here too."""
        now = time.time()
        users = {u["id"]: u for u in h.store.users()}
        for s in h.store.sessions():
            u = users.get(s["user_id"])
            if s["kind"] == "tablet" and u and u["role"] != "tablet":
                h.store.remove_session(s["id"])
        signed_in = {s["user_id"] for s in h.store.sessions()
                     if s["kind"] == "tablet" and A.session_accepted(s, users.get(s["user_id"]), now)}
        names: dict[str, str] = {}
        removed: list[str] = []
        for inst in list(h.integrations.instances.values()):
            accounts = getattr(inst, "tablet_accounts", None)
            if not accounts:
                continue
            for uid in sorted(accounts() - signed_in):
                name, ids = inst.forget_account(uid)
                names[uid] = names.get(uid) or (users.get(uid) or {}).get("name") or name
                removed += ids
        for eid in removed:
            h.store.forget_entity(eid)  # a name or settings given to it go too, as for any removed device
        if removed:
            h.hub.set_name_overrides(h.store.entity_names())
        for uid, name in names.items():
            what = f"{name}'s tablet" if name else "a wall tablet"
            log.info("Removed %s (%s): no longer signed in on a wall tablet", what, uid)
            h.store.log_event("system", f"Removed {what}: no longer signed in on a tablet")
    h.forget_signed_out_tablets = forget_signed_out_tablets  # also at start and hourly (app.py)

    @r.patch("/api/users/{uid}")
    async def update_user(req: web.Request) -> web.Response:
        require_admin(req)
        uid, data = req.match_info["uid"], await body(req)
        u = h.store.user(uid)
        if not u:
            return _err(404, "Unknown account")
        admins = [x for x in h.store.users() if x["role"] == "admin" and not x["disabled"]]
        demoting = (data.get("role") in ("family", "guest", "tablet") or data.get("disabled")) and u["role"] == "admin"
        if demoting and len(admins) <= 1:
            return _err(400, "Newman needs at least one admin")
        changes: dict[str, Any] = {}
        if data.get("name"):
            changes["name"] = str(data["name"]).strip()[:48]
        if "username" in data:
            # The sign-in name. Sessions belong to the account, not the name, so its devices stay signed in.
            username = str(data.get("username") or "").strip().lower()
            if not re.fullmatch(r"[a-z0-9._-]{2,32}", username):
                return _err(400, "Usernames are 2-32 letters, digits, dots, dashes or underscores")
            other = h.store.user_by_name(username)
            if other and other["id"] != uid:
                return _err(409, "That username is taken")
            if username != u["username"]:
                changes["username"] = username
        if data.get("role") in A.ROLES and data["role"] != u["role"]:
            changes["role"] = data["role"]
            if data["role"] in A.NOT_PEOPLE:
                # The person (and their phones) stay; the tablet / guest just isn't them.
                changes["person_id"] = None
                if data["role"] == "tablet":
                    h.store.remove_sessions(uid)  # its phone/browser sign-ins no longer apply
            elif u["role"] in A.NOT_PEOPLE:
                changes["person_id"] = link_person(changes.get("name") or u["name"])
        if isinstance(data.get("location"), dict):
            lc = data["location"]
            if "mode" in lc:
                if lc["mode"] not in ("off", "choice", "required"):
                    return _err(400, "Location sharing is off, choice or required")
                changes["loc_mode"] = lc["mode"]
                if lc["mode"] == "required":
                    changes.update(loc_enabled=1, loc_required_by=person_name_of(req["user"]))
                elif lc["mode"] == "off":
                    changes["loc_enabled"] = 0
            if "visibleTo" in lc:
                vis = lc["visibleTo"]
                tablets = {x["id"] for x in h.store.users() if x["role"] == "tablet"}
                # People in the household and wall tablets' accounts.
                if vis is not None and (not isinstance(vis, list) or
                                        not all(h.store.person(str(x)) or str(x) in tablets for x in vis)):
                    return _err(400, "Choose who can see them from the household and wall tablets")
                changes["loc_visible"] = None if vis is None else json.dumps([str(x) for x in vis])
        if "canAutomate" in data:
            role = changes.get("role") or u["role"]
            if not isinstance(data["canAutomate"], bool) or role == "tablet":
                return _err(400, "\"Make & change automations\" is true or false, for anyone but a wall tablet")
            changes["can_automate"] = int(data["canAutomate"])
        if "seesPeople" in data:
            if not isinstance(data["seesPeople"], bool) or (changes.get("role") or u["role"]) != "tablet":
                return _err(400, "Only a wall tablet's account has \"Can see where people are\" (true or false)")
            changes["tablet_people"] = int(data["seesPeople"])
        before = viewers_of(u.get("person_id"))
        mine = {ws for ws in list(sockets) if sockets[ws][0]["id"] == uid}  # this account's own sockets
        was_sharing = loc.sharing(u)
        if "disabled" in data:
            changes["disabled"] = int(bool(data["disabled"]))
        if data.get("password"):
            if problem := A.password_problem(str(data["password"])):
                return _err(400, problem)
            changes.update(password_hash=A.hash_secret(str(data["password"])), password_changed_at=time.time())
        if data.get("resetTwoFactor"):
            changes.update(totp_enabled=0, totp_secret=None, recovery="[]")
        h.store.update_user(uid, **changes)
        if changes.get("name") and changes["name"] != u["name"]:
            await rename_account_devices(uid, u["name"], changes["name"])
        if changes.get("disabled") or "password_hash" in changes:
            h.store.remove_sessions(uid)
        forget_signed_out_tablets()  # disabled, a new password or no longer a wall tablet's signs its wall tablet out
        now = h.store.user(uid)
        if was_sharing and not loc.sharing(now):
            loc.forget(u.get("person_id"))  # nothing kept once they stop sharing
        if u.get("person_id"):
            await push_person(u["person_id"], before)
        if "tablet_people" in changes:  # the tablet's People tile fills in (or goes quiet) straight away
            for p in h.store.people():
                await push_to(p, mine)
        return web.json_response(user_json(now))

    @r.get("/api/tablets")
    async def tablets(_: web.Request) -> web.Response:
        """Wall tablets' accounts - what "show on wall tablet" in an automation can pick."""
        return web.json_response([{"id": u["id"], "name": u["name"]} for u in h.store.users()
                                  if u["role"] == "tablet" and not u["disabled"]])

    def tablet_account(uid: str) -> dict[str, Any]:
        u = h.store.user(uid)
        if not u or u["role"] != "tablet":
            raise web.HTTPNotFound(text='{"error": "Unknown wall tablet account"}', content_type="application/json")
        return u

    @r.get("/api/charging/{uid}")
    async def charging_get(req: web.Request) -> web.Response:
        """A wall tablet's smart charging (charging.py): its settings, its battery device, and the
        devices that can be its charger."""
        require_admin(req)
        tablet_account(req.match_info["uid"])
        return web.json_response(charging.to_json(req.match_info["uid"]))

    @r.patch("/api/charging/{uid}")
    async def charging_set(req: web.Request) -> web.Response:
        require_admin(req)
        uid = req.match_info["uid"]
        tablet_account(uid)
        s, errors = charging_settings(await body(req), charging.settings(uid), h.hub)
        if errors:
            return _err(400, "; ".join(errors))
        await charging.update(uid, s)
        return web.json_response(charging.to_json(uid))

    @r.post("/api/charging/{uid}/answer")
    async def charging_answer(req: web.Request) -> web.Response:
        """Admins answer "which plug is it on?" (to_json's question): {"question": id, "plug": one of its
        choices' ids, or null for none of these}. Answers with the smart charging settings."""
        require_admin(req)
        uid = req.match_info["uid"]
        tablet_account(uid)
        data = await body(req)
        plug = data.get("plug")
        try:
            await charging.answer(uid, str(data.get("question") or ""), str(plug) if plug else None, req["user"]["name"])
        except ActionError as e:
            return _err(409, str(e))
        return web.json_response(charging.to_json(uid))

    @r.delete("/api/users/{uid}")
    async def remove_user(req: web.Request) -> web.Response:
        require_admin(req)
        uid = req.match_info["uid"]
        if uid == req["user"]["id"]:
            return _err(400, "You can't remove your own account")
        u = h.store.user(uid)
        if u and u["role"] == "admin" and sum(1 for x in h.store.users() if x["role"] == "admin") <= 1:
            return _err(400, "Newman needs at least one admin")
        h.store.remove_user(uid)
        widget_push.tokens.prune()
        live.tokens.prune()
        forget_signed_out_tablets()
        if u and u.get("person_id") and not loc.account(u["person_id"]):
            loc.forget(u["person_id"])  # no account sharing for them any more: keep nothing
        return web.json_response({"ok": True})

    # -- dashboards ---------------------------------------------------------------------------

    def own_dashboard(req: web.Request) -> dict[str, Any]:
        d = h.store.dashboard(req.match_info["did"])
        if not d or (d["userId"] != req["user"]["id"] and req["user"]["role"] != "admin"):
            raise web.HTTPNotFound(text='{"error": "Unknown dashboard"}', content_type="application/json")
        return d

    @r.get("/api/dashboards/tiles")
    async def dashboard_tiles(_: web.Request) -> web.Response:
        return web.json_response({"cols": D.GRID_COLS, "rows": D.GRID_ROWS, "themes": list(D.THEMES),
                                  "types": D.TILE_TYPES})

    @r.get("/api/dashboards/today-page")
    async def dashboard_today_page(_: web.Request) -> web.Response:
        """A ready-made Today page (greeting, To do, agenda, weather) for the editor's "Add a Today page"."""
        return web.json_response(D.today_page(h.hub))

    @r.get("/api/dashboards")
    async def dashboards(req: web.Request) -> web.Response:
        everyone = req.query.get("all") and req["user"]["role"] == "admin"
        return web.json_response(h.store.dashboards(None if everyone else req["user"]["id"]))

    @r.get("/api/dashboards/current")
    async def dashboard_current(req: web.Request) -> web.Response:
        """What this device shows: the one chosen for it, else the person's default. The first
        time, a starter dashboard is laid out from the house's own devices."""
        u, s = req["user"], req["session"]
        d = h.store.dashboard(s["dashboard_id"]) if s.get("dashboard_id") else None
        if not d:
            d = next((x for x in h.store.dashboards(u["id"]) if x["isDefault"]), None)
        if not d:
            if u["id"] == MAINTENANCE["id"]:
                return web.json_response(D.starter(h.hub))  # scripts looking: show one, keep nothing
            d = h.store.save_dashboard(u["id"], D.starter(h.hub))
        return web.json_response(d)

    @r.post("/api/dashboards")
    async def dashboard_add(req: web.Request) -> web.Response:
        """{"name", "pages", …} - a new dashboard; no pages: a starter one. {"copyFrom": id, "name"?} - a copy
        of one of yours (an admin: anyone's) with everything on it, its Christmas pictures included."""
        data = await body(req)
        source = None
        if data.get("copyFrom"):
            source = h.store.dashboard(str(data["copyFrom"]))
            if not source or (source["userId"] != req["user"]["id"] and req["user"]["role"] != "admin"):
                return _err(404, "Unknown dashboard")
            clean, _ = D.clean({**source, "name": str(data.get("name") or f"Copy of {source['name']}")})
        elif data.get("pages"):
            clean, errors = D.clean(data)
            if errors:
                return web.json_response({"error": errors[0], "errors": errors}, status=400)
            clean["christmas"]["pictures"] = []  # pictures come only through /pictures (or a copy)
        else:
            clean = D.starter(h.hub, str(data.get("name") or "Wall tablet")[:40])
        owner = req["user"]["id"]
        if data.get("userId") and data["userId"] != owner:
            # An admin lays out another account's dashboard - e.g. a wall tablet's, which can't edit its own.
            require_admin(req)
            if not h.store.user(str(data["userId"])):
                return _err(404, "Unknown account")
            owner = str(data["userId"])
        pictures = clean["christmas"]["pictures"] if source else []
        clean["christmas"]["pictures"] = []
        saved = h.store.save_dashboard(owner, clean)
        if pictures:
            clean["christmas"]["pictures"] = await asyncio.to_thread(WP.copy_all, config.data_dir, source["id"],
                                                                     saved["id"], pictures)
            saved = h.store.save_dashboard(owner, clean, saved["id"])
        return web.json_response(saved)

    @r.get("/api/dashboards/{did}")
    async def dashboard_get(req: web.Request) -> web.Response:
        return web.json_response(own_dashboard(req))

    @r.put("/api/dashboards/{did}")
    async def dashboard_put(req: web.Request) -> web.Response:
        d = own_dashboard(req)
        clean, errors = D.clean(await body(req))
        if errors:
            return web.json_response({"error": errors[0], "errors": errors}, status=400)
        # Its pictures come and go only through /pictures (below): what an editor sends is ignored.
        clean["christmas"]["pictures"] = d["christmas"]["pictures"]
        saved = h.store.save_dashboard(d["userId"], clean, d["id"])
        await broadcast({"type": "dashboard", "id": d["id"], "userId": d["userId"]})  # tablets reload it
        return web.json_response(saved)

    @r.post("/api/dashboards/{did}/default")
    async def dashboard_default(req: web.Request) -> web.Response:
        d = own_dashboard(req)
        h.store.set_default_dashboard(d["userId"], d["id"])
        await broadcast({"type": "dashboard", "userId": d["userId"]})
        return web.json_response({"ok": True})

    @r.delete("/api/dashboards/{did}")
    async def dashboard_delete(req: web.Request) -> web.Response:
        d = own_dashboard(req)
        h.store.remove_dashboard(d["id"])
        WP.remove_all(config.data_dir, d["id"])
        await broadcast({"type": "dashboard", "userId": d["userId"]})
        return web.json_response({"ok": True})

    # -- a dashboard's Christmas pictures (wall_pictures.py) ----------------------------------------

    async def pictures_changed(d: dict[str, Any], pics: list[str]) -> web.Response:
        body_ = {k: v for k, v in d.items() if k not in ("id", "userId", "isDefault", "createdAt", "updatedAt")}
        body_["christmas"] = {**d["christmas"], "pictures": pics}
        h.store.save_dashboard(d["userId"], body_, d["id"])
        await broadcast({"type": "dashboard", "id": d["id"], "userId": d["userId"]})
        return web.json_response({"pictures": pics})

    @r.post("/api/dashboards/{did}/pictures")
    async def picture_add(req: web.Request) -> web.Response:
        d = own_dashboard(req)
        pics = list(d["christmas"]["pictures"])
        if len(pics) >= D.MAX_PICTURES:
            return _err(409, f"A dashboard has room for {D.MAX_PICTURES} pictures - remove one first")
        if (req.content_type or "").lower() not in WP.TYPES:
            return _err(415, "Send the picture itself as image/png or image/webp")
        raw = bytearray()  # read by hand: the app's usual body limit is far smaller
        while len(raw) <= WP.MAX_BYTES:
            chunk = await req.content.read(65536)
            if not chunk:
                break
            raw += chunk
        if len(raw) > WP.MAX_BYTES:
            return _err(413, "That picture is over 2 MB")
        try:
            png = await asyncio.to_thread(WP.make, bytes(raw))
        except ValueError as e:
            return _err(400, str(e))
        pid = WP.save(config.data_dir, d["id"], png)
        return await pictures_changed(d, pics if pid in pics else [*pics, pid])

    @r.delete("/api/dashboards/{did}/pictures/{pic}")
    async def picture_remove(req: web.Request) -> web.Response:
        d = own_dashboard(req)
        pic = req.match_info["pic"]
        if pic not in d["christmas"]["pictures"]:
            return _err(404, "No such picture")
        WP.remove(config.data_dir, d["id"], pic)
        return await pictures_changed(d, [p for p in d["christmas"]["pictures"] if p != pic])

    @r.get("/api/dashboards/{did}/pictures/{pic}")
    async def picture_get(req: web.Request) -> web.StreamResponse:
        """One of the dashboard's pictures (its owner - the wall tablet - or an admin). Cached long: the id
        is a hash of the picture."""
        d = own_dashboard(req)
        pic = req.match_info["pic"]
        p = WP.path(config.data_dir, d["id"], pic) if D.PICTURE_ID.match(pic) else None
        if not p or pic not in d["christmas"]["pictures"] or not p.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Content-Type": "image/png",
                                            "Cache-Control": "private, max-age=31536000, immutable"})

    # -- phone layouts (phone_layouts.py): a household default, and people's own ------------------

    def phone_accounts() -> list[dict[str, Any]]:
        """Accounts that have a phone layout: everyone but wall tablets' (and disabled) accounts."""
        return [u for u in h.store.users() if u["role"] != "tablet" and not u["disabled"]]

    def default_phone_layout() -> dict[str, Any]:
        """The household default. The first time, it's laid out from the first admin's default wall
        dashboard the way the phone apps drew it before (and kept, so phones look the same)."""
        saved = h.store.phone_layout(h.store.PHONE_DEFAULT)
        if saved:
            return PL.with_text_size(saved["layout"])
        admin = next((u for u in h.store.users() if u["role"] == "admin" and not u["disabled"]), None)
        dashes = h.store.dashboards(admin["id"]) if admin else []
        dash = next((d for d in dashes if d["isDefault"]), dashes[0] if dashes else None)
        if not dash:
            return PL.from_dashboard(D.starter(h.hub))  # nothing laid out yet: show one, keep nothing
        layout = PL.from_dashboard(dash)
        h.store.save_phone_layout(h.store.PHONE_DEFAULT, layout)
        return layout

    def phone_layout_of(uid: str) -> dict[str, Any]:
        own = h.store.phone_layout(uid) if uid else None
        if own:
            return {"source": "own", "layout": PL.with_text_size(own["layout"]), "updatedAt": own["updatedAt"]}
        return {"source": "default", "layout": default_phone_layout(), "updatedAt": None}

    def on_default() -> list[str]:
        own = h.store.phone_layout_times()
        return [u["id"] for u in phone_accounts() if u["id"] not in own]

    def no_tablet(req: web.Request) -> None:
        if req["session"]["kind"] == "tablet" or req["user"]["role"] == "tablet":
            raise web.HTTPForbidden(text='{"error": "A wall tablet has no phone layout"}', content_type="application/json")

    def phone_target(req: web.Request) -> dict[str, Any]:
        """The account in the path ("me" = yourself; anyone else: admins only)."""
        no_tablet(req)
        uid = req.match_info.get("uid", "me")
        if uid == "me" or uid == req["user"]["id"]:
            if req["user"]["id"] == MAINTENANCE["id"]:
                raise web.HTTPBadRequest(text='{"error": "Sign in as someone to have a phone layout"}',
                                         content_type="application/json")
            return req["user"]
        require_admin(req)
        u = h.store.user(uid)
        if not u or u["role"] == "tablet":
            raise web.HTTPNotFound(text='{"error": "Unknown account (wall tablets have no phone layout)"}',
                                   content_type="application/json")
        return u

    async def push_layout(user_ids: Any) -> None:
        """{"type": "dashboard"} to these accounts' open apps and pages: their phone layout changed."""
        ids = set(user_ids)
        for ws in [w for w, (u, _) in list(sockets.items()) if u["id"] in ids]:
            try:
                await ws.send_str(json.dumps({"type": "dashboard", "userId": sockets[ws][0]["id"], "phoneLayout": True}))
            except Exception:
                sockets.pop(ws, None)

    async def layout_body(req: web.Request) -> tuple[dict[str, Any], web.Response | None]:
        data = await body(req)
        layout, errors = PL.clean(data["layout"] if isinstance(data.get("layout"), dict) else data)
        if errors:
            return layout, web.json_response({"error": errors[0], "errors": errors}, status=400)
        return layout, None

    def person_layout_json(u: dict[str, Any]) -> dict[str, Any]:
        return {"userId": u["id"], "name": u["name"], **phone_layout_of(u["id"])}

    @r.get("/api/phone-layout")
    async def phone_layout(req: web.Request) -> web.Response:
        """What this phone shows: the person's own layout, else the household default."""
        no_tablet(req)
        mine = phone_layout_of(req["user"]["id"] if req["user"]["id"] != MAINTENANCE["id"] else "")
        return web.json_response({"source": mine["source"], "layout": mine["layout"]})

    @r.get("/api/phone-layouts")
    async def phone_layouts(req: web.Request) -> web.Response:
        require_admin(req)
        own = h.store.phone_layout_times()
        return web.json_response({"default": default_phone_layout(), "people": [
            {"userId": u["id"], "name": u["name"], "role": u["role"], "source": "own" if u["id"] in own else "default",
             "updatedAt": own.get(u["id"])} for u in phone_accounts()]})

    @r.get("/api/phone-layouts/default")
    async def phone_default_get(req: web.Request) -> web.Response:
        require_admin(req)
        saved = h.store.phone_layout(h.store.PHONE_DEFAULT)
        return web.json_response({"layout": default_phone_layout(), "updatedAt": saved and saved["updatedAt"],
                                  "using": len(on_default())})

    @r.put("/api/phone-layouts/default")
    async def phone_default_put(req: web.Request) -> web.Response:
        require_admin(req)
        layout, bad = await layout_body(req)
        if bad:
            return bad
        h.store.save_phone_layout(h.store.PHONE_DEFAULT, layout)
        using = on_default()
        await push_layout(using)  # everyone on the default sees it straight away
        saved = h.store.phone_layout(h.store.PHONE_DEFAULT)
        return web.json_response({"layout": PL.with_text_size(saved["layout"]), "updatedAt": saved["updatedAt"],
                                  "using": len(using)})

    @r.post("/api/phone-layouts/reset-all")
    async def phone_reset_all(req: web.Request) -> web.Response:
        require_admin(req)
        own = list(h.store.phone_layout_times())
        for uid in own:
            h.store.remove_phone_layout(uid)
        await push_layout(own)
        return web.json_response({"ok": True, "reset": len(own)})

    @r.get("/api/phone-layouts/{uid}")
    async def phone_person_get(req: web.Request) -> web.Response:
        return web.json_response(person_layout_json(phone_target(req)))

    @r.put("/api/phone-layouts/{uid}")
    async def phone_person_put(req: web.Request) -> web.Response:
        u = phone_target(req)
        layout, bad = await layout_body(req)
        if bad:
            return bad
        h.store.save_phone_layout(u["id"], layout)
        await push_layout([u["id"]])
        return web.json_response(person_layout_json(u))

    @r.post("/api/phone-layouts/{uid}/reset")
    async def phone_person_reset(req: web.Request) -> web.Response:
        u = phone_target(req)
        if h.store.remove_phone_layout(u["id"]):
            await push_layout([u["id"]])
        return web.json_response(person_layout_json(u))

    # -- meta / status ------------------------------------------------------------------

    @r.get("/health")
    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "version": config.app_version})

    @r.get("/api/meta")
    async def meta(_: web.Request) -> web.Response:
        return web.json_response(meta_json(config.app_version))

    # -- connecting apps (connect.py) ---------------------------------------------------------------

    def identity() -> tuple[str, str, str | None]:
        s = h.store.settings()
        return CN.hub_id(h.store), CN.home_name(s), CN.outside_url(s, config.public_url)

    def lan_urls() -> list[str]:
        return CN.local_urls(CN.lan_ipv4s(only=config.mdns_interface), config.port)

    @r.get("/api/hello")
    async def hello(_: web.Request) -> web.Response:
        """No sign-in: which hub this is, for an app connecting to it. Nothing secret. webBuild: the web
        build it serves (pages compare it with their own and reload)."""
        hid, name, outside = identity()
        return web.json_response({**CN.hello_json(hid, name, config.app_version, outside, lan_urls()), "webBuild": web_build},
                                 headers={"Cache-Control": "no-store"})

    @r.get("/api/connect-info")
    async def connect_info(req: web.Request) -> web.Response:
        """The connect link (and its parts) shown as a QR code for setting up a phone or tablet."""
        require_admin(req)
        hid, name, outside = identity()
        return web.json_response(CN.connect_info(hid, name, config.app_version, outside, lan_urls()))

    @r.get("/api/status")
    async def status(_: web.Request) -> web.Response:
        return web.json_response({
            "version": config.app_version, "uptime": time.time() - STARTED,
            "integrations": [{k: i[k] for k in ("id", "type", "name", "status", "detail")} for i in h.integrations.list()],
            "devices": len(h.hub.all()), "people": len(h.store.people()), "automations": len(h.engine.autos),
            "openRuns": len(h.engine.open_runs), "nextRuns": h.engine.next_runs()[:5],
        })

    # -- entities ----------------------------------------------------------------------------

    @r.get("/api/entities")
    async def entities(req: web.Request) -> web.Response:
        origins = video.origins()
        return web.json_response([entity_json(e, origins) for e in h.hub.all(req.query.get("kind"))])

    @r.patch("/api/entities/{eid}")
    async def rename_entity(req: web.Request) -> web.Response:
        eid, data = req.match_info["eid"], await body(req)
        if not h.hub.get(eid) or not str(data.get("name", "")).strip():
            return _err(400, "unknown device or empty name")
        name = str(data["name"]).strip()[:64]
        h.store.rename_entity(eid, name)
        # Through update() so everything watching (wall tablets, phones, open dashboards) gets the new name
        # straight away - set_name_overrides alone changes it silently (seen live: the wall kept the old name).
        await h.hub.update(eid, name=name)
        h.hub.set_name_overrides(h.store.entity_names())
        return web.json_response(entity_json(h.hub.get(eid)))

    @r.put("/api/entities/{eid}/video")
    async def entity_video(req: web.Request) -> web.Response:
        """Admins: a device's video settings - any of {"tileStream", "fullStream", "videoFrom"} (an id,
        or null for Automatic / its own; a "videoFrom" device whose video is another's means that one) and
        "showInCameras" (true/false). Everyone's screens are told."""
        require_admin(req)
        eid = req.match_info["eid"]
        if not h.hub.get(eid):
            return _err(404, "Unknown device")
        try:
            changed = video.save(eid, await body(req))
        except ValueError as e:
            return _err(400, str(e))
        for cid in changed:
            if ent := h.hub.get(cid):
                await broadcast({"type": "entity", "entity": entity_json(ent)})
        return web.json_response(entity_json(h.hub.get(eid)))

    @r.put("/api/entities/{eid}/live-status")
    async def entity_live_status(req: web.Request) -> web.Response:
        """Admins: a device's "Live status while…" - any of {"enabled": bool, "states": [state ids from
        its kind], "recipients": null (everyone) | [person ids]}. Answers the device (its liveStatus)."""
        require_admin(req)
        eid = req.match_info["eid"]
        if not h.hub.get(eid):
            return _err(404, "Unknown device")
        try:
            live.save_setting(eid, await body(req))
        except ValueError as e:
            return _err(400, str(e))
        ent = h.hub.get(eid)
        await broadcast({"type": "entity", "entity": entity_json(ent)})
        return web.json_response(entity_json(ent))

    # -- a remote's Controls (controls.py) ---------------------------------------------------------

    def controls_json(ent: Entity) -> dict[str, Any]:
        c = h.engine.controls
        return {"controls": c.get(ent.id), "rows": c.rows(ent), "buttons": C.buttons(ent),
                "choices": c.choices(ent)}

    @r.get("/api/entities/{eid}/controls")
    async def get_controls(req: web.Request) -> web.Response:
        """A remote's Controls with what each press does now, its buttons, and the devices / automations
        a press can be set to (for the editor). Not for wall tablets."""
        if req["session"]["kind"] == "tablet":
            return _err(403, "Change this from the Newman dashboard, not the wall tablet")
        ent = h.hub.get(req.match_info["eid"])
        if not ent:
            return _err(404, "Unknown device")
        return web.json_response(controls_json(ent))

    @r.put("/api/entities/{eid}/controls")
    async def put_controls(req: web.Request) -> web.Response:
        """Admins: {"lights": [ids], "onLevel": 1-100 | null, "hold": bool, "buttons": {press: override}}."""
        require_admin(req)
        ent = h.hub.get(req.match_info["eid"])
        if not ent:
            return _err(404, "Unknown device")
        try:
            h.engine.controls.set(ent.id, await body(req))
        except ValueError as e:
            return _err(400, str(e))
        await broadcast({"type": "entity", "entity": entity_json(ent)})
        return web.json_response(controls_json(ent))

    @r.delete("/api/entities/{eid}/controls")
    async def delete_controls(req: web.Request) -> web.Response:
        require_admin(req)
        ent = h.hub.get(req.match_info["eid"])
        if not ent:
            return _err(404, "Unknown device")
        h.engine.controls.remove(ent.id)
        await broadcast({"type": "entity", "entity": entity_json(ent)})
        return web.json_response(controls_json(ent))

    @r.post("/api/entities/replace")
    async def replace_entities(req: web.Request) -> web.Response:
        """{"pairs": [{"from": old device id, "to": new device id}]}: everything that used the old devices
        (automations, dashboards, names, upkeep items…) uses the new ones from now on."""
        require_admin(req)
        try:
            counts = await R.replace_entities(h, (await body(req)).get("pairs"))
        except ValueError as e:
            return _err(400, str(e))
        for uid in counts.pop("dashboardOwners", []):
            await broadcast({"type": "dashboard", "userId": uid})
        h.store.log_event("system", "Devices swapped", f"{counts['references']} uses moved to the new devices "
                          f"by {req['user']['name']}")
        return web.json_response({"ok": True, **counts})

    @r.post("/api/entities/remove")
    async def remove_entities(req: web.Request) -> web.Response:
        """Admins: {"ids": [...], "dryRun"?: true, "force"?: true} - take offline / unavailable devices off
        the hub for good (names and settings given to them go too). Answers {"removed": [ids], "devices":
        [{id, name, usedBy: [{type, id, name}]}]}. 400 when a device is unknown or online (nothing removed);
        409 with "devices" when one is still used by automations, dashboards, phone layouts or widgets and
        "force" isn't set; "dryRun" only reports. A connection that reports one again later brings it back
        as a new device."""
        require_admin(req)
        data = await body(req)
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            return _err(400, "Say which devices: ids, a list of device ids")
        ids = list(dict.fromkeys(ids))
        if unknown := [i for i in ids if not h.hub.get(i)]:
            return _err(400, f"Unknown device{'s' if len(unknown) != 1 else ''}: {', '.join(unknown[:5])}")
        if online := [h.hub.name(i) for i in ids if not h.hub.get(i).offline]:
            names = ", ".join(online[:5]) + (f" and {len(online) - 5} more" if len(online) > 5 else "")
            return _err(400, f"Only offline or unavailable devices can be removed - "
                             f"{names} {'is' if len(online) == 1 else 'are'} online")
        devices = [{"id": i, "name": h.hub.name(i), "usedBy": R.references(h, i)} for i in ids]
        if data.get("dryRun"):
            return web.json_response({"ok": True, "removed": [], "devices": devices})
        if any(d["usedBy"] for d in devices) and not data.get("force"):
            return web.json_response({"ok": False, "error": "Some of these devices are still in use",
                                      "removed": [], "devices": devices}, status=409)
        for i in ids:
            h.hub.remove(i)  # the screens drop it ("entity-removed")
            h.store.forget_entity(i)
            h.engine.controls.remove(i)  # a remote that's gone takes its Controls with it
            muting.forget(i)
            muting.warnings.forget(i)
            groups.forget(i)
            cats.forget(i)
        h.hub.set_name_overrides(h.store.entity_names())
        for lid in video.forget(ids):
            if ent := h.hub.get(lid):
                await broadcast({"type": "entity", "entity": entity_json(ent)})
        what = devices[0]["name"] if len(devices) == 1 else f"{len(devices)} devices"
        h.store.log_event("system", f"Removed {what}", f"By {req['user']['name']}" + (
            f": {', '.join(d['name'] for d in devices[:10])}" if len(devices) > 1 else ""))
        return web.json_response({"ok": True, "removed": ids, "devices": devices})

    @r.put("/api/entities/{eid}/mute")
    async def mute_issue(req: web.Request) -> web.Response:
        """{"issue": id, "for": "day"|"week"|"fixed"|"always"}: one of its current problems stops being
        mentioned (muting.py). Anyone signed in, wall tablets too."""
        ent = h.hub.get(req.match_info["eid"])
        if not ent:
            return _err(404, "Unknown device")
        data = await body(req)
        issue, mode = str(data.get("issue") or ""), str(data.get("for") or "")
        try:
            text = muting.mute(ent, issue, mode, req["user"]["name"])
        except ValueError as e:
            return _err(400, str(e))
        how = {"day": "for a day", "week": "for a week", "fixed": "until it's fixed", "always": "always"}[mode]
        h.store.log_event("system", f"{req['user']['name']} muted “{text}” on {ent.name} ({how})", entity_id=ent.id)
        await broadcast({"type": "entity", "entity": entity_json(ent)})
        return web.json_response({"health": muting.health(ent)})

    @r.delete("/api/entities/{eid}/mute/{issue}")
    async def unmute_issue(req: web.Request) -> web.Response:
        ent = h.hub.get(req.match_info["eid"])
        if not ent:
            return _err(404, "Unknown device")
        issue = req.match_info["issue"]
        if muting.unmute(ent, issue):
            text = dict(ent.health_items).get(issue) or issue.replace("_", " ")
            h.store.log_event("system", f"{req['user']['name']} unmuted “{text}” on {ent.name}", entity_id=ent.id)
            await broadcast({"type": "entity", "entity": entity_json(ent)})
        return web.json_response({"health": muting.health(ent)})

    # -- a device's warnings (its Warnings tab), rooms and groups -----------------------------------

    @r.get("/api/entities/{eid}/warnings")
    async def get_warnings(req: web.Request) -> web.Response:
        """{"batteryLow": its own level | null, "houseBatteryLow", "hasBattery", "warnings": [{"id", "label",
        "on"}]} - every problem it can report and whether it's switched on."""
        ent = h.hub.get(req.match_info["eid"])
        if not ent:
            return _err(404, "Unknown device")
        return web.json_response(muting.warnings.json(ent, house.get()["devices"]))

    @r.put("/api/entities/{eid}/warnings")
    async def put_warnings(req: web.Request) -> web.Response:
        """Admin: {"batteryLow": 5-50 | null, "warnings": {issue id: bool}} (either or both)."""
        require_admin(req)
        ent = h.hub.get(req.match_info["eid"])
        if not ent:
            return _err(404, "Unknown device")
        try:
            muting.warnings.set(ent, await body(req))
        except ValueError as e:
            return _err(400, str(e))
        await broadcast({"type": "entity", "entity": entity_json(ent)})
        home_changed()
        return web.json_response(muting.warnings.json(ent, house.get()["devices"]))

    @r.put("/api/entities/{eid}/room")
    async def put_room(req: web.Request) -> web.Response:
        """Admin: {"room": "Kitchen" | null} - the device's room in Newman (null: its connection's again)."""
        require_admin(req)
        eid = req.match_info["eid"]
        if not h.hub.get(eid):
            return _err(404, "Unknown device")
        await groups.set_room(eid, (await body(req)).get("room"))
        return web.json_response(entity_json(h.hub.get(eid)))

    @r.get("/api/rooms")
    async def rooms(_: web.Request) -> web.Response:
        """Every room in use, for pickers."""
        return web.json_response(groups.rooms())

    @r.get("/api/groups")
    async def list_groups(_: web.Request) -> web.Response:
        return web.json_response([groups.json(gid) for gid in groups.all()])

    @r.post("/api/groups")
    async def create_group(req: web.Request) -> web.Response:
        """Admin: {"name", "members": [light / switch ids], "room"?} -> the group."""
        require_admin(req)
        try:
            gid = await groups.create(await body(req))
        except ValueError as e:
            return _err(400, str(e))
        h.store.log_event("system", f"Made the group {groups.all()[gid]['name']}", f"By {req['user']['name']}", gid)
        return web.json_response(groups.json(gid))

    @r.put("/api/groups/{gid}")
    async def update_group(req: web.Request) -> web.Response:
        require_admin(req)
        gid = req.match_info["gid"]
        try:
            await groups.update(gid, await body(req))
        except KeyError:
            return _err(404, "Unknown group")
        except ValueError as e:
            return _err(400, str(e))
        return web.json_response(groups.json(gid))

    @r.delete("/api/groups/{gid}")
    async def delete_group(req: web.Request) -> web.Response:
        require_admin(req)
        gid = req.match_info["gid"]
        name = (groups.all().get(gid) or {}).get("name")
        if not await groups.delete(gid):
            return _err(404, "Unknown group")
        h.store.log_event("system", f"Removed the group {name}", f"By {req['user']['name']}")
        return web.json_response({"ok": True})

    async def touched(ids: list[str]) -> None:
        for eid in ids:
            if ent := h.hub.get(eid):
                await broadcast({"type": "entity", "entity": entity_json(ent)})
        home_changed()
    groups.touched.append(touched)

    # -- house settings (the web's Settings page) ---------------------------------------------------------

    @r.get("/api/settings/house")
    async def get_house(_: web.Request) -> web.Response:
        return web.json_response(house.get())

    @r.patch("/api/settings/house")
    async def patch_house(req: web.Request) -> web.Response:
        """Admin: any part, e.g. {"lights": {"step": 5}} -> all the settings."""
        require_admin(req)
        try:
            s = house.patch(await body(req))
        except ValueError as e:
            return _err(400, str(e))
        await broadcast({"type": "settings-house", "settings": s})
        home_changed()
        return web.json_response(s)

    # -- quiet hours (each person's Profile) ---------------------------------------------------------------

    def own_person(req: web.Request) -> str:
        pid = req["user"].get("person_id")
        if not pid:
            raise web.HTTPBadRequest(text='{"error": "This account isn\'t a person, so it has no quiet hours"}',
                                     content_type="application/json")
        return pid

    @r.get("/api/me/quiet-hours")
    async def get_quiet(req: web.Request) -> web.Response:
        return web.json_response(quiet.get(own_person(req)))

    @r.put("/api/me/quiet-hours")
    async def put_quiet(req: web.Request) -> web.Response:
        """{"enabled", "from": "22:00", "to": "07:00", "days": "all"|"weekdays"|"weekends",
        "allow": [alarm, doorbell, people, urgent]} - their phones get the rest silently in that window."""
        pid = own_person(req)
        try:
            return web.json_response(quiet.set(pid, await body(req)))
        except ValueError as e:
            return _err(400, str(e))

    # -- the phone's Home screen (home.py) -----------------------------------------------------------------

    def today_for(u: dict[str, Any]) -> dict[str, Any]:
        st = h.store.settings()
        return T.build(h.hub, st, person_id=u.get("person_id"), dismissed=st.get("todayDismissed") or {})

    def people_brief(viewer: dict[str, Any], session: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for p in h.store.people():
            pj = loc.person_json(p, viewer, session)
            out.append({"name": p["name"], "home": bool(pj.get("home"))})
        return out

    home = h.home = HM.Home(h.hub, h.store, groups, house, muting, today=lambda _u: {}, people=lambda: [], cats=cats)

    @r.get("/api/home")
    async def get_home(req: web.Request) -> web.Response:
        u, s = req["user"], req["session"]
        if s["kind"] == "tablet":
            return _err(403, "The phone's Home is for phones")
        home.people = lambda: people_brief(u, s)
        return web.json_response(home.json(u, can_automate(u), today_for(u)))

    @r.put("/api/home/me")
    async def put_home_mine(req: web.Request) -> web.Response:
        """{"cards": [{"id", "on"}…in order], "favourites": [ids, ≤4]} - this account's Edit Home."""
        u, s = req["user"], req["session"]
        if s["kind"] == "tablet":
            return _err(403, "The phone's Home is for phones")
        if not home.can_edit(u):
            return _err(403, "Your Home is set by the household - ask an admin")
        try:
            home.set_mine(u["id"], await body(req))
        except ValueError as e:
            return _err(400, str(e))
        await send_to_user(u["id"], {"type": "home"})
        home.people = lambda: people_brief(u, s)
        return web.json_response(home.json(u, can_automate(u), today_for(u)))

    @r.put("/api/home/climate")
    async def put_home_climate(req: web.Request) -> web.Response:
        """{"rooms": [{"entity", "name"}…in order]} - the household's Climate rooms (any temperature sensor)."""
        if req["session"]["kind"] == "tablet":
            return _err(403, "Change this from a phone or the dashboard")
        try:
            home.set_climate(await body(req))
        except ValueError as e:
            return _err(400, str(e))
        home_changed()
        u, s = req["user"], req["session"]
        home.people = lambda: people_brief(u, s)
        return web.json_response(home.json(u, can_automate(u), today_for(u)))

    # -- the household's Home and who may change their own (Dashboards > Phone Home; admins) -------------

    @r.get("/api/home/layouts")
    async def home_layouts(req: web.Request) -> web.Response:
        require_admin(req)
        return web.json_response(home.layouts(h.store.users(), today_for(req["user"])))

    @r.put("/api/home/household")
    async def put_home_household(req: web.Request) -> web.Response:
        """{"cards": [{"id", "on"}…in order], "favourites": [ids, ≤4]} - every Home that isn't its own."""
        require_admin(req)
        try:
            home.set_household(await body(req))
        except ValueError as e:
            return _err(400, str(e))
        await broadcast({"type": "home"})
        return web.json_response(home.layouts(h.store.users(), today_for(req["user"])))

    def home_person(uid: str) -> dict[str, Any]:
        u = h.store.user(uid)
        if not u or u.get("role") == "tablet":
            raise web.HTTPNotFound(text='{"error": "No such person"}', content_type="application/json")
        return u

    @r.post("/api/home/people/{uid}/reset")
    async def reset_home_person(req: web.Request) -> web.Response:
        """Drops their own layout: their Home is the household's again."""
        require_admin(req)
        u = home_person(req.match_info["uid"])
        home.reset(u["id"])
        await send_to_user(u["id"], {"type": "home"})
        return web.json_response(home.layouts(h.store.users(), today_for(req["user"])))

    @r.patch("/api/home/people/{uid}")
    async def patch_home_person(req: web.Request) -> web.Response:
        """{"canEditHome": bool} - whether they may change their own Home (admins always may)."""
        require_admin(req)
        u = home_person(req.match_info["uid"])
        data = await body(req)
        if not isinstance(data.get("canEditHome"), bool):
            return _err(400, 'Send {"canEditHome": true} or false')
        if u["role"] == "admin" and not data["canEditHome"]:
            return _err(400, "An admin can always change their own Home")
        home.set_can_edit(u["id"], data["canEditHome"])
        await send_to_user(u["id"], {"type": "home"})
        return web.json_response(home.layouts(h.store.users(), today_for(req["user"])))

    # -- categories and a device's category / icon (categories.py) ------------------------------------

    @r.get("/api/categories")
    async def categories_list(_: web.Request) -> web.Response:
        return web.json_response(cats.list())

    @r.put("/api/categories/{cid}")
    async def category_rename(req: web.Request) -> web.Response:
        """{"name"} - renames a category made in Newman."""
        require_admin(req)
        try:
            info = cats.rename(req.match_info["cid"], (await body(req)).get("name"))
        except ValueError as e:
            return _err(400, str(e))
        await broadcast({"type": "categories"})
        home_changed()
        return web.json_response(info)

    async def category_moved(ent: Entity) -> None:
        await broadcast({"type": "entity", "entity": entity_json(ent)})
        await broadcast({"type": "categories"})
        home_changed()

    @r.put("/api/entities/{eid}/category")
    async def entity_category(req: web.Request) -> web.Response:
        """{"category": id | null, "newName"?: str} - moves a device into a category (null: back to its
        type's); newName: into a new category of that name. Answers the device."""
        require_admin(req)
        ent = h.hub.get(req.match_info["eid"])
        if not ent:
            return _err(404, "Unknown device")
        data = await body(req)
        try:
            cats.set_category(ent, data.get("category"), data.get("newName"))
        except ValueError as e:
            return _err(400, str(e))
        await category_moved(ent)
        return web.json_response(entity_json(ent))

    @r.put("/api/entities/{eid}/icon")
    async def entity_icon(req: web.Request) -> web.Response:
        """{"icon": name | null} - the icon it's shown with (null: its type's). Answers the device."""
        require_admin(req)
        ent = h.hub.get(req.match_info["eid"])
        if not ent:
            return _err(404, "Unknown device")
        try:
            cats.set_icon(ent.id, (await body(req)).get("icon"))
        except ValueError as e:
            return _err(400, str(e))
        await category_moved(ent)
        return web.json_response(entity_json(ent))

    home_pending: dict[str, Any] = {"task": None, "last": 0.0}

    def home_changed() -> None:
        """Tells the phones' Home screens to read it again - at most once a second."""
        if home_pending["task"] and not home_pending["task"].done():
            return

        async def later() -> None:
            await asyncio.sleep(max(0.0, 1.0 - (time.time() - home_pending["last"])))
            home_pending["last"] = time.time()
            await broadcast({"type": "home"})
        try:
            home_pending["task"] = asyncio.get_running_loop().create_task(later())
        except RuntimeError:
            pass

    async def home_on_change(old: Entity | None, new: Entity) -> None:
        # Any card may show a change of state; readings and brightness only show on the purpose-built screens.
        if old is None or old.state != new.state or old.available != new.available or \
                (CT.screen_of(new.category) is not None and old.attrs != new.attrs):
            home_changed()

    @r.post("/api/entities/{eid}/command")
    async def command(req: web.Request) -> web.Response:
        data = await body(req)
        u, s = req["user"], req["session"]
        who = u["name"] if u["id"] != "local" else "Newman"
        ent = h.hub.get(req.match_info["eid"])
        cmd = KINDS[ent.kind].command(str(data.get("command", ""))) if ent else None
        if cmd and cmd.sensitive and s["kind"] == "tablet" and auth_settings()["tabletSensitive"] == "pin":
            o = origin(req)
            if not u.get("pin_hash"):
                return web.json_response({"error": "Set a PIN in your Newman profile first", "needsPin": True}, status=403)
            wait = throttle.blocked(o.ip, "pin:" + u["username"], True)
            if wait > 0:
                return web.json_response({"error": f"Too many wrong PINs. Try again in {max(1, round(wait / 60))} min."},
                                         status=429)
            if not A.check_secret(str(data.get("pin", "")), u["pin_hash"]):
                throttle.fail(o.ip, "pin:" + u["username"], True)
                return web.json_response({"error": "Wrong PIN", "needsPin": True}, status=403)
        try:
            await h.engine.command(req.match_info["eid"], str(data.get("command", "")), who, data.get("value"))
        except ActionError as e:
            return _err(409, str(e))
        return web.json_response({"ok": True})

    # -- people & phones ---------------------------------------------------------------------

    @r.get("/api/people")
    async def people(req: web.Request) -> web.Response:
        """The household (never guests). A guest sees nobody; where each person is comes only for
        those the requester may see (a wall tablet: by its "Can see where people are" and the lists)."""
        if req["user"]["role"] == "guest":
            return web.json_response([])
        return web.json_response([person_json(p, req["user"], req["session"]) for p in h.store.people()])

    @r.post("/api/people")
    async def add_person(req: web.Request) -> web.Response:
        name = str((await body(req)).get("name", "")).strip()
        if not name:
            return _err(400, "name required")
        return web.json_response(person_json(h.store.add_person(name[:48]), req["user"], req["session"]))

    @r.patch("/api/people/{pid}")
    async def rename_person(req: web.Request) -> web.Response:
        pid, name = req.match_info["pid"], str((await body(req)).get("name", "")).strip()
        if not h.store.person(pid) or not name:
            return _err(400, "unknown person or empty name")
        h.store.rename_person(pid, name[:48])
        return web.json_response(person_json(h.store.person(pid), req["user"], req["session"]))

    @r.post("/api/people/{pid}/follow")
    async def follow_person(req: web.Request) -> web.Response:
        """Gone: live following drained batteries. Apps ask for one fresh fix (.../refresh) instead."""
        return _err(410, "Live following was removed - update the app (it refreshes on tap now)")

    @r.post("/api/people/{pid}/refresh")
    async def refresh_person(req: web.Request) -> web.Response:
        """Ask this person's phones for one fresh location now - at most once per 2 minutes per
        person, whoever asks (429 with nextAllowedAt otherwise)."""
        pid = req.match_info["pid"]
        if not h.store.person(pid) or not loc.can_see(req["user"], req["session"], pid):
            return _err(404 if not h.store.person(pid) else 403, "You can't see where they are")
        sent, next_at = await loc.refresh(pid)
        if not sent:
            return web.json_response({"ok": False, "error": "Asked a moment ago - try again in a minute or two",
                                      "nextAllowedAt": next_at}, status=429)
        return web.json_response({"requested": True, "nextAllowedAt": next_at}, status=202)

    # -- how people look: colour and photo ---------------------------------------------------------

    def profile_target(req: web.Request) -> str:
        """The person a profile request is about: the signed-in one (/api/me/…) or, for an admin, any."""
        if "pid" in req.match_info:
            require_admin(req)
            pid = req.match_info["pid"]
        else:
            pid = req["user"].get("person_id") or ""
        if not pid or not h.store.person(pid):
            raise web.HTTPNotFound(text='{"error": "No such person"}', content_type="application/json")
        return pid

    async def profile_changed(pid: str) -> None:
        """Everyone who sees their name hears about the new look (not guests)."""
        p = h.store.person(pid)
        if p:
            await push_to(p, {ws for ws in list(sockets) if socket_viewer(ws)[0].get("role") != "guest"})

    @r.put("/api/me/profile")
    @r.put("/api/people/{pid}/profile")
    async def set_profile(req: web.Request) -> web.Response:
        pid, data = profile_target(req), await body(req)
        if "color" in data:
            try:
                h.store.set_person(pid, color=PF.clean_color(data["color"]))
            except ValueError as e:
                return _err(400, str(e))
        await profile_changed(pid)
        return web.json_response(person_json(h.store.person(pid), req["user"], req["session"]))

    @r.put("/api/me/photo")
    @r.put("/api/people/{pid}/photo")
    async def set_photo(req: web.Request) -> web.Response:
        pid = profile_target(req)
        ctype = (req.content_type or "").lower()
        if ctype not in PF.PHOTO_TYPES:
            return _err(415, "Send the picture itself as image/jpeg, image/png or image/webp")
        raw = bytearray()  # read by hand: the app's usual body limit is far smaller
        while len(raw) <= PF.PHOTO_MAX_BYTES:
            chunk = await req.content.read(65536)
            if not chunk:
                break
            raw += chunk
        if len(raw) > PF.PHOTO_MAX_BYTES:
            return _err(413, "That picture is over 3 MB")
        try:
            jpeg = await asyncio.to_thread(PF.make_photo, bytes(raw))
        except ValueError as e:
            return _err(400, str(e))
        h.store.set_person(pid, photo=PF.save_photo(config.data_dir, pid, jpeg))
        await profile_changed(pid)
        return web.json_response({"photo": PF.photo_url(h.store.person(pid) or {})})

    @r.delete("/api/me/photo")
    @r.delete("/api/people/{pid}/photo")
    async def remove_photo(req: web.Request) -> web.Response:
        pid = profile_target(req)
        PF.remove_photo(config.data_dir, pid)
        h.store.set_person(pid, photo=None)
        await profile_changed(pid)
        return web.json_response({"photo": None})

    @r.get("/api/people/{pid}/photo")
    async def person_photo(req: web.Request) -> web.StreamResponse:
        """Their photo (anyone who sees their name - not guests). Cached long: the URL changes with it."""
        p = h.store.person(req.match_info["pid"])
        path = PF.photo_path(config.data_dir, p["id"]) if p else None
        if req["user"]["role"] == "guest" or not p or not p.get("photo") or not path or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Content-Type": "image/jpeg",
                                               "Cache-Control": "private, max-age=31536000, immutable"})
    # -- my own location sharing (the phone app) -------------------------------------------------

    @r.get("/api/me/location")
    async def me_location(req: web.Request) -> web.Response:
        return web.json_response(loc.me_json(req["user"]))

    @r.put("/api/me/location")
    async def me_location_set(req: web.Request) -> web.Response:
        u, data = req["user"], await body(req)
        if not isinstance(data.get("enabled"), bool):
            return _err(400, 'Send {"enabled": true} or {"enabled": false}')
        mode = loc.mode(u)
        if mode == "off":
            return _err(403, "Location sharing is off for your account")
        if mode == "required":
            if not data["enabled"]:
                return _err(403, f"Required by {u.get('loc_required_by') or 'an admin'}")
            return web.json_response(loc.me_json(u))
        before = viewers_of(u["person_id"])
        h.store.update_user(u["id"], loc_enabled=int(data["enabled"]))
        u = h.store.user(u["id"])
        if not data["enabled"]:
            loc.forget(u["person_id"])
        await push_person(u["person_id"], before)
        return web.json_response(loc.me_json(u))

    @r.post("/api/me/location/report")
    async def me_location_report(req: web.Request) -> web.Response:
        u = req["user"]
        if not loc.sharing(u):
            return _err(403, "Location sharing isn't on for your account")
        try:
            data = await req.json()
        except ValueError:
            return _err(400, "Send the location as JSON")
        if not isinstance(data, dict):
            return _err(400, "Send the location as a JSON object")
        try:
            changed = loc.report(u, data)
        except ValueError as e:
            return _err(400, str(e))
        if changed:
            await push_person(u["person_id"])
        return web.json_response({"live": False, "everySeconds": 300})  # no live following any more

    @r.post("/api/me/location/push-token")
    async def me_location_push_token(req: web.Request) -> web.Response:
        """This iPhone's location push token (its LocationPushExtension); a null token forgets it."""
        try:
            kept = loc.set_push_token(req["user"], await body(req))
        except PermissionError as e:
            return _err(403, str(e))
        except LookupError as e:
            return _err(404, str(e))
        except ValueError as e:
            return _err(400, str(e))
        return web.json_response({"ok": True, "kept": kept})

    # -- places (Home and named ones) -------------------------------------------------------------

    def places_reader(req: web.Request) -> None:
        if req["user"]["role"] == "guest":
            raise web.HTTPForbidden(text='{"error": "Your account can\'t do that"}', content_type="application/json")

    async def places_changed() -> None:
        for p in h.store.people():  # home / place wording may have changed for anyone
            await push_person(p["id"])

    # Map data for the web and wall maps: OpenFreeMap's vector map (styles, tiles, fonts, sprites),
    # fetched by the hub from that one server only and kept, so a browser or wall tablet never contacts
    # anyone else (OpenStreetMap's own servers once blocked the wall's web view) - this works away from
    # home behind the client-certificate proxy too. Style and TileJSON links are rewritten to come back
    # here. Kept gzip-compressed (served that way to browsers that take it, which is all of them).
    map_dir = config.data_dir / "map-tiles" / "ofm"
    map_fetch: dict[str, asyncio.Future] = {}
    map_http: list[ClientSession] = []  # one connection pool for every request (not a new one each)

    def map_file(path: str, ext: str) -> Path:
        name = hashlib.sha256(path.encode()).hexdigest()
        return map_dir / name[:2] / (name + ("" if ext in MAP_RAW else ".gz"))

    # Which map to draw: Apple Maps (with a short-lived MapKit JS token) when a MapKit key is set up,
    # else OpenFreeMap through the hub (below). See mapkit.py.
    mapkit = MapKitTokens()

    @r.get("/api/map/token")
    async def map_token(req: web.Request) -> web.Response:
        return web.json_response(mapkit.provider(), headers={"Cache-Control": "no-store"})

    @r.get("/api/map/ofm/{path:.+}")
    async def map_data(req: web.Request) -> web.StreamResponse:
        path = req.match_info["path"]  # (already %-decoded: "%2e%2e" or "%2F" can't sneak past this)
        parts = path.split("/")
        last = parts[-1]
        ext = "." + last.rsplit(".", 1)[1].lower() if "." in last else ""
        if len(path) > 200 or len(parts) > 8 or ext not in MAP_TYPES \
                or not all(MAP_SEGMENT.match(s) and ".." not in s for s in parts):
            return _err(404, "not found")
        f = map_file(path, ext)
        keep = MAP_STYLE_KEEP_S if ext == "" else MAP_KEEP_S
        if not (f.is_file() and time.time() - f.stat().st_mtime < keep):
            job = map_fetch.get(path)
            if job is None:  # one fetch per file, however many maps ask at once
                job = map_fetch[path] = asyncio.ensure_future(_fetch_map(path, ext, f))
                job.add_done_callback(lambda _, p=path: map_fetch.pop(p, None))
            # Nothing kept yet: wait for it. An old copy is served straight away and refreshed behind
            # (so a style due a refresh never holds the map up - and it beats nothing when offline).
            if not f.is_file():
                status = await asyncio.shield(job)
                if status != 200 and not f.is_file():
                    return _err(404 if status == 404 else 502, "map data unavailable")
        data = await asyncio.to_thread(f.read_bytes)
        headers = {"Content-Type": MAP_TYPES[ext],
                   "Cache-Control": f"private, max-age={3600 if ext == '' else 7 * 86400}"}
        if ext not in MAP_RAW:
            headers["Vary"] = "Accept-Encoding"
            if "gzip" in req.headers.get("Accept-Encoding", ""):
                headers["Content-Encoding"] = "gzip"
            else:
                data = gzip.decompress(data)
        return web.Response(body=data, headers=headers)

    async def _fetch_map(path: str, ext: str, f: Path) -> int:
        url = URL(MAP_UPSTREAM + quote(path, safe="/,@+-._"), encoded=True)
        if url.host != URL(MAP_UPSTREAM).host:  # only ever that one server
            return 404
        try:
            if not map_http or map_http[0].closed:
                map_http[:] = [ClientSession(auto_decompress=False,
                                             headers={"User-Agent": MAP_USER_AGENT, "Accept-Encoding": "gzip"})]
            async with map_http[0].get(url, timeout=ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    log.info("Map data %s: HTTP %s", path, resp.status)
                    return resp.status
                gz = resp.headers.get("Content-Encoding", "").lower() == "gzip"
                data = await resp.read()
            data = await asyncio.to_thread(_map_prepare, data, gz, ext)
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + ".part")
            tmp.write_bytes(data)
            tmp.replace(f)
            return 200
        except Exception as e:  # noqa: BLE001 - missing map data just shows blank
            log.info("Map data %s: %s", path, e)
            return 502

    @r.get("/api/places")
    async def places(req: web.Request) -> web.Response:
        places_reader(req)
        return web.json_response(loc.places())

    @r.post("/api/places")
    async def place_add(req: web.Request) -> web.Response:
        require_admin(req)
        try:
            p = loc.add_place(await body(req))
        except ValueError as e:
            return _err(400, str(e))
        await places_changed()
        return web.json_response(p)

    @r.put("/api/places/{plid}")
    async def place_change(req: web.Request) -> web.Response:
        require_admin(req)
        try:
            p = loc.change_place(req.match_info["plid"], await body(req))
        except KeyError:
            return _err(404, "Unknown place")
        except ValueError as e:
            return _err(400, str(e))
        await places_changed()
        return web.json_response(p)

    @r.delete("/api/places/{plid}")
    async def place_remove(req: web.Request) -> web.Response:
        require_admin(req)
        try:
            loc.remove_place(req.match_info["plid"])
        except KeyError:
            return _err(404, "Unknown place")
        except ValueError as e:
            return _err(400, str(e))
        await places_changed()
        return web.json_response({"ok": True})

    @r.delete("/api/people/{pid}")
    async def remove_person(req: web.Request) -> web.Response:
        pid = req.match_info["pid"]
        h.store.remove_person(pid)
        PF.remove_photo(config.data_dir, pid)
        for a in list(h.engine.autos.values()):
            if pid in a.get("recipients", []):
                a["recipients"] = [x for x in a["recipients"] if x != pid]
                h.store.save_automation(a)
            lv = a.get("live") or {}
            if isinstance(lv.get("recipients"), list) and pid in lv["recipients"]:
                lv["recipients"] = [x for x in lv["recipients"] if x != pid]
                h.store.save_automation(a)
        live.forget_person(pid)
        return web.json_response({"ok": True})

    @r.get("/api/phones")
    async def phones(_: web.Request) -> web.Response:
        return web.json_response([p.to_json() for p in h.store.phones()])

    @r.patch("/api/phones/{did}")
    async def update_phone(req: web.Request) -> web.Response:
        did, data = req.match_info["did"], await body(req)
        if not h.store.phone(did):
            return _err(404, "unknown phone")
        if "personId" in data and data["personId"] and not h.store.person(data["personId"]):
            return _err(400, "unknown person")
        h.store.update_phone(did, person_id=data["personId"] if "personId" in data else False,
                             name=data.get("name"))
        return web.json_response(h.store.phone(did).to_json())

    @r.delete("/api/phones/{did}")
    async def remove_phone(req: web.Request) -> web.Response:
        widget_push.tokens.forget(req.match_info["did"])
        live.tokens.forget(req.match_info["did"])
        return web.json_response({"ok": h.store.remove_phone(req.match_info["did"])})

    @r.post("/api/devices/register")
    async def register(req: web.Request) -> web.Response:
        data = await body(req)
        token, platform, device_id = data.get("token"), data.get("platform"), data.get("deviceId")
        if not (token and device_id and platform in ("ios", "android", "test")):
            return _err(400, "token, deviceId and platform (ios|android|test) are required")
        person_id = data.get("personId") or req["user"].get("person_id") or None
        if person_id and not h.store.person(person_id):
            return _err(400, "unknown person")
        ph = h.store.register_phone(Phone(
            device_id=str(device_id), token=str(token), platform=platform, person_id=person_id,
            apns_env="sandbox" if data.get("apnsEnvironment") == "sandbox" else "production",
            friendly_name=str(data.get("friendlyName", ""))[:64], device_model=str(data.get("deviceModel", ""))[:64]))
        return web.json_response({"ok": True, "personId": ph.person_id})

    @r.get("/api/push/categories")
    async def push_categories(_: web.Request) -> web.Response:
        return web.json_response(h.engine.categories() + charging.categories())

    # -- automations --------------------------------------------------------------------------

    @r.get("/api/automations")
    async def automations(_: web.Request) -> web.Response:
        return web.json_response(h.engine.list())

    @r.get("/api/automations/templates")
    async def automation_templates(_: web.Request) -> web.Response:
        return web.json_response(templates(h.hub, h.store.people()))

    @r.post("/api/automations/describe")
    async def describe(req: web.Request) -> web.Response:
        return web.json_response(h.engine.describe(await body(req)))

    @r.post("/api/automations")
    async def create_automation(req: web.Request) -> web.Response:
        data = await body(req)
        data.pop("id", None)
        try:
            return web.json_response(h.engine.save(data))
        except ValueError as e:
            return _err(400, str(e))

    @r.get("/api/automations/{aid}")
    async def get_automation(req: web.Request) -> web.Response:
        a = next((x for x in h.engine.list() if x["id"] == req.match_info["aid"]), None)
        return web.json_response(a) if a else _err(404, "not found")

    @r.put("/api/automations/{aid}")
    async def put_automation(req: web.Request) -> web.Response:
        aid = req.match_info["aid"]
        if aid not in h.engine.autos:
            return _err(404, "not found")
        data = await body(req)
        data["id"] = aid
        try:
            return web.json_response(h.engine.save(data))
        except ValueError as e:
            return _err(400, str(e))

    @r.patch("/api/automations/{aid}")
    async def patch_automation(req: web.Request) -> web.Response:
        """{"enabled": bool} - switches it on or off (the phone apps' Automations list); answers it as the
        list does (with summary, lastRun, nextRun)."""
        aid = req.match_info["aid"]
        auto = h.engine.autos.get(aid)
        if auto is None:
            return _err(404, "not found")
        data = await body(req)
        if set(data) != {"enabled"} or not isinstance(data["enabled"], bool):
            return _err(400, 'Send {"enabled": true} or {"enabled": false}')
        h.engine.save({**auto, "enabled": data["enabled"]})
        return web.json_response(next(x for x in h.engine.list() if x["id"] == aid))

    @r.delete("/api/automations/{aid}")
    async def delete_automation(req: web.Request) -> web.Response:
        return web.json_response({"ok": h.engine.delete(req.match_info["aid"])})

    @r.post("/api/automations/{aid}/run")
    async def run_automation(req: web.Request) -> web.Response:
        auto = h.engine.autos.get(req.match_info["aid"])
        if not auto:
            return _err(404, "not found")
        force = bool((await body(req)).get("force", False))
        trig: dict[str, Any] = {"type": auto["trigger"]["type"], "manual": True}
        if auto["trigger"]["type"] == "event":
            trig["entity"] = auto["trigger"].get("entity")
            inst = h.integrations.instances.get(h.hub.get(trig["entity"]).integration) if h.hub.get(trig.get("entity", "")) else None
            if inst and hasattr(inst, "snapshot") and not isinstance(inst, VirtualIntegration):
                if sid := await inst.snapshot(trig["entity"]):
                    trig["snapshot_id"] = sid
        return web.json_response(await h.engine.trigger(auto, trig, force=force))

    @r.get("/api/runs")
    async def runs(req: web.Request) -> web.Response:
        out = h.store.runs(req.query.get("automationId"), int(req.query.get("limit", 50)))
        for run in out:
            if live := h.engine.open_runs.get(run["id"]):
                run.update(live)
        return web.json_response(out)

    # -- activity --------------------------------------------------------------------------------

    @r.get("/api/events")
    async def events(req: web.Request) -> web.Response:
        """?limit, before, kind, since; ?runId=<a notification's alertId> - that automation run's entry."""
        before = float(req.query["before"]) if "before" in req.query else None
        since = float(req.query["since"]) if "since" in req.query else None
        return web.json_response(h.store.events(int(req.query.get("limit", 100)), before, req.query.get("kind"),
                                                run_id=req.query.get("runId") or None, since=since))

    # -- integrations -----------------------------------------------------------------------------

    @r.get("/api/integrations/catalog")
    async def catalog(_: web.Request) -> web.Response:
        return web.json_response(h.integrations.catalog())

    @r.get("/api/integrations/discovered")
    async def discovered(_: web.Request) -> web.Response:
        return web.json_response({"listening": h.discovery.running,
                                  "found": h.discovery.found(h.store.integrations())})

    # -- upkeep & today ----------------------------------------------------------------------

    def person_name_of(u: dict[str, Any]) -> str:
        p = h.store.person(u["person_id"]) if u.get("person_id") else None
        return p["name"] if p else u["name"]

    async def upkeep(create: bool = False) -> Any:
        """The Upkeep connection (made the first time someone adds an item)."""
        inst = next((i for i in h.integrations.instances.values() if i.type == "upkeep"), None)
        if not inst and create:
            rec = await h.integrations.create("upkeep", "Upkeep", {})
            inst = h.integrations.instances[rec["id"]]
        if inst:
            inst._people = h.store.people
        return inst

    def runtime_devices() -> list[dict[str, Any]]:
        """Devices whose running hours an item can count: ones that report an activity (a thermostat
        heating or cooling) or have an on/active state."""
        return [{"id": e.id, "name": e.name} for e in h.hub.all()
                if e.kind != "upkeep" and ("action" in (e.attrs or {}) or KINDS[e.kind].active_state)]

    @r.get("/api/upkeep")
    async def upkeep_list(_: web.Request) -> web.Response:
        inst = await upkeep()
        items = [inst.item_json(i) for i in inst.items] if inst else []
        order = {"overdue": 0, "due": 1, "soon": 2, "ok": 3}
        items.sort(key=lambda i: (order[i["state"]], i["daysLeft"] if i["daysLeft"] is not None else 9999, i["name"]))
        return web.json_response({"items": items, "people": [{"id": p["id"], "name": p["name"]} for p in h.store.people()],
                                  "runtimeDevices": runtime_devices()})

    @r.post("/api/upkeep")
    async def upkeep_add(req: web.Request) -> web.Response:
        require_admin(req)
        try:
            return web.json_response(await (await upkeep(create=True)).add(await body(req)))
        except ValueError as e:
            return _err(400, str(e))

    @r.put("/api/upkeep/{iid}")
    async def upkeep_change(req: web.Request) -> web.Response:
        require_admin(req)
        inst = await upkeep()
        try:
            return web.json_response(await inst.change(req.match_info["iid"], await body(req)))
        except (ValueError, CommandError, AttributeError) as e:
            return _err(400, str(e) or "No upkeep items yet")

    @r.delete("/api/upkeep/{iid}")
    async def upkeep_remove(req: web.Request) -> web.Response:
        require_admin(req)
        inst = await upkeep()
        try:
            await inst.remove(req.match_info["iid"])
        except (CommandError, AttributeError):
            return _err(404, "That upkeep item is gone")
        return web.json_response({"ok": True})

    @r.post("/api/upkeep/{iid}/{op:done|undo|snooze}")
    async def upkeep_act(req: web.Request) -> web.Response:
        """Done / Undo / Remind tomorrow - from the wall, a phone or the dashboard. Who did it: on a
        wall tablet the household name tapped ("who"), else the item's owner, else the tablet; anywhere
        else, the signed-in person."""
        inst = await upkeep()
        data, op = await body(req), req.match_info["op"]
        try:
            it = inst._item(req.match_info["iid"])
            if op == "snooze":
                return web.json_response(await inst.snooze(it["id"], int(data.get("days") or 1)))
            if op == "undo":
                return web.json_response(await inst.undo(it["id"]))
            u, s = req["user"], req["session"]
            if s.get("kind") == "tablet":
                names = {p["name"] for p in h.store.people()}
                owner = inst._person_name(it["owner"]) if it["owner"] else ""
                who = data.get("who") if data.get("who") in names else owner or u["name"]
            else:
                who = person_name_of(u)
            return web.json_response(await inst.done(it["id"], who))
        except (CommandError, AttributeError) as e:
            return _err(404, str(e) or "That upkeep item is gone")

    @r.get("/api/today")
    async def today(req: web.Request) -> web.Response:
        """The Today briefing. A phone sees its person's items and everyone's; a wall tablet and the
        dashboard see the whole household (?mine=1 narrows it)."""
        u, s = req["user"], req["session"]
        mine = s.get("kind") == "app" or req.query.get("mine") == "1"
        st = h.store.settings()
        return web.json_response(T.build(h.hub, st, person_id=u.get("person_id") if mine else None,
                                         dismissed=st.get("todayDismissed") or {}))

    @r.post("/api/tablet/battery")
    async def tablet_battery(req: web.Request) -> web.Response:
        """A wall tablet's own battery (the wall app sends it on start, every 5 minutes and on a change;
        with "screen": "on"/"off" and "background", see integrations/tablets.py): the signed-in account's
        "Wall tablet" device. 204, or 400 for a bad report."""
        try:
            data = await req.json()
        except ValueError:
            return _err(400, "Send the battery as JSON")
        try:
            attrs = tablet_report(data)
        except ValueError as e:
            return _err(400, str(e))
        inst = next((i for i in h.integrations.instances.values() if i.type == "tablets"), None)
        if not inst:
            rec = await h.integrations.create("tablets", None, {})
            inst = h.integrations.instances[rec["id"]]
        u = req["user"]
        await inst.report(u["id"], u["name"], attrs)
        return web.Response(status=204)

    @r.post("/api/today/dismiss")
    async def today_dismiss(req: web.Request) -> web.Response:
        """Hide an appliance's need ("rinse aid low") until the appliance reports it again."""
        key = str((await body(req)).get("id") or "")
        if ":need:" not in key:
            return _err(400, "Only an appliance's reminder can be dismissed")
        current = {f"{e.id}:need:{n}" for e in h.hub.all() for n in (e.attrs or {}).get("needs") or []}
        kept = {k: v for k, v in (h.store.settings().get("todayDismissed") or {}).items() if k in current}
        kept[key] = time.time()
        h.store.set_setting("todayDismissed", kept)
        return web.json_response({"ok": True})

    @r.get("/api/today/settings")
    async def today_settings(_: web.Request) -> web.Response:
        st = h.store.settings()
        return web.json_response({k: st.get(k, v) for k, v in T.DEFAULTS.items()})

    @r.patch("/api/today/settings")
    async def today_settings_set(req: web.Request) -> web.Response:
        require_admin(req)
        data = await body(req)
        if "todayRainThreshold" in data:
            try:
                thr = int(data["todayRainThreshold"])
            except (TypeError, ValueError):
                thr = -1
            if not 0 <= thr <= 100:
                return _err(400, "Rain threshold is 0 (off) to 100%")
            h.store.set_setting("todayRainThreshold", thr)
        if "todayEveningFrom" in data:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(data["todayEveningFrom"])):
                return _err(400, "Use a time like 17:00")
            h.store.set_setting("todayEveningFrom", data["todayEveningFrom"])
        st = h.store.settings()
        return web.json_response({k: st.get(k, v) for k, v in T.DEFAULTS.items()})

    # -- backups (admins only) ------------------------------------------------------------------

    saved_backups = B.Saved(config.data_dir)

    def backup_settings() -> dict[str, Any]:
        s = h.store.settings()
        return {"auto": bool(s.get("backupAuto", True)), "keep": int(s.get("backupKeep") or 7),
                "time": str(s.get("backupTime") or "03:30")}

    def make_backup() -> bytes:
        return B.create(h.store._db, config.data_dir, config.app_version)

    @r.get("/api/backups")
    async def backups_list(req: web.Request) -> web.Response:
        require_admin(req)
        return web.json_response({"backups": saved_backups.list(), **backup_settings()})

    @r.patch("/api/backups/settings")
    async def backups_settings(req: web.Request) -> web.Response:
        require_admin(req)
        data = await body(req)
        if "auto" in data:
            h.store.set_setting("backupAuto", bool(data["auto"]))
        if "keep" in data:
            try:
                keep = int(data["keep"])
            except (TypeError, ValueError):
                keep = 0
            if not 1 <= keep <= 90:
                return _err(400, "Keep between 1 and 90 backups")
            h.store.set_setting("backupKeep", keep)
        if "time" in data:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(data["time"])):
                return _err(400, "Use a time like 03:30")
            h.store.set_setting("backupTime", data["time"])
        return web.json_response(backup_settings())

    @r.post("/api/backups")
    async def backups_create(req: web.Request) -> web.Response:
        require_admin(req)
        return web.json_response(saved_backups.save(make_backup(), backup_settings()["keep"]))

    @r.post("/api/backups/download")
    async def backups_download(req: web.Request) -> web.Response:
        """{"name"?: a saved backup (default: a fresh one), "passphrase"?: lock the file with it}."""
        require_admin(req)
        data = await body(req)
        if data.get("name"):
            path = saved_backups.path(str(data["name"]))
            if not path:
                return _err(404, "That backup isn't there any more")
            blob, name = path.read_bytes(), path.name
        else:
            blob, name = make_backup(), f"newman-{time.strftime('%Y-%m-%d-%H%M')}{B.SUFFIX}"
        passphrase = str(data.get("passphrase") or "")
        if passphrase:
            if len(passphrase) < 8:
                return _err(400, "Use a passphrase of at least 8 characters")
            blob = B.lock(blob, passphrase)
        h.store.log_event("system", "Backup downloaded", f"By {req['user']['name']}"
                          + (", locked with a passphrase" if passphrase else ""))
        return web.Response(body=blob, content_type="application/octet-stream",
                            headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @r.delete("/api/backups/{name}")
    async def backups_delete(req: web.Request) -> web.Response:
        require_admin(req)
        path = saved_backups.path(req.match_info["name"])
        if not path:
            return _err(404, "That backup isn't there any more")
        path.unlink()
        return web.json_response({"ok": True})

    @r.post("/api/backups/restore")
    async def backups_restore(req: web.Request) -> web.Response:
        """Restore from an uploaded file (multipart: file, passphrase, check) or a saved backup (JSON:
        name, check). With check=true it only reports what's in the backup; otherwise the hub
        restarts into it (what was there before is kept in data/.pre-restore-*)."""
        require_admin(req)
        raw, passphrase, check = b"", "", False
        if req.content_type.startswith("multipart/"):
            reader = await req.multipart()
            while (part := await reader.next()) is not None:
                if part.name == "file":
                    chunks, size = [], 0
                    while chunk := await part.read_chunk(1 << 20):
                        size += len(chunk)
                        if size > 2 << 30:
                            return _err(413, "That file is too big to be a backup")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                elif part.name == "passphrase":
                    passphrase = (await part.text()).strip()
                elif part.name == "check":
                    check = (await part.text()).strip().lower() in ("1", "true", "yes")
        else:
            data = await body(req)
            path = saved_backups.path(str(data.get("name") or ""))
            if not path:
                return _err(404, "That backup isn't there any more")
            raw, check = path.read_bytes(), bool(data.get("check"))
        if not raw:
            return _err(400, "Choose a backup file")
        try:
            blob = B.unlock(raw, passphrase)
            info = B.inspect(blob) if check else B.stage(blob, config.data_dir)
        except B.BackupError as e:
            return _err(400, str(e))
        summary = {"created": info.get("created"), "version": info.get("version"), "counts": info.get("counts", {})}
        if check:
            return web.json_response({"ok": True, "backup": summary})
        h.store.log_event("system", "Restoring from a backup", f"Started by {req['user']['name']}; restarting")
        restart = getattr(h, "request_restart", None)
        if restart:
            asyncio.get_running_loop().call_later(1.0, restart)  # after this reply is sent
        return web.json_response({"ok": True, "backup": summary, "restarting": bool(restart)})

    @r.get("/api/integrations")
    async def integrations(req: web.Request) -> web.Response:
        out = h.integrations.list()
        if req["user"]["role"] == "admin" and req["session"]["kind"] != "tablet":
            for i in out:
                inst = h.integrations.instances.get(i["id"])
                if inst and (extra := inst.admin_details()):
                    i["adminDetails"] = extra
        return web.json_response(out)

    @r.post("/api/integrations")
    async def add_integration(req: web.Request) -> web.Response:
        data = await body(req)
        try:
            return web.json_response(await h.integrations.create(str(data.get("type", "")), data.get("name"),
                                                                  data.get("config") or {}))
        except ValueError as e:
            return _err(400, str(e))

    @r.get("/api/integrations/{iid}/options")
    async def integration_options(req: web.Request) -> web.Response:
        inst = h.integrations.instances.get(req.match_info["iid"])
        if not inst:
            return _err(404, "not found")
        try:
            return web.json_response(await inst.dynamic_options())
        except Exception as e:
            return _err(502, f"Couldn't read the choices from the device: {e}")

    @r.get("/api/integrations/{iid}/replacements")
    async def integration_replacements(req: web.Request) -> web.Response:
        """Devices this connection can take over from another's ([{from, to, name, kind}]), for
        POST /api/entities/replace."""
        inst = h.integrations.instances.get(req.match_info["iid"])
        if not inst:
            return _err(404, "not found")
        return web.json_response(inst.replacements())

    @r.patch("/api/integrations/{iid}")
    async def update_integration(req: web.Request) -> web.Response:
        data = await body(req)
        try:
            return web.json_response(await h.integrations.update(
                req.match_info["iid"], name=data.get("name"), cfg=data.get("config"), enabled=data.get("enabled")))
        except KeyError:
            return _err(404, "not found")
        except ValueError as e:
            return _err(400, str(e))

    @r.post("/api/integrations/{iid}/reconnect")
    async def reconnect_integration(req: web.Request) -> web.Response:
        """Start the connection again with its saved settings; answers with it (status/detail) once it has
        connected or failed, or after a few seconds if it's still connecting."""
        require_admin(req)
        try:
            return web.json_response(await h.integrations.reconnect(req.match_info["iid"]))
        except KeyError:
            return _err(404, "not found")
        except ValueError as e:
            return _err(400, str(e))

    @r.post("/api/integrations/{iid}/devices")
    async def add_integration_device(req: web.Request) -> web.Response:
        """Admins: {"code"} - add a device to a connection that takes setup codes (catalog "addByCode").
        Answers at once with {"adding": {"state", "message"}}; the progress is in the connection's
        adminDetails.adding. The code is passed on and never kept."""
        require_admin(req)
        inst = h.integrations.instances.get(req.match_info["iid"])
        if not inst:
            return _err(404, "not found")
        try:
            adding = await inst.add_device(str((await body(req)).get("code") or ""))
        except ValueError as e:
            return _err(400, str(e))
        h.store.log_event("system", f"Adding a device to {inst.display_name}", f"Started by {req['user']['name']}")
        return web.json_response({"adding": adding})

    @r.post("/api/integrations/{iid}/adding/cancel")
    async def cancel_integration_add(req: web.Request) -> web.Response:
        """Admins: stop the "add a device" that's running, so another can start. Answers {"adding"}; 409 if
        nothing is running."""
        require_admin(req)
        inst = h.integrations.instances.get(req.match_info["iid"])
        if not inst:
            return _err(404, "not found")
        try:
            adding = await inst.cancel_add()
        except ValueError as e:
            return _err(409, str(e))
        h.store.log_event("system", f"Stopped adding a device to {inst.display_name}", f"By {req['user']['name']}")
        return web.json_response({"adding": adding})

    @r.post("/api/integrations/{iid}/adding/seen")
    async def seen_integration_add(req: web.Request) -> web.Response:
        """Admins: how the last "add a device" ended has been shown (adminDetails.adding.seen becomes true)."""
        require_admin(req)
        inst = h.integrations.instances.get(req.match_info["iid"])
        if not inst:
            return _err(404, "not found")
        inst.add_seen()
        return web.json_response({"ok": True})

    @r.delete("/api/integrations/{iid}/devices/{did}")
    async def remove_integration_device(req: web.Request) -> web.Response:
        """Admins: take one of a connection's paired devices (adminDetails.pairedDevices[].id) off it for good."""
        require_admin(req)
        inst = h.integrations.instances.get(req.match_info["iid"])
        if not inst:
            return _err(404, "not found")
        names = {x.get("id"): x.get("name") for x in (inst.admin_details().get("pairedDevices") or [])}
        try:
            await inst.remove_device(req.match_info["did"])
        except KeyError:
            return _err(404, "That device isn't paired with this connection")
        except (ValueError, CommandError) as e:
            return _err(409, str(e))
        h.store.log_event("system", f"Removed {names.get(req.match_info['did']) or 'a device'} from {inst.display_name}",
                          f"By {req['user']['name']}")
        return web.json_response({"ok": True})

    @r.delete("/api/integrations/{iid}")
    async def remove_integration(req: web.Request) -> web.Response:
        return web.json_response({"ok": await h.integrations.remove(req.match_info["iid"])})

    # -- snapshots --------------------------------------------------------------------------------

    @r.get("/api/snapshots/{sid}")
    async def snapshot(req: web.Request) -> web.StreamResponse:
        return snapshot_response(req.match_info["sid"])

    async def clip_response(req: web.Request, iid: str, ev: str) -> web.StreamResponse:
        inst = h.integrations.instances.get(iid)
        if not isinstance(inst, FrigateIntegration) or not CLIP_ID.match(ev):
            raise web.HTTPNotFound()
        # Frigate builds clip.mp4 on the fly and ignores Range requests, which iOS players require.
        # So a finished clip is fetched once into a local cache and served from there (aiohttp's
        # FileResponse handles Range/seeking); a clip still being recorded is streamed as-is.
        cached = h.clip_dir / f"{iid}_{ev.replace(':', '_')}.mp4"
        if not cached.is_file():
            try:
                finished = await inst.event_finished(ev)
                up = await inst.open_clip(ev, None)
            except Exception as e:
                return _err(502, f"Frigate unreachable: {e}")
            try:
                if up.status_code >= 400:
                    return _err(up.status_code, "clip not available (yet)")
                if not finished:
                    resp = web.StreamResponse(headers={"Content-Type": "video/mp4", "Cache-Control": "no-store"})
                    await resp.prepare(req)
                    async for chunk in up.aiter_bytes(64 * 1024):
                        await resp.write(chunk)
                    await resp.write_eof()
                    return resp
                h.clip_dir.mkdir(parents=True, exist_ok=True)
                tmp = cached.with_suffix(".part")
                with tmp.open("wb") as fh:
                    async for chunk in up.aiter_bytes(256 * 1024):
                        fh.write(chunk)
                tmp.replace(cached)
                prune_clips()
            finally:
                await up.aclose()
        return web.FileResponse(cached, headers={"Content-Type": "video/mp4", "Cache-Control": "private, max-age=86400"})

    def prune_clips(days: int | None = None) -> None:
        """Copies of watched clips older than Settings > Cameras says go."""
        cutoff = time.time() - (days if days is not None else house.clip_days()) * 86400
        for f in h.clip_dir.glob("*.mp4"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)

    async def public_webhook(req: web.Request) -> web.Response:
        """Incoming webhooks: the hard-to-guess secret in the address is the only key (like the
        notification buttons' signed links). A wrong secret gets the same answer as no such hook."""
        ent = h.hub.get(req.match_info["hook"])
        inst = h.integrations.instances.get(ent.integration) if ent else None
        if not inst or not await inst.receive_webhook(ent.id, req.match_info["secret"], await req.read(),
                                                      req.content_type):
            return _err(404, "not found")
        return web.json_response({"ok": True})

    r.post("/api/public/webhooks/{hook}/{secret}")(public_webhook)
    r.get("/api/public/webhooks/{hook}/{secret}")(public_webhook)  # for services that can only call a URL

    @r.get("/api/entities/{eid}/still.jpg")
    async def entity_still(req: web.Request) -> web.StreamResponse:
        """A device's current picture, from whichever integration provides it (?h= height)."""
        eid = req.match_info["eid"]
        pic = video.picture_of(eid) if h.hub.get(eid) else None  # its "Video from" device's, or its own
        ent = h.hub.get(pic) if pic else None
        inst = h.integrations.instances.get(ent.integration) if ent else None
        if not inst or not inst.has_still(pic):
            raise web.HTTPNotFound()
        try:
            jpeg = await inst.still(pic, min(int(req.query.get("h", 480)), 1080))
        except Exception as e:
            return _err(502, f"{inst.display_name} unreachable: {e}")
        if not jpeg:
            return _err(502, "No picture right now")
        return web.Response(body=jpeg, headers={"Content-Type": "image/jpeg", "Cache-Control": "no-store"})

    def _page(req: web.Request) -> tuple[int, float | None]:
        return min(int(req.query.get("limit", 10)), 100), float(req.query["before"]) if "before" in req.query else None

    @r.get("/api/entities/{eid}/live")
    async def entity_live(req: web.Request) -> web.StreamResponse:
        """Relay a camera's real-time stream (go2rtc MSE over websocket) from its integration, so
        clients only ever talk to Newman - through Caddy/mTLS, from anywhere."""
        eid = req.match_info["eid"]
        # Its "Video from" device's when it has one, in the stream picked for this quality (video.py).
        url = video.live_url(eid, req.query.get("quality", "main")) if h.hub.get(eid) else None
        if not url:
            raise web.HTTPNotFound()
        client = web.WebSocketResponse(max_msg_size=0, heartbeat=30)
        await client.prepare(req)
        async with ClientSession() as session:
            try:
                upstream = await session.ws_connect(url, max_msg_size=0, heartbeat=30)
            except Exception as e:
                log.warning("Live stream for %s unavailable: %s", eid, e)
                await client.close(code=1011, message=b"camera unreachable")
                return client

            async def pipe(src: Any, dst: Any) -> None:
                async for msg in src:
                    if msg.type == WSMsgType.TEXT:
                        await dst.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    else:
                        break

            tasks = [asyncio.create_task(pipe(client, upstream)), asyncio.create_task(pipe(upstream, client))]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tasks:
                    t.cancel()
                await upstream.close()
                await client.close()
        return client

    # -- live updates: every change pushed to open dashboards / tablets -----------------------------

    # Each open socket with who signed it in (person updates go only to those allowed to see them).
    sockets: dict[web.WebSocketResponse, tuple[dict[str, Any], dict[str, Any]]] = {}

    async def broadcast(msg: dict[str, Any]) -> None:
        if not sockets:
            return
        data = json.dumps(msg)
        for ws in list(sockets):
            try:
                await ws.send_str(data)
            except Exception:
                sockets.pop(ws, None)

    def socket_viewer(ws: web.WebSocketResponse) -> tuple[dict[str, Any], dict[str, Any]]:
        u, s = sockets[ws]
        return (h.store.user(u["id"]) or u), s  # current role / person, not the one at connect

    def viewers_of(person_id: str | None) -> set[web.WebSocketResponse]:
        if not person_id:
            return set()
        return {ws for ws in list(sockets) if loc.can_see(*socket_viewer(ws), person_id)}

    async def push_person(person_id: str, also: set[web.WebSocketResponse] | None = None) -> None:
        """{"type": "person"} to every socket that may see them - and to `also` (those who could a
        moment ago, so they learn the person stopped sharing; they get no location)."""
        p = h.store.person(person_id)
        if p:
            await push_to(p, viewers_of(person_id) | (also or set()))

    async def push_to(p: dict[str, Any], targets: set[web.WebSocketResponse]) -> None:
        """{"type": "person"} to these sockets, each with what it may see of them."""
        for ws in targets:
            if ws not in sockets:
                continue
            try:
                await ws.send_str(json.dumps({"type": "person", "person": person_json(p, *socket_viewer(ws))}))
            except Exception:
                sockets.pop(ws, None)


    async def push_linked(eid: str) -> None:
        """Devices showing `eid`'s video ("Video from"): their video / picture may have changed too."""
        for lid in video.linked_to(eid):
            if ent := h.hub.get(lid):
                await broadcast({"type": "entity", "entity": entity_json(ent)})

    async def on_entity(old: Entity | None, new: Entity) -> None:
        await broadcast({"type": "entity", "entity": entity_json(new)})
        await push_linked(new.id)

    async def on_device_event(ent: Entity, event: str, data: dict[str, Any]) -> None:
        await broadcast({"type": "event", "entityId": ent.id, "event": event})

    async def on_entity_added(new: Entity) -> None:
        await broadcast({"type": "entity", "entity": entity_json(new)})
        await push_linked(new.id)

    async def on_entities_removed(ids: list[str]) -> None:
        for eid in ids:  # screens drop it straight away (no reload needed)
            await broadcast({"type": "entity-removed", "id": eid})
            await push_linked(eid)

    h.hub.subscribe_changes(muting.on_change)  # first: a "fixed" mute goes before the screens hear of it
    h.hub.subscribe(home.on_state)  # when each device's state last changed ("open for 15 minutes")
    h.hub.subscribe_changes(home_on_change)
    h.hub.subscribe_changes(on_entity)
    h.hub.subscribe_changes(widget_push.changed)
    h.hub.subscribe_registry(on_entity_added, on_entities_removed)
    h.hub.subscribe_events(on_device_event)
    h.store.event_listeners.append(lambda entry: asyncio.get_running_loop().create_task(
        broadcast({"type": "activity", "entry": entry})))

    async def on_wall_alert(alert: dict[str, Any]) -> None:
        # Every client hears it; a wall tablet shows it when `tablets` is empty or names its account.
        await broadcast({"type": "wall", "alert": alert})
    h.engine.wall_listeners.append(on_wall_alert)

    @r.get("/api/ws")
    async def live_updates(req: web.Request) -> web.WebSocketResponse:
        """Push channel: {"type": "hello", "time", "version", "webBuild"} on connect (clients can check
        their clock, and reload when the web build they run is no longer the one served),
        then {"type": "entity", "entity"} on every device change, {"type": "event", …} on device
        events, {"type": "activity", "entry"} for each new activity entry and {"type": "person",
        "person"} when someone this sign-in may see moves (or starts / stops sharing), and {"type": "live",
        "activities", "ended"} with the signed-in person's live statuses - on connect (when there are any) and
        whenever they change."""
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(req)
        await ws.send_str(json.dumps({"type": "hello", "time": time.time(), "version": config.app_version,
                                  "webBuild": web_build}))
        sockets[ws] = (req["user"], req["session"])
        if (pid := req["user"].get("person_id")) and (acts := live.view(pid)):  # (none: nothing to say)
            await ws.send_str(json.dumps({"type": "live", "activities": acts, "ended": []}))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT and msg.data == "ping":
                    await ws.send_str(json.dumps({"type": "pong", "time": time.time()}))
        finally:
            sockets.pop(ws, None)
        return ws

    @r.get("/api/entities/{eid}/alerts")
    async def entity_alerts(req: web.Request) -> web.Response:
        eid = req.match_info["eid"]
        ent = h.hub.get(eid)
        inst = h.integrations.instances.get(ent.integration) if ent else None
        if not inst or not inst.has_alerts(eid):
            return _err(404, "not found")
        try:
            return web.json_response(await inst.alerts(eid, *_page(req)))
        except Exception as e:
            return _err(502, f"{inst.display_name} unreachable: {e}")

    async def gather_alerts(insts: list[Any], limit: int, before: float | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for inst in insts:
            try:
                out += [dict(a, integration=inst.id) for a in await inst.alerts(None, limit, before)]
            except Exception as e:
                log.warning("Couldn't read alerts from %s: %s", inst.display_name, e)
        out.sort(key=lambda x: x.get("start") or 0, reverse=True)
        return out[:limit]

    def alert_brief(a: dict[str, Any]) -> dict[str, Any]:
        """The phone's shape of a camera alert, added to what each connection sends: at, label, zone,
        snapshot, clip (the signed-in API path)."""
        objs = [str(o).replace("_", " ") for o in a.get("objects") or []]
        clip = a.get("clip") if isinstance(a.get("clip"), str) else None
        iid, _, ev = (clip or "").partition(":")
        return {**a, "at": a.get("start"), "label": " & ".join(objs).capitalize() or "Activity",
                "zone": (a.get("zones") or [None])[0], "snapshot": a.get("snapshotUrl"),
                "clip": f"api/clips/{iid}/{ev}" if clip and ev else a.get("clipUrl")}

    def activity_brief(ev: dict[str, Any]) -> dict[str, Any]:
        data = ev.get("data") or {}
        clip = data.get("clip")
        iid, _, rest = str(clip or "").partition(":")
        return {"id": ev["id"], "at": ev["ts"], "title": ev["title"], "body": ev["body"], "entity": ev["entityId"],
                "label": str(ev["title"]).split(" at ")[0] if ev["kind"] == "alert" else ev["title"],
                "zone": (ev["body"] or None) if ev["kind"] == "alert" else None,
                "snapshot": data.get("snapshotUrl") or (f"api/snapshots/{data['snapshotId']}" if data.get("snapshotId") else None),
                "clip": f"api/clips/{iid}/{rest}" if clip and rest else None, "kind": ev["kind"]}

    @r.get("/api/alerts")
    async def alerts(req: web.Request) -> web.Response:
        """Most recent alerts across every connection that has them, newest first.
        ?entity=<camera id>: just that device's (its connection's alerts). ?since=<unix>: from then on.
        ?category=security[,garage…]: the activity entries about devices of those categories instead
        (the phone's Security screen) - each {"id", "at", "title", "body", "entity", "label", "zone",
        "snapshot", "clip", "kind"}. ?limit (default 10, at most 100)."""
        limit, before = _page(req)
        since = float(req.query["since"]) if "since" in req.query else None
        if wanted := [c for c in req.query.get("category", "").split(",") if c]:
            out = []
            for ev in h.store.events(500, before, since=since):
                e = h.hub.get(ev.get("entityId") or "")
                if e and e.category in wanted:
                    out.append(activity_brief(ev))
                    if len(out) >= limit:
                        break
            return web.json_response(out)
        if eid := req.query.get("entity"):
            ent = h.hub.get(eid)
            inst = h.integrations.instances.get(ent.integration) if ent else None
            if not inst or not inst.has_alerts(eid):
                return _err(404, "That device has no alerts")
            try:
                found = [dict(a, integration=inst.id) for a in await inst.alerts(eid, limit, before)]
            except Exception as e:
                return _err(502, f"{inst.display_name} unreachable: {e}")
        else:
            insts = [i for i in h.integrations.instances.values() if i.has_alerts(None)]
            found = await gather_alerts(insts, limit, before)
        out = [alert_brief(a) for a in found]
        if since is not None:
            out = [a for a in out if (a.get("at") or 0) >= since]
        return web.json_response(out)

    @r.get("/api/integrations/{iid}/alerts")
    async def integration_alerts(req: web.Request) -> web.Response:
        inst = h.integrations.instances.get(req.match_info["iid"])
        if not inst or not inst.has_alerts(None):
            return _err(404, "not found")
        return web.json_response(await gather_alerts([inst], *_page(req)))

    @r.get("/api/cameras/{iid}/reviews/{rid}/{which:(thumb\\.webp|snapshot\\.jpg)}")
    async def review_image(req: web.Request) -> web.Response:
        inst = h.integrations.instances.get(req.match_info["iid"])
        rid = req.match_info["rid"]
        if not isinstance(inst, FrigateIntegration) or not CLIP_ID.match(rid):
            raise web.HTTPNotFound()
        try:
            body, ctype = await inst.review_image(rid, req.match_info["which"].split(".")[0])
        except Exception:
            return _err(404, "image not available")
        return web.Response(body=body, headers={"Content-Type": ctype, "Cache-Control": "private, max-age=86400"})

    @r.get("/api/clips/{iid}/{ev}")
    async def clip(req: web.Request) -> web.StreamResponse:
        return await clip_response(req, req.match_info["iid"], req.match_info["ev"])

    @r.get("/api/public/clips/{iid}/{ev}")
    async def public_clip(req: web.Request) -> web.StreamResponse:
        iid, ev, q = req.match_info["iid"], req.match_info["ev"], req.query
        if not tokens.verify("clip", f"{iid}:{ev}", q.get("device", ""), q.get("exp"), q.get("token")):
            return _err(401, "invalid or expired token")
        return await clip_response(req, iid, ev)

    @r.get("/api/public/snapshots/{sid}")
    async def public_snapshot(req: web.Request) -> web.StreamResponse:
        sid, q = req.match_info["sid"], req.query
        if not tokens.verify("snapshot", sid, q.get("device", ""), q.get("exp"), q.get("token")):
            return _err(401, "invalid or expired token")
        return snapshot_response(sid)

    @r.post("/api/public/actions")
    async def public_action(req: web.Request) -> web.Response:
        data = await body(req)
        device_id, subject, button = str(data.get("deviceId", "")), str(data.get("subject", "")), str(data.get("button", ""))
        ph = h.store.phone(device_id) if device_id else None
        what = f"Button {button!r} on {subject or '?'} from {device_id or '?'}" + (f" ({ph.display_name})" if ph else "")
        log.info("%s: received", what)  # on arrival, so a press that never finishes still shows up
        if not tokens.verify("action", subject, device_id, data.get("exp"), data.get("token")):
            log.warning("%s: refused (401 invalid or expired token)", what)
            return _err(401, "invalid or expired token")
        try:
            # Checks run now (401/409 at once); device commands are sent after the reply (background),
            # so the phone never waits on a slow connection - a failure comes back as a push.
            if subject.startswith("charging:"):  # smart charging's "which plug is it on?"
                person = h.store.person(ph.person_id) if ph and ph.person_id else None
                result = await charging.press(subject, button, (person or {}).get("name") or "a phone", background=True)
            else:
                result = await h.engine.press(subject, button, device_id, background=True)
        except ActionError as e:
            log.warning("%s: refused (409 %s)", what, e)
            return _err(409, str(e))
        log.info("%s: %s", what, "accepted" if result.get("accepted") else "done")
        return web.json_response(result)

    press_reports = h.press_reports = getattr(h, "press_reports", None) or PRS.PressReports()

    @r.post("/api/public/press-report")
    async def press_report(req: web.Request) -> web.Response:
        """A phone's account of one notification-button press (press_reports.py): signed with that
        push's action token (accepted up to a week after it expired - the phone queues reports it
        couldn't send) or by a signed-in session. Logged as one WARNING line per try of a failed press."""
        data = await body(req)
        device_id, subject = str(data.get("deviceId", "")), str(data.get("subject", ""))
        signed = tokens.verify("action", subject, device_id, data.get("exp"), str(data.get("token") or ""),
                               grace_s=PRS.GRACE_S)
        if not signed and not find_session(req):
            log.warning("Press report from %s refused (401 invalid or expired token)", device_id or "?")
            return _err(401, "invalid or expired token")
        rid = str(data.get("id", ""))[:64]
        if press_reports.seen(rid):
            return web.json_response({"ok": True, "duplicate": True})
        ph = h.store.phone(device_id) if device_id else None
        rep = PRS.clean(data, phone=ph.display_name if ph else None, remote=req.remote)
        press_reports.add(rep)
        for line in PRS.lines(rep):
            (log.info if rep["ok"] else log.warning)("%s", line)
        return web.json_response({"ok": True})

    @r.get("/api/diagnostics/press")
    async def press_diagnostics(req: web.Request) -> web.Response:
        """The last press reports, newest first (admins)."""
        require_admin(req)
        return web.json_response(press_reports.to_json())

    @r.post("/api/runs/{rid}/press")
    async def run_press(req: web.Request) -> web.Response:
        """A signed-in client presses one of an automation run's buttons (a wall tablet's alert card,
        the phone app's Alerts tab): {"button": "lock_all" | "dismiss" | "snooze:60" | …}. Device
        buttons that need Face ID / a PIN aren't pressed this way - use the device's own control."""
        data = await body(req)
        rid, button, user = req.match_info["rid"], str(data.get("button", "")), req["user"]
        what = f"Button {button!r} on {rid} from {user['name']}"
        log.info("%s: received", what)
        try:
            result = await h.engine.press(rid, button, "", who=user["name"], person_id=user.get("person_id"),
                                          background=True)
        except ActionError as e:
            log.warning("%s: refused (409 %s)", what, e)
            return _err(409, str(e))
        log.info("%s: %s", what, "accepted" if result.get("accepted") else "done")
        return web.json_response(result)

    # -- test tools ----------------------------------------------------------------------------------

    @r.get("/api/test/outbox")
    async def outbox(req: web.Request) -> web.Response:
        did = req.query.get("deviceId")
        return web.json_response([m for m in h.notifier.outbox if not did or m["deviceId"] == did])

    @r.delete("/api/test/outbox")
    async def clear_outbox(_: web.Request) -> web.Response:
        h.notifier.outbox.clear()
        return web.json_response({"ok": True})

    @r.post("/api/test/phones")
    async def test_phone(req: web.Request) -> web.Response:
        data = await body(req)
        pid = data.get("personId")
        if not pid or not h.store.person(pid):
            return _err(400, "personId required")
        did = f"test-{secrets.token_hex(3)}"
        ph = h.store.register_phone(Phone(device_id=did, token=did, platform="test", person_id=pid,
                                          friendly_name=str(data.get("name") or f"Test phone ({h.store.person(pid)['name']})")))
        return web.json_response(ph.to_json())

    @r.post("/api/test/entities/{eid}")
    async def test_entity(req: web.Request) -> web.Response:
        """Virtual devices only: change state "by hand" or fire an event."""
        eid, data = req.match_info["eid"], await body(req)
        ent = h.hub.get(eid)
        inst = h.integrations.instances.get(ent.integration) if ent else None
        if not isinstance(inst, VirtualIntegration):
            return _err(400, "only virtual devices can be simulated")
        if "state" in data:
            await inst.set_state(eid, str(data["state"]))
        if "event" in data:
            await inst.press(eid, str(data["event"]))
        return web.json_response(entity_json(h.hub.get(eid)))

    @r.post("/api/test/push")
    async def test_push(req: web.Request) -> web.Response:
        data = await body(req)
        n = Notification(kind="test", title="Newman test", body=f"Sent at {time.strftime('%-I:%M %p')}")
        sent = await h.notifier.send(n, people=[data["personId"]] if data.get("personId") else None,
                                     device_ids=[data["deviceId"]] if data.get("deviceId") else None)
        return web.json_response({"ok": sent > 0, "phones": sent})

    # -- voice (wall tablets): voice/api.py ------------------------------------------------------------

    async def send_to_user(uid: str, msg: dict[str, Any]) -> None:
        """A message for one account's open sockets (its wall tablet's timers, its voice settings)."""
        data = json.dumps(msg)
        for ws in [w for w in list(sockets) if sockets[w][0]["id"] == uid]:
            try:
                await ws.send_str(data)
            except Exception:
                sockets.pop(ws, None)

    async def send_to_person(pid: str, msg: dict[str, Any]) -> None:
        """A message for the open sockets of whoever is this person (their live statuses)."""
        data = json.dumps(msg)
        for ws in list(sockets):
            u = socket_viewer(ws)[0] if ws in sockets else {}
            if u.get("person_id") == pid:
                try:
                    await ws.send_str(data)
                except Exception:
                    sockets.pop(ws, None)

    live.send_to_person = send_to_person

    # Integrations reach one account's sockets too (the music connection tells a wall tablet what to play).
    h.integrations._ctx.push_to_user = send_to_user

    async def replace_for_integration(pairs: list[dict[str, Any]]) -> dict[str, Any]:
        """A connection moving what used some of its old devices to new ones (Matter merging readings)."""
        counts = await R.replace_entities(h, pairs)
        for uid in counts.pop("dashboardOwners", []):
            await broadcast({"type": "dashboard", "userId": uid})
        return counts
    h.integrations._ctx.replace_entities = replace_for_integration
    # Whether anything still uses a device (a connection deciding if one it no longer has can go).
    h.integrations._ctx.in_use = lambda entity_id: R.uses(h, entity_id)
    add_music_routes(r, h, body=body)
    kitchen = add_recipe_routes(r, h, body=body, send_to_user=send_to_user)  # cook mode (recipes.py)
    W.add_widget_routes(r, h, push=widget_push, body=body)  # phones' widgets and quick tiles
    add_live_routes(r, h, live=live, body=body)  # live statuses (Live Activities / ongoing notifications)

    voice_start, voice_stop = add_voice_routes(r, h, loc=loc, body=body, require_admin=require_admin,
                                               send_to_user=send_to_user, stt=getattr(h, "voice_stt", None),
                                               tts=getattr(h, "voice_tts", None),
                                               fetch=getattr(h, "voice_fetch", None), kitchen=kitchen)

    # (audio for /api/voice/utterance is read as a stream with its own limit)
    app = web.Application(client_max_size=256 * 1024, middlewares=[auth_middleware])
    app.add_routes(r)
    app.on_startup.append(voice_start)
    app.on_cleanup.append(voice_stop)

    async def close_map_http(_: web.Application) -> None:
        for s in map_http:
            await s.close()
    app.on_cleanup.append(close_map_http)

    async def close_widget_push(_: web.Application) -> None:
        await widget_push.close()
    app.on_cleanup.append(close_widget_push)
    app.on_startup.append(live.start)

    async def groups_start(_app: web.Application) -> None:
        await groups.start()
    app.on_startup.append(groups_start)
    app.on_cleanup.append(live.close)
    app[LOCATOR] = loc

    # -- dashboard (built web/ assets) ------------------------------------------------------------
    web_dir = Path(config.web_dir)
    if (web_dir / "index.html").is_file():
        index = web_dir / "index.html"

        async def spa(req: web.Request) -> web.StreamResponse:
            path = req.match_info.get("path", "")
            if path.strip("/") == "wall":
                raise web.HTTPFound("/#/wall")  # the wall tablet; the app's own links are relative to /
            root = web_dir.resolve()
            parts = [p for p in path.split("/") if p]
            # A file, also when asked for from under a page path ("wall/assets/x.js" -> "assets/x.js").
            for i in range(len(parts)):
                candidate = (web_dir / "/".join(parts[i:])).resolve()
                if candidate.is_file() and root in candidate.parents:
                    cache = "public, max-age=31536000, immutable" if "assets" in parts[i:] else "no-cache"
                    return web.FileResponse(candidate, headers={"Cache-Control": cache})
            return web.FileResponse(index, headers={"Cache-Control": "no-cache"})

        app.router.add_get("/", spa)
        app.router.add_get("/{path:(?!api/).*}", spa)
    else:
        log.warning("No dashboard build at %s - serving the API only", web_dir)
    return app
