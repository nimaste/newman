"""Where the household is, over the real HTTP API: per-account sharing (their choice / required /
off), who may see whom (guests never; wall tablets by their own setting and the lists), reports -> Home / place / distance with
only the newest fix kept, asking for a fresh fix (one locate push, at most every 2 minutes per
person; live following is gone), live socket updates only to those allowed, and admin-only places."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman.api import LOCATOR, build_app  # noqa: E402
from newman.automations.engine import Engine  # noqa: E402
from newman.config import config  # noqa: E402
from newman.integrations.base import Context  # noqa: E402
from newman.integrations.manager import IntegrationManager  # noqa: E402
from newman.locations import distance_m  # noqa: E402
from newman.push.notifier import Notifier  # noqa: E402
from newman.state import Hub  # noqa: E402
from newman.store import Phone, Store  # noqa: E402

HOME = (43.6500, -79.3800)
SCHOOL = (43.6680, -79.3800)  # ~2 km north


class H:
    def __init__(self, data: Path) -> None:
        self.snapshot_dir, self.clip_dir = data / "snapshots", data / "clips"
        self.store = Store(data)
        self.hub = Hub()
        self.notifier = Notifier(self.store)
        self.integrations = IntegrationManager(self.store, Context(self.hub, data, self.snapshot_dir))
        self.engine = Engine(self.hub, self.store, self.notifier, self.integrations)
        self.discovery = None


class FakeApns:
    enabled = True

    def __init__(self):
        self.sent = []

    async def send(self, token, payload, **kw):
        self.sent.append((token, payload, kw))

    async def close(self):
        pass


class LocationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        # Home's default spot is the weather connection's.
        self.h.store.save_integration({"id": "weather", "type": "weather", "name": "Weather",
                                       "config": {"place": "Toronto", "latitude": HOME[0], "longitude": HOME[1]}})
        self.app = build_app(self.h)
        self.loc = self.app[LOCATOR]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        r = await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        self.assertEqual(r.status, 200)
        self.ids = {}
        for name, role in (("Marlina", "family"), ("Dante", "family"), ("Massimo", "family"), ("Visitor", "guest"),
                           ("Hallway", "tablet")):
            u = await (await self.client.post("/api/users", json={"username": name.lower(), "name": name, "role": role,
                                                                   "password": "another pass"})).json()
            self.ids[name] = u
        self.chris = await self.token("chris", "correct horse")
        self.marlina, self.dante = await self.token("marlina"), await self.token("dante")
        self.guest = await self.token("visitor")
        self.tablet = await self.token("hallway", kind="tablet")
        self.pid = {p["name"]: p["id"] for p in self.h.store.people()}

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def token(self, username, password="another pass", kind="app"):
        s = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))  # its cookies stay its own
        r = await s.post(self.client.make_url("/api/auth/login"),
                         json={"username": username, "password": password, "kind": kind})
        body = await r.json()
        await s.close()
        self.assertEqual(r.status, 200, body)
        return {"Authorization": "Bearer " + body["token"]}

    async def call(self, method, path, who, **kw):
        r = await self.client.request(method, path, headers=who, **kw)
        try:
            body = await r.json()
        except Exception:
            body = None
        return r.status, body

    async def report(self, who, lat, lon, at=None, **extra):
        return await self.call("POST", "/api/me/location/report", who,
                               json={"lat": lat, "lon": lon, "accuracyM": 12.0, "at": at or time.time(),
                                     "speedMps": None, "source": "background", **extra})

    async def set_sharing(self, name, **location):
        st, body = await self.call("PATCH", f"/api/users/{self.ids[name]['id']}", self.chris, json={"location": location})
        self.assertEqual(st, 200, body)
        return body["location"]

    async def person(self, who, name):
        st, people = await self.call("GET", "/api/people", who)
        self.assertEqual(st, 200)
        return next((p for p in people if p["name"] == name), None)

    # -- sharing modes ----------------------------------------------------------------------------

    async def test_their_choice_is_off_until_they_turn_it_on(self):
        st, me = await self.call("GET", "/api/me/location", self.dante)
        self.assertEqual((st, me), (200, {"mode": "choice", "enabled": False, "requiredBy": None,
                                          "everySeconds": 300, "live": False}))
        self.assertEqual((await self.report(self.dante, *HOME))[0], 403)
        st, me = await self.call("PUT", "/api/me/location", self.dante, json={"enabled": True})
        self.assertEqual((st, me["enabled"], me["mode"]), (200, True, "choice"))
        st, body = await self.report(self.dante, *HOME)
        self.assertEqual((st, body), (200, {"live": False, "everySeconds": 300}))
        self.assertIsNotNone(self.h.store.location(self.pid["Dante"]))
        # Turning it off keeps nothing.
        st, me = await self.call("PUT", "/api/me/location", self.dante, json={"enabled": False})
        self.assertEqual((st, me["enabled"]), (200, False))
        self.assertIsNone(self.h.store.location(self.pid["Dante"]))
        self.assertEqual((await self.report(self.dante, *HOME))[0], 403)
        self.assertEqual((await self.call("PUT", "/api/me/location", self.dante, json={}))[0], 400)

    async def test_required_cant_be_turned_off_and_off_hides_it(self):
        users = await (await self.client.get("/api/users")).json()
        self.assertEqual(next(u for u in users if u["name"] == "Dante")["location"],
                         {"mode": "choice", "enabled": False, "visibleTo": None, "requiredBy": None})
        self.assertEqual(next(u for u in users if u["name"] == "Visitor")["location"]["mode"], "off")
        self.assertEqual(next(u for u in users if u["name"] == "Hallway")["location"]["mode"], "off")
        got = await self.set_sharing("Dante", mode="required", visibleTo=[self.pid["Chris"]])
        self.assertEqual(got, {"mode": "required", "enabled": True, "visibleTo": [self.pid["Chris"]], "requiredBy": "Chris"})
        st, me = await self.call("GET", "/api/me/location", self.dante)
        self.assertEqual((me["mode"], me["enabled"], me["requiredBy"]), ("required", True, "Chris"))
        st, body = await self.call("PUT", "/api/me/location", self.dante, json={"enabled": False})
        self.assertEqual(st, 403)
        self.assertIn("Chris", body["error"])
        self.assertEqual((await self.call("PUT", "/api/me/location", self.dante, json={"enabled": True}))[0], 200)
        self.assertEqual((await self.report(self.dante, *HOME))[0], 200)
        # Back to their choice: it unlocks (and stays on until they switch it off).
        await self.set_sharing("Dante", mode="choice")
        st, me = await self.call("PUT", "/api/me/location", self.dante, json={"enabled": False})
        self.assertEqual((st, me["enabled"], me["requiredBy"]), (200, False, None))
        # Off: not shared, and they can't turn it on.
        await self.set_sharing("Dante", mode="off")
        st, me = await self.call("GET", "/api/me/location", self.dante)
        self.assertEqual((me["mode"], me["enabled"]), ("off", False))
        self.assertEqual((await self.call("PUT", "/api/me/location", self.dante, json={"enabled": True}))[0], 403)
        self.assertEqual((await self.report(self.dante, *HOME))[0], 403)
        # Only an admin sets it, and only to people in the household.
        st, _ = await self.call("PATCH", f"/api/users/{self.ids['Dante']['id']}", self.marlina,
                                json={"location": {"mode": "choice"}})
        self.assertEqual(st, 403)
        st, _ = await self.call("PATCH", f"/api/users/{self.ids['Dante']['id']}", self.chris,
                                json={"location": {"mode": "required", "visibleTo": ["p_nobody"]}})
        self.assertEqual(st, 400)

    async def test_admin_switching_off_forgets_where_they_were(self):
        await self.call("PUT", "/api/me/location", self.dante, json={"enabled": True})
        await self.report(self.dante, *HOME)
        await self.set_sharing("Dante", mode="off")
        self.assertIsNone(self.h.store.location(self.pid["Dante"]))

    # -- who sees whom ------------------------------------------------------------------------------

    async def test_visibility(self):
        await self.set_sharing("Dante", mode="required", visibleTo=[self.pid["Chris"]])
        await self.report(self.dante, *SCHOOL)
        await self.call("PUT", "/api/me/location", self.marlina, json={"enabled": True})
        await self.report(self.marlina, *HOME)
        # Chris was picked: he sees Dante. Marlina (their choice) is seen by the household.
        d = await self.person(self.chris, "Dante")
        self.assertEqual((d["sharing"], d["lat"], d["lon"]), (True, SCHOOL[0], SCHOOL[1]))
        self.assertTrue((await self.person(self.chris, "Marlina"))["home"])
        self.assertTrue((await self.person(self.dante, "Marlina"))["sharing"])
        # Marlina wasn't picked: Dante is listed, but nothing about where he is.
        d = await self.person(self.marlina, "Dante")
        self.assertEqual((d["sharing"], d["lat"], d["home"], d["place"], d["distanceM"], d["at"]),
                         (False, None, False, None, None, None))
        # Dante sees himself.
        self.assertEqual((await self.person(self.dante, "Dante"))["lat"], SCHOOL[0])
        # Nobody who isn't sharing shows a location (Massimo never turned it on).
        m = await self.person(self.chris, "Massimo")
        self.assertEqual((m["sharing"], m["lat"]), (False, None))
        # A guest isn't one of the people and sees nobody; a wall tablet sees no one's location.
        names = [p["name"] for p in (await self.call("GET", "/api/people", self.chris))[1]]
        self.assertNotIn("Visitor", names)
        self.assertNotIn("Hallway", names)
        self.assertEqual(await self.call("GET", "/api/people", self.guest), (200, []))
        self.assertEqual((await self.call("GET", "/api/places", self.guest))[0], 403)
        self.assertEqual((await self.call("POST", f"/api/people/{self.pid['Marlina']}/refresh", self.guest))[0], 403)
        st, me = await self.call("GET", "/api/me/location", self.guest)
        self.assertEqual((st, me["mode"]), (200, "off"))
        self.assertEqual((await self.report(self.guest, *HOME))[0], 403)
        # A wall tablet (its "Can see where people are" on by default) sees Marlina - their choice
        # means the household - but not Dante, whose list doesn't name it. It never reports.
        self.assertTrue((await self.person(self.tablet, "Marlina"))["home"])
        d = await self.person(self.tablet, "Dante")
        self.assertEqual((d["sharing"], d["lat"]), (False, None))
        self.assertEqual((await self.report(self.tablet, *HOME))[0], 403)
        # Everyone in the household when nobody is picked.
        await self.set_sharing("Dante", mode="required", visibleTo=None)
        self.assertTrue((await self.person(self.marlina, "Dante"))["sharing"])

    async def test_wall_tablets_see_people_by_their_setting_and_the_lists(self):
        hallway = self.ids["Hallway"]["id"]
        kitchen_u = await (await self.client.post("/api/users", json={"username": "kitchen", "name": "Kitchen",
                                                                       "role": "tablet", "password": "another pass"})).json()
        kitchen = await self.token("kitchen", kind="tablet")
        # "Can see where people are" is on for a new tablet; other accounts don't have it.
        users = {u["name"]: u for u in (await self.call("GET", "/api/users", self.chris))[1]}
        self.assertEqual((users["Hallway"]["seesPeople"], users["Kitchen"]["seesPeople"]), (True, True))
        self.assertEqual((users["Dante"]["seesPeople"], users["Visitor"]["seesPeople"], users["Chris"]["seesPeople"]),
                         (None, None, None))
        await self.call("PUT", "/api/me/location", self.marlina, json={"enabled": True})
        await self.report(self.marlina, *HOME)
        # Their choice: every tablet with the setting on sees them.
        for t in (self.tablet, kitchen):
            m = await self.person(t, "Marlina")
            self.assertEqual((m["sharing"], m["home"], m["lat"]), (True, True, HOME[0]))
        # Required, seen by the whole household: tablets too.
        await self.set_sharing("Dante", mode="required", visibleTo=None)
        await self.report(self.dante, *SCHOOL)
        for t in (self.tablet, kitchen):
            self.assertEqual((await self.person(t, "Dante"))["lat"], SCHOOL[0])
        # Required, with a list: only the tablets named in it (alongside people).
        got = await self.set_sharing("Dante", mode="required", visibleTo=[self.pid["Chris"], hallway])
        self.assertEqual(got["visibleTo"], [self.pid["Chris"], hallway])
        self.assertEqual((await self.person(self.tablet, "Dante"))["place"], None)
        self.assertEqual((await self.person(self.tablet, "Dante"))["lat"], SCHOOL[0])
        self.assertFalse((await self.person(kitchen, "Dante"))["sharing"])
        self.assertFalse((await self.person(self.marlina, "Dante"))["sharing"])
        # A guest's or family member's account id isn't a tablet: not accepted in the list.
        for bad in (self.ids["Visitor"]["id"], self.ids["Marlina"]["id"], "u_nobody"):
            st, _ = await self.call("PATCH", f"/api/users/{self.ids['Dante']['id']}", self.chris,
                                    json={"location": {"visibleTo": [bad]}})
            self.assertEqual(st, 400, bad)
        # A tablet can refresh someone it sees (its Family map), nobody else.
        self.assertEqual((await self.call("POST", f"/api/people/{self.pid['Dante']}/refresh", self.tablet))[0], 202)
        self.assertEqual((await self.call("POST", f"/api/people/{self.pid['Dante']}/refresh", kitchen))[0], 403)
        self.assertEqual((await self.call("POST", f"/api/people/{self.pid['Massimo']}/refresh", self.tablet))[0], 403)
        # Switched off for the hallway tablet: nobody, even where it's named - and no following.
        st, u = await self.call("PATCH", f"/api/users/{hallway}", self.chris, json={"seesPeople": False})
        self.assertEqual((st, u["seesPeople"]), (200, False))
        st, people = await self.call("GET", "/api/people", self.tablet)
        self.assertTrue(people and all(not p["sharing"] and p["lat"] is None for p in people))
        self.assertEqual((await self.call("POST", f"/api/people/{self.pid['Marlina']}/refresh", self.tablet))[0], 403)
        self.assertTrue((await self.person(kitchen, "Marlina"))["sharing"])  # the other tablet is unaffected
        st, _ = await self.call("PATCH", f"/api/users/{hallway}", self.chris, json={"seesPeople": True})
        self.assertTrue((await self.person(self.tablet, "Dante"))["sharing"])
        # Only an admin changes it, only on a tablet's account, only true / false.
        self.assertEqual((await self.call("PATCH", f"/api/users/{hallway}", self.marlina, json={"seesPeople": False}))[0], 403)
        self.assertEqual((await self.call("PATCH", f"/api/users/{hallway}", self.tablet, json={"seesPeople": False}))[0], 403)
        self.assertEqual((await self.call("PATCH", f"/api/users/{self.ids['Dante']['id']}", self.chris,
                                          json={"seesPeople": True}))[0], 400)
        self.assertEqual((await self.call("PATCH", f"/api/users/{kitchen_u['id']}", self.chris,
                                          json={"seesPeople": "yes"}))[0], 400)
        # Tablets still never share a location, and guests still see nobody.
        self.assertEqual((await self.report(self.tablet, *HOME))[0], 403)
        self.assertEqual((await self.call("PUT", "/api/me/location", self.tablet, json={"enabled": True}))[0], 403)
        st, me = await self.call("GET", "/api/me/location", kitchen)
        self.assertEqual((me["mode"], me["enabled"]), ("off", False))
        self.assertEqual(await self.call("GET", "/api/people", self.guest), (200, []))
        # A household member signed in on a wall tablet doesn't bring their own view to the wall.
        on_wall = await self.token("marlina", kind="tablet")
        self.assertFalse((await self.person(on_wall, "Marlina"))["sharing"])
        # A disabled tablet sees nobody.
        self.h.store.update_user(kitchen_u["id"], disabled=1)
        self.assertFalse(self.loc.can_see(self.h.store.user(kitchen_u["id"]), {"kind": "tablet"}, self.pid["Marlina"]))

    # -- reports ---------------------------------------------------------------------------------------

    async def test_report_home_place_distance_and_only_the_latest(self):
        await self.call("PUT", "/api/me/location", self.dante, json={"enabled": True})
        st, places = await self.call("GET", "/api/places", self.marlina)
        self.assertEqual(places, [{"id": "home", "name": "Home", "lat": HOME[0], "lon": HOME[1], "radiusM": 150,
                                   "builtIn": True}])
        st, school = await self.call("POST", "/api/places", self.chris,
                                     json={"name": "School", "lat": SCHOOL[0], "lon": SCHOOL[1], "radiusM": 300})
        self.assertEqual(st, 200, school)
        await self.call("POST", "/api/places", self.chris,
                        json={"name": "Gym", "lat": SCHOOL[0] + 0.001, "lon": SCHOOL[1], "radiusM": 60})
        # Just next door: home.
        t0 = time.time() - 100
        self.assertEqual((await self.report(self.dante, HOME[0] + 0.0005, HOME[1], at=t0))[0], 200)
        d = await self.person(self.chris, "Dante")
        self.assertEqual((d["home"], d["place"]), (True, None))
        self.assertLess(d["distanceM"], 150)
        # At school: away, at the named place, ~2 km.
        await self.report(self.dante, *SCHOOL, at=t0 + 10)
        d = await self.person(self.chris, "Dante")
        self.assertEqual((d["home"], d["place"], d["accuracyM"]), (False, "School", 12.0))
        self.assertAlmostEqual(d["distanceM"], distance_m(*HOME, *SCHOOL), delta=1)
        self.assertAlmostEqual(d["distanceM"], 2001, delta=20)
        # Inside the gym too: the smaller place wins.
        await self.report(self.dante, SCHOOL[0] + 0.001, SCHOOL[1], at=t0 + 20)
        self.assertEqual((await self.person(self.chris, "Dante"))["place"], "Gym")
        # Somewhere unnamed.
        await self.report(self.dante, 43.70, -79.40, at=t0 + 30)
        d = await self.person(self.chris, "Dante")
        self.assertEqual((d["home"], d["place"]), (False, None))
        self.assertGreater(d["distanceM"], 5000)
        # A late, older fix doesn't replace the newer one - and there's only ever one row.
        await self.report(self.dante, *HOME, at=t0 + 5)
        d = await self.person(self.chris, "Dante")
        self.assertEqual((d["lat"], d["at"]), (43.70, t0 + 30))
        n = self.h.store._db.execute("SELECT COUNT(*) FROM person_locations").fetchone()[0]
        self.assertEqual(n, 1)
        # Moving Home re-labels them.
        st, _ = await self.call("PUT", "/api/places/home", self.chris, json={"lat": 43.70, "lon": -79.40})
        self.assertEqual(st, 200)
        self.assertTrue((await self.person(self.chris, "Dante"))["home"])
        # Bad reports.
        for bad in ({"lat": "x", "lon": 1}, {"lat": 91, "lon": 0}, {"lon": 2}, {"lat": 1, "lon": 2, "source": "nope"},
                    {"lat": 1, "lon": 2, "accuracyM": -3}, {"lat": True, "lon": 2}):
            st, _ = await self.call("POST", "/api/me/location/report", self.dante, json=bad)
            self.assertEqual(st, 400, bad)
        r = await self.client.post("/api/me/location/report", data=b"not json",
                                   headers={**self.dante, "Content-Type": "application/json"})
        self.assertEqual(r.status, 400)
        # Signed out.
        s = aiohttp.ClientSession()
        r = await s.post(self.client.make_url("/api/me/location/report"), json={"lat": 1, "lon": 2})
        self.assertEqual(r.status, 401)
        await s.close()

    # -- refreshing ----------------------------------------------------------------------------------------

    async def test_refresh_sends_one_locate_push_at_most_every_two_minutes(self):
        apns = FakeApns()
        self.h.notifier._apns = apns  # iOS: a silent background push; Android without FCM: skipped quietly
        did = self.pid["Dante"]
        self.h.store.register_phone(Phone(device_id="test-d", token="test-d", platform="test", person_id=did))
        self.h.store.register_phone(Phone(device_id="ios-d", token="tok-ios", platform="ios", person_id=did,
                                          apns_env="sandbox"))
        self.h.store.register_phone(Phone(device_id="and-d", token="tok-and", platform="android", person_id=did))
        await self.call("PUT", "/api/me/location", self.dante, json={"enabled": True})
        await self.report(self.dante, *HOME)
        before = time.time()
        st, body = await self.call("POST", f"/api/people/{did}/refresh", self.chris)
        self.assertEqual((st, body["requested"]), (202, True))
        self.assertAlmostEqual(body["nextAllowedAt"], before + 120, delta=3)
        self.assertEqual([m["type"] for m in self.h.notifier.outbox if m["deviceId"] == "test-d"], ["locate"])
        self.assertEqual(len(apns.sent), 1)
        token, payload, kw = apns.sent[0]
        self.assertEqual((token, payload), ("tok-ios", {"aps": {"content-available": 1}, "newman": {"type": "locate"}}))
        self.assertEqual((kw["push_type"], kw["priority"], kw["env"]), ("background", 5, "sandbox"))
        self.assertEqual(self.h.store.phone("and-d").failures, 0)  # no FCM here: skipped, not a failure
        # The phone answers with one fix ("refresh"); it never goes live - every 5 minutes as always.
        self.assertEqual((await self.report(self.dante, *HOME, source="refresh"))[1], {"live": False, "everySeconds": 300})
        st, me = await self.call("GET", "/api/me/location", self.dante)
        self.assertEqual((me["live"], me["everySeconds"]), (False, 300))
        self.assertFalse((await self.person(self.chris, "Dante"))["live"])
        # Asking again within 2 minutes (whoever asks): 429 with when it's allowed, and no push.
        st, again = await self.call("POST", f"/api/people/{did}/refresh", self.marlina)
        self.assertEqual((st, again["nextAllowedAt"]), (429, body["nextAllowedAt"]))
        self.assertTrue(again["error"])
        self.assertEqual(len(apns.sent), 1)
        # Two minutes later it goes out again - once.
        real = self.loc.clock
        self.loc.clock = lambda: real() + 121
        self.assertEqual((await self.call("POST", f"/api/people/{did}/refresh", self.chris))[0], 202)
        self.assertEqual(len(apns.sent), 2)
        # Only someone who may see them can ask.
        await self.set_sharing("Dante", mode="required", visibleTo=[self.pid["Chris"]])
        self.assertEqual((await self.call("POST", f"/api/people/{did}/refresh", self.marlina))[0], 403)
        self.assertEqual((await self.call("POST", "/api/people/p_nobody/refresh", self.chris))[0], 404)
        self.assertEqual((await self.call("POST", f"/api/people/{self.pid['Massimo']}/refresh", self.chris))[0], 403)

    async def test_live_following_is_gone(self):
        did = self.pid["Dante"]
        await self.call("PUT", "/api/me/location", self.dante, json={"enabled": True})
        for who in (self.chris, self.tablet, self.marlina):
            st, body = await self.call("POST", f"/api/people/{did}/follow", who)
            self.assertEqual(st, 410)
            self.assertIn("error", body)
        self.assertEqual(len(self.h.notifier.outbox), 0)
        # An older app still reporting as "live" is accepted (and told: every 5 minutes).
        self.assertEqual((await self.report(self.dante, *HOME, source="live"))[1], {"live": False, "everySeconds": 300})
    # -- live socket ---------------------------------------------------------------------------------------

    async def test_person_updates_only_reach_sockets_allowed_to_see_them(self):
        await self.set_sharing("Dante", mode="required", visibleTo=[self.pid["Chris"]])

        async def socket(who):
            ws = await self.client.ws_connect("/api/ws?token=" + who["Authorization"][7:])
            self.assertEqual((await ws.receive_json(timeout=2))["type"], "hello")
            return ws

        chris, marlina, guest, tablet = (await socket(self.chris), await socket(self.marlina),
                                         await socket(self.guest), await socket(self.tablet))
        await self.report(self.dante, *SCHOOL)
        msg = await chris.receive_json(timeout=2)
        self.assertEqual(msg["type"], "person")
        self.assertEqual((msg["person"]["name"], msg["person"]["lat"], msg["person"]["home"]), ("Dante", SCHOOL[0], False))
        for ws in (marlina, guest, tablet):
            with self.assertRaises(asyncio.TimeoutError):
                await ws.receive_json(timeout=0.4)
        # Opened up to the household: Marlina and the wall tablet hear about him now, the guest still doesn't.
        await self.set_sharing("Dante", mode="required", visibleTo=None)
        await chris.receive_json(timeout=2)
        self.assertEqual((await marlina.receive_json(timeout=2))["person"]["sharing"], True)
        self.assertEqual((await tablet.receive_json(timeout=2))["person"]["sharing"], True)
        await self.report(self.dante, *HOME)
        self.assertTrue((await marlina.receive_json(timeout=2))["person"]["home"])
        self.assertTrue((await tablet.receive_json(timeout=2))["person"]["home"])
        with self.assertRaises(asyncio.TimeoutError):
            await guest.receive_json(timeout=0.4)
        # The tablet's "Can see where people are" switched off: it hears he's gone quiet, then nothing.
        st, _ = await self.call("PATCH", f"/api/users/{self.ids['Hallway']['id']}", self.chris, json={"seesPeople": False})
        self.assertEqual(st, 200)
        msgs = [await tablet.receive_json(timeout=2) for _ in self.h.store.people()]
        self.assertEqual({m["person"]["name"]: m["person"]["sharing"] for m in msgs}["Dante"], False)
        self.assertTrue(all(m["person"]["lat"] is None for m in msgs))
        await self.report(self.dante, *SCHOOL)
        await marlina.receive_json(timeout=2)
        for ws in (guest, tablet):
            with self.assertRaises(asyncio.TimeoutError):
                await ws.receive_json(timeout=0.4)
        await self.call("PATCH", f"/api/users/{self.ids['Hallway']['id']}", self.chris, json={"seesPeople": True})
        msgs = [await tablet.receive_json(timeout=2) for _ in self.h.store.people()]
        self.assertEqual({m["person"]["name"]: m["person"]["lat"] for m in msgs}["Dante"], SCHOOL[0])
        # Switched off: those who could see him learn he stopped - with no location.
        await self.set_sharing("Dante", mode="off")
        msg = await marlina.receive_json(timeout=2)
        self.assertEqual((msg["person"]["sharing"], msg["person"]["lat"]), (False, None))
        for ws in (chris, marlina, guest, tablet):
            await ws.close()

    # -- places ----------------------------------------------------------------------------------------------

    async def test_places_are_admin_only_to_change(self):
        new = {"name": "School", "lat": SCHOOL[0], "lon": SCHOOL[1], "radiusM": 250}
        self.assertEqual((await self.call("POST", "/api/places", self.marlina, json=new))[0], 403)
        self.assertEqual((await self.call("POST", "/api/places", self.tablet, json=new))[0], 403)
        st, p = await self.call("POST", "/api/places", self.chris, json=new)
        self.assertEqual((st, p["name"], p["radiusM"], p["builtIn"]), (200, "School", 250, False))
        self.assertEqual((await self.call("PUT", f"/api/places/{p['id']}", self.marlina, json={"radiusM": 400}))[0], 403)
        st, p2 = await self.call("PUT", f"/api/places/{p['id']}", self.chris, json={"radiusM": 400, "name": "High school"})
        self.assertEqual((st, p2["radiusM"], p2["name"], p2["lat"]), (200, 400, "High school", SCHOOL[0]))
        st, listed = await self.call("GET", "/api/places", self.tablet)
        self.assertEqual([x["name"] for x in listed], ["Home", "High school"])
        self.assertEqual((await self.call("DELETE", f"/api/places/{p['id']}", self.marlina))[0], 403)
        self.assertEqual((await self.call("DELETE", f"/api/places/{p['id']}", self.chris))[0], 200)
        self.assertEqual((await self.call("DELETE", f"/api/places/{p['id']}", self.chris))[0], 404)
        # Home can be moved and resized, not removed.
        self.assertEqual((await self.call("DELETE", "/api/places/home", self.chris))[0], 400)
        st, home = await self.call("PUT", "/api/places/home", self.chris, json={"radiusM": 200})
        self.assertEqual((st, home["radiusM"], home["lat"]), (200, 200, HOME[0]))
        for bad in ({"name": "", "lat": 1, "lon": 1, "radiusM": 100}, {"name": "X", "lat": 1, "lon": 1, "radiusM": 5},
                    {"name": "X", "lat": None, "lon": 1, "radiusM": 100}, {"name": "home", "lat": 1, "lon": 1, "radiusM": 100}):
            self.assertEqual((await self.call("POST", "/api/places", self.chris, json=bad))[0], 400, bad)

    async def test_home_without_a_weather_connection(self):
        self.h.store.remove_integration("weather")
        st, places = await self.call("GET", "/api/places", self.chris)
        self.assertEqual((places[0]["lat"], places[0]["lon"]), (None, None))
        await self.call("PUT", "/api/me/location", self.dante, json={"enabled": True})
        await self.report(self.dante, *HOME)
        d = await self.person(self.chris, "Dante")
        self.assertEqual((d["home"], d["distanceM"], d["lat"]), (False, None, HOME[0]))


if __name__ == "__main__":
    unittest.main()
