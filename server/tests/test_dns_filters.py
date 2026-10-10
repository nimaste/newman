"""DNS filters (AdGuard Home, Pi-hole v6 / v5, Technitium), offline against fake servers: readings, pause with a
"back on at" time, events, session renewal, plain errors."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations import REGISTRY  # noqa: E402
from newman.integrations.adguard import AdGuardIntegration  # noqa: E402
from newman.integrations.base import CommandError, Context, mask_config  # noqa: E402
from newman.integrations.pihole import PiholeIntegration  # noqa: E402
from newman.integrations.technitium import TechnitiumIntegration  # noqa: E402
from newman.kinds import KINDS, properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402


def body(req):
    return json.loads(req.content) if req.content else None


class FakeAdGuard:
    def __init__(self):
        self.enabled, self.until_ms, self.posts, self.auth_ok = True, 0, [], True

    def __call__(self, req):
        if req.headers.get("authorization") != "Basic " + __import__("base64").b64encode(b"newman:pw").decode() \
                or not self.auth_ok:
            return httpx.Response(401, text="Unauthorized")
        p = req.url.path
        if p == "/control/status":
            return httpx.Response(200, json={"protection_enabled": self.enabled, "version": "v0.107.52",
                                             "protection_disabled_duration": self.until_ms, "running": True})
        if p == "/control/stats":
            return httpx.Response(200, json={"num_dns_queries": 2000, "num_blocked_filtering": 450,
                                             "num_replaced_safebrowsing": 40, "num_replaced_parental": 10,
                                             "top_clients": [{"10.1.1.5": 900}, {"10.1.1.6": 300}, {"10.1.1.7": 2}]})
        if p == "/control/filtering/status":
            return httpx.Response(200, json={"filters": [{"rules_count": 50000, "enabled": True},
                                                         {"rules_count": 999, "enabled": False}],
                                             "user_rules": ["||ads.example^", "! a comment", ""]})
        if p == "/control/version.json":
            return httpx.Response(200, json={"disabled": False, "new_version": "v0.107.53"})
        if p == "/control/protection" and req.method == "POST":
            b = body(req)
            self.posts.append(b)
            self.enabled = b["enabled"]
            self.until_ms = b.get("duration", 0)
            return httpx.Response(200, text="OK", headers={"content-type": "text/plain"})
        return httpx.Response(404)


class FakePihole:
    def __init__(self):
        self.blocking, self.timer, self.posts = "enabled", None, []
        self.sids, self.logins, self.deleted = set(), 0, []

    def __call__(self, req):
        p = req.url.path
        if p == "/api/auth" and req.method == "POST":
            if body(req)["password"] != "app-pw":
                return httpx.Response(401, json={"session": {"valid": False}})
            self.logins += 1
            sid = f"sid{self.logins}"
            self.sids.add(sid)
            return httpx.Response(200, json={"session": {"valid": True, "sid": sid, "validity": 1800}})
        if p == "/api/auth" and req.method == "DELETE":
            self.deleted.append(req.headers.get("x-ftl-sid"))
            return httpx.Response(204)
        if req.headers.get("x-ftl-sid") not in self.sids:
            return httpx.Response(401, json={"error": {"key": "unauthorized"}})
        if p == "/api/stats/summary":
            return httpx.Response(200, json={"queries": {"total": 1000, "blocked": 123, "percent_blocked": 12.3456},
                                             "clients": {"active": 7, "total": 9},
                                             "gravity": {"domains_being_blocked": 150000}})
        if p == "/api/dns/blocking" and req.method == "GET":
            return httpx.Response(200, json={"blocking": self.blocking, "timer": self.timer})
        if p == "/api/dns/blocking" and req.method == "POST":
            b = body(req)
            self.posts.append(b)
            self.blocking, self.timer = ("enabled" if b["blocking"] else "disabled"), b.get("timer")
            return httpx.Response(200, json={"blocking": self.blocking, "timer": self.timer})
        if p == "/api/info/version":
            return httpx.Response(200, json={"version": {
                "core": {"local": {"version": "v6.0.4"}, "remote": {"version": "v6.0.4"}},
                "web": {"local": {"version": "v6.0.1"}, "remote": {"version": "v6.0.2"}},
                "ftl": {"local": {"version": "v6.0.3"}, "remote": {"version": "v6.0.3"}}}})
        return httpx.Response(404)


class FakePiholeV5:
    def __init__(self):
        self.status, self.calls = "enabled", []

    def __call__(self, req):
        if req.url.path == "/api/auth":
            return httpx.Response(404, text="Not found")
        q = dict(req.url.params)
        if q.get("auth") != "tok":
            return httpx.Response(200, json=[])
        self.calls.append({k: v for k, v in q.items() if k != "auth"})
        if "summaryRaw" in q:
            return httpx.Response(200, json={"dns_queries_today": 500, "ads_blocked_today": 50,
                                             "ads_percentage_today": 10.0, "unique_clients": 4,
                                             "domains_being_blocked": 90000, "status": self.status})
        if "versions" in q:
            return httpx.Response(200, json={"core_current": "v5.18", "core_update": False, "web_update": False,
                                             "FTL_update": True})
        if "disable" in q:
            self.status = "disabled"
            return httpx.Response(200, json={"status": "disabled"})
        if "enable" in q:
            self.status = "enabled"
            return httpx.Response(200, json={"status": "enabled"})
        return httpx.Response(200, json={})


class FakeTechnitium:
    def __init__(self):
        self.blocking, self.till, self.calls = True, None, []

    def __call__(self, req):
        q = dict(req.url.params)
        if q.get("token") != "tk":
            return httpx.Response(200, json={"status": "invalid-token", "errorMessage": "Invalid token"})
        p = req.url.path
        self.calls.append((p, {k: v for k, v in q.items() if k != "token"}))
        ok = lambda r: httpx.Response(200, json={"status": "ok", "response": r})  # noqa: E731
        if p == "/api/settings/get":
            return ok({"version": "13.6", "enableBlocking": self.blocking, "temporaryDisableBlockingTill": self.till})
        if p == "/api/dashboard/stats/get":
            return ok({"stats": {"totalQueries": 4000, "totalBlocked": 1000, "totalClients": 12,
                                 "blockedZones": 3, "blockListZones": 70000}})
        if p == "/api/user/checkForUpdate":
            return ok({"updateAvailable": False, "currentVersion": "13.6"})
        if p == "/api/settings/set":
            self.blocking = q["enableBlocking"] == "true"
            self.till = None
            return ok({})
        if p == "/api/settings/temporaryDisableBlocking":
            self.blocking = False
            self.till = datetime.fromtimestamp(time.time() + int(q["minutes"]) * 60, timezone.utc) \
                .isoformat().replace("+00:00", "Z")
            return ok({"temporaryDisableBlockingTill": self.till})
        return httpx.Response(404)


class DnsFilterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((ent.id, event, data.get("detail")))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def make(self, cls, fake, config, iid):
        d = cls(iid, iid.capitalize(), config, Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        d._http = httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="http://dns.test")
        self.hub.upsert(d._eid, kind="dns_filter", name=d.display_name, integration=d.id)
        return d

    def test_kind_and_registry(self):
        k = KINDS["dns_filter"]
        self.assertEqual({c.id for c in k.commands}, {"turn_on", "turn_off", "pause"})
        pause = k.command("pause")
        self.assertEqual((pause.value_type, pause.unit, pause.takes_value), ("number", "min", True))
        self.assertEqual({e.id for e in k.events}, {"protection_off", "protection_on"})
        for t in ("adguard", "pihole", "technitium"):
            self.assertEqual(REGISTRY[t].provides, ("dns_filter",))
        masked = mask_config(AdGuardIntegration, {"address": "x", "username": "newman", "password": "pw"})
        self.assertEqual(masked["password"], "••••")
        self.assertEqual(mask_config(PiholeIntegration, {"password": "app-pw"})["password"], "••••")
        self.assertEqual(mask_config(TechnitiumIntegration, {"token": "tk"})["token"], "••••")

    async def test_adguard_readings_pause_and_back_on(self):
        fake = FakeAdGuard()
        d = self.make(AdGuardIntegration, fake, {"address": "dns.test:3000", "username": "newman", "password": "pw"}, "adguard")
        self.assertEqual(d.address, "http://dns.test:3000")
        self.assertTrue(await d.refresh())
        e = self.hub.get("adguard.filter")
        self.assertEqual(e.state, "on")
        self.assertEqual({k: e.attrs[k] for k in ("queriesToday", "blockedToday", "blockedPercent", "clients", "rules",
                                                  "version", "updateAvailable")},
                         {"queriesToday": 2000, "blockedToday": 500, "blockedPercent": 25.0, "clients": 3,
                          "rules": 50001, "version": "0.107.52", "updateAvailable": True})
        self.assertEqual(d.detail, "Protecting · 2,000 queries today, 25% blocked")
        props = {p["id"]: p for p in properties_for(e)}
        self.assertEqual((props["blockedPercent"]["unit"], props["updateAvailable"]["type"]), ("%", "boolean"))
        self.assertNotIn("resumesAt", props)

        await d.command("adguard.filter", "pause", 10)
        self.assertEqual(fake.posts[-1], {"enabled": False, "duration": 600000})
        e = self.hub.get("adguard.filter")
        self.assertEqual(e.state, "paused")
        self.assertAlmostEqual(e.attrs["resumesAt"], time.time() + 600, delta=3)
        self.assertTrue(d.detail.startswith("Paused until "))
        await d.command("adguard.filter", "turn_off")
        self.assertEqual(fake.posts[-1], {"enabled": False})
        self.assertEqual((self.hub.get("adguard.filter").state, self.hub.get("adguard.filter").attrs["resumesAt"]),
                         ("off", None))
        await d.command("adguard.filter", "turn_on")
        self.assertEqual(fake.posts[-1], {"enabled": True})
        await asyncio.sleep(0)
        self.assertEqual([(ev, det.startswith("back on at") if det else None) for _, ev, det in self.events],
                         [("protection_off", True), ("protection_on", None)])
        with self.assertRaises(CommandError):
            await d.command("adguard.filter", "pause", 0)
        with self.assertRaises(CommandError):
            await d.command("adguard.filter", "explode")

    async def test_adguard_wrong_password_and_unreachable(self):
        fake = FakeAdGuard()
        d = self.make(AdGuardIntegration, fake, {"address": "http://dns.test", "username": "newman", "password": "no"}, "adguard")
        self.assertFalse(await d.refresh())
        self.assertEqual((d.status, d.detail), ("error", "AdGuard Home didn't accept the user and password"))
        self.assertFalse(self.hub.get("adguard.filter").available)

        def down(req):
            raise httpx.ConnectError("refused")
        d._http = httpx.AsyncClient(transport=httpx.MockTransport(down), base_url="http://dns.test")
        self.assertFalse(await d.refresh())
        self.assertEqual(d.detail, "Can't reach AdGuard Home at http://dns.test")
        with self.assertRaises(CommandError) as cm:
            await d.command("adguard.filter", "turn_off")
        self.assertIn("Can't reach", str(cm.exception))

    async def test_pihole_v6_readings_pause_and_session_renewal(self):
        fake = FakePihole()
        d = self.make(PiholeIntegration, fake, {"address": "http://dns.test", "password": "app-pw"}, "pihole")
        self.assertTrue(await d.refresh())
        e = self.hub.get("pihole.filter")
        self.assertEqual(e.state, "on")
        self.assertEqual({k: e.attrs[k] for k in ("queriesToday", "blockedToday", "blockedPercent", "clients", "rules",
                                                  "version", "updateAvailable")},
                         {"queriesToday": 1000, "blockedToday": 123, "blockedPercent": 12.3, "clients": 7,
                          "rules": 150000, "version": "6.0.4", "updateAvailable": True})
        self.assertEqual(fake.logins, 1)
        fake.sids.clear()  # the session expired on Pi-hole's side
        await d.command("pihole.filter", "pause", 5)
        self.assertEqual(fake.logins, 2)
        self.assertEqual(fake.posts[-1], {"blocking": False, "timer": 300})
        fake.timer = 297.5
        await d.refresh()
        e = self.hub.get("pihole.filter")
        self.assertEqual(e.state, "paused")
        self.assertAlmostEqual(e.attrs["resumesAt"], time.time() + 297, delta=3)
        await d.command("pihole.filter", "turn_on")
        self.assertEqual(fake.posts[-1], {"blocking": True, "timer": None})
        self.assertEqual(self.hub.get("pihole.filter").state, "on")
        await d.close()
        self.assertEqual(fake.deleted, ["sid2"])

    async def test_pihole_wrong_app_password(self):
        d = self.make(PiholeIntegration, FakePihole(), {"address": "http://dns.test", "password": "nope"}, "pihole")
        self.assertFalse(await d.refresh())
        self.assertEqual(d.detail, "Pi-hole didn't accept the app password")
        self.assertFalse(self.hub.get("pihole.filter").available)

    async def test_pihole_v5_fallback(self):
        fake = FakePiholeV5()
        d = self.make(PiholeIntegration, fake, {"address": "http://dns.test", "password": "tok"}, "pihole")
        self.assertTrue(await d.refresh())
        e = self.hub.get("pihole.filter")
        self.assertEqual((e.state, e.attrs["queriesToday"], e.attrs["rules"], e.attrs["updateAvailable"]),
                         ("on", 500, 90000, True))
        await d.command("pihole.filter", "pause", 2)
        self.assertIn({"disable": "120"}, fake.calls)
        e = self.hub.get("pihole.filter")
        self.assertEqual(e.state, "paused")
        self.assertAlmostEqual(e.attrs["resumesAt"], time.time() + 120, delta=3)
        await d.command("pihole.filter", "turn_on")
        self.assertEqual(self.hub.get("pihole.filter").state, "on")

    async def test_technitium(self):
        fake = FakeTechnitium()
        d = self.make(TechnitiumIntegration, fake, {"address": "http://dns.test:5380", "token": "tk"}, "technitium")
        self.assertTrue(await d.refresh())
        e = self.hub.get("technitium.filter")
        self.assertEqual({k: e.attrs[k] for k in ("queriesToday", "blockedToday", "blockedPercent", "clients", "rules",
                                                  "version", "updateAvailable")},
                         {"queriesToday": 4000, "blockedToday": 1000, "blockedPercent": 25.0, "clients": 12,
                          "rules": 70003, "version": "13.6", "updateAvailable": False})
        self.assertIn(("/api/dashboard/stats/get", {"type": "LastDay"}), fake.calls)
        await d.command("technitium.filter", "pause", 15)
        self.assertIn(("/api/settings/temporaryDisableBlocking", {"minutes": "15"}), fake.calls)
        e = self.hub.get("technitium.filter")
        self.assertEqual(e.state, "paused")
        self.assertAlmostEqual(e.attrs["resumesAt"], time.time() + 900, delta=3)
        await d.command("technitium.filter", "turn_off")
        self.assertEqual(fake.calls[-3], ("/api/settings/set", {"enableBlocking": "false"}))
        self.assertEqual(self.hub.get("technitium.filter").state, "off")
        bad = self.make(TechnitiumIntegration, fake, {"address": "http://dns.test", "token": "wrong"}, "tech2")
        self.assertFalse(await bad.refresh())
        self.assertEqual(bad.detail, "Technitium didn't accept the API token")


if __name__ == "__main__":
    unittest.main()
