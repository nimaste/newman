"""Phone layouts: the household default (first laid out from the admin's wall dashboard by the
rules the phone apps used before), people's own layouts, resets, who may change what, validation,
and the live "dashboard" message going to exactly the people whose phone changed."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman import phone_layouts as PL  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from test_auth import H, Browser  # noqa: E402


def tile(t, x, y, w, h, **config):
    return {"id": f"{t}{x}{y}", "type": t, "x": x, "y": y, "w": w, "h": h, "config": config}


# A wall dashboard like the starter: Today, Overview (weather, calendar, camera, alerts, a small
# dial, four shortcuts), Security, Lights (one big devices tile) and Climate (a big dial).
DIAL = {"entity": "fake.stat", "command": "set_temperature", "modeCommand": "set_mode"}
SAMPLE = {"name": "Wall tablet", "pages": [
    {"id": "today", "name": "Today", "icon": "sun", "tiles": [
        tile("greeting", 0, 0, 12, 1), tile("todo", 0, 1, 5, 7), tile("calendar", 5, 1, 4, 7), tile("weather", 9, 1, 3, 7)]},
    {"id": "overview", "name": "Overview", "icon": "home", "tiles": [
        tile("weather", 0, 0, 4, 3, show="days"), tile("calendar", 0, 3, 4, 5, entities=[], days=31),
        tile("camera", 4, 0, 5, 5, entities=["fake.cam"]), tile("alerts", 4, 5, 5, 3, limit=4),
        tile("dial", 9, 0, 3, 4, **DIAL),
        tile("devices", 9, 4, 3, 4, title="Shortcuts", entities=["fake.side", "fake.light"], style="tiles")]},
    {"id": "security", "name": "Security", "icon": "shield", "tiles": [
        tile("devices", 0, 0, 7, 3, title="Locks & doors", entities=["fake.side"], style="list"),
        tile("attention", 4, 4, 3, 4)]},
    {"id": "lights", "name": "Lights", "icon": "bulb", "tiles": [
        tile("devices", 0, 0, 12, 8, title="Lights", entities=["fake.light", "fake.lamp", "fake.porch"], style="tiles",
             groupBy="area", textSize=90)]},
    {"id": "climate", "name": "Climate", "icon": "thermometer", "tiles": [tile("dial", 0, 0, 8, 8, **DIAL)]},
]}


def boxes(page):
    return [(t["type"], t["x"], t["y"], t["w"], t["h"]) for t in page["tiles"]]


class FromDashboardTests(unittest.TestCase):
    def test_todays_rules(self):
        out = PL.from_dashboard(SAMPLE)
        # The Today page is the Today card; the next three pages are tabs; Climate opens from a link.
        self.assertTrue(out["today"])
        self.assertEqual([(p["id"], p["tab"]) for p in out["pages"]],
                         [("overview", True), ("security", True), ("lights", True), ("climate", False)])
        overview, security, lights, climate = out["pages"]
        # Weather (a small one) and the dial pair up as half-width squares; the shortcuts become one
        # card per device, tucked in under them; wide tiles span the 4 columns.
        self.assertEqual(boxes(overview), [
            ("weather", 0, 0, 2, 2), ("dial", 2, 0, 2, 2),
            ("calendar", 0, 2, 4, 4), ("camera", 0, 6, 4, 3), ("alerts", 0, 9, 4, 3),
            ("devices", 0, 12, 2, 1), ("devices", 2, 12, 2, 1)])
        self.assertEqual([t["config"]["entities"] for t in overview["tiles"][-2:]], [["fake.side"], ["fake.light"]])
        self.assertEqual(overview["tiles"][1]["config"], DIAL)  # the dial keeps its settings (it opens Climate)
        self.assertEqual(boxes(security), [("devices", 0, 0, 4, 2), ("attention", 0, 2, 4, 2)])
        # A wide devices tile: full-width cards (with the brightness bar), text size kept.
        self.assertEqual(boxes(lights), [("devices", 0, i, 4, 1) for i in range(3)])
        self.assertEqual(lights["tiles"][0]["textSize"], 90)
        self.assertEqual(boxes(climate), [("dial", 0, 0, 4, 4)])
        # It's a valid layout as it stands.
        cleaned, errors = PL.clean(out)
        self.assertEqual(errors, [])
        self.assertEqual(cleaned, out)

    def test_no_today_page_and_greeting_dropped(self):
        out = PL.from_dashboard({"pages": [{"id": "a", "name": "A", "tiles": [
            tile("greeting", 0, 0, 12, 1), tile("mode", 0, 1, 8, 1, entity="x")]}]})
        self.assertFalse(out["today"])
        self.assertEqual(boxes(out["pages"][0]), [("mode", 0, 0, 4, 2)])

    def test_wall_only_tiles_left_out(self):
        out = PL.from_dashboard({"pages": [{"id": "a", "name": "A", "tiles": [
            tile("music", 0, 0, 4, 4), tile("mode", 4, 0, 8, 1, entity="x"), tile("recipes", 0, 4, 4, 4),
            tile("climate", 4, 4, 5, 8)]}]})
        self.assertEqual(boxes(out["pages"][0]), [("mode", 0, 0, 4, 2)])


def stored_with_wall_only():
    """A phone page saved before Music, Recipes and Climate glance were wall-only."""
    return {"today": False, "pages": [{"id": "a", "name": "A", "icon": "home", "tab": True, "tiles": [
        tile("weather", 0, 0, 2, 2), tile("music", 2, 0, 2, 2), tile("recipes", 0, 2, 4, 3),
        tile("dial", 0, 5, 2, 2), tile("climate", 2, 5, 2, 4), tile("camera", 0, 9, 4, 3)]}]}


class WallOnlyMigrationTests(unittest.TestCase):
    def test_catalogue(self):
        from newman import dashboards as D
        for t in ("music", "recipes", "climate"):
            self.assertTrue(D.TYPES[t].get("wallOnly"))
            self.assertNotIn("phone", D.TYPES[t])
            self.assertNotIn(t, D.PHONE_SIZES)
        self.assertEqual(D.TYPES["camera"]["phone"], [4, 3])

    def test_dropped_on_load_and_rows_close_up(self):
        out = PL.with_text_size(stored_with_wall_only())
        # Weather keeps its place (Music beside it went); the Recipes rows close up; the dial keeps
        # the rows Climate glance shared with it (2 of 4), the rest close up.
        self.assertEqual(boxes(out["pages"][0]), [("weather", 0, 0, 2, 2), ("dial", 0, 2, 2, 2), ("camera", 0, 4, 4, 3)])
        cleaned, errors = PL.clean(out)
        self.assertEqual(errors, [])
        self.assertEqual(cleaned, out)

    def test_dropped_when_saved(self):
        cleaned, errors = PL.clean(stored_with_wall_only())
        self.assertEqual(errors, [])  # an older client's layout still saves, without them
        self.assertEqual(boxes(cleaned["pages"][0]), [("weather", 0, 0, 2, 2), ("dial", 0, 2, 2, 2), ("camera", 0, 4, 4, 3)])

    def test_nothing_dropped_nothing_moves(self):
        lay = {"today": False, "pages": [{"id": "a", "name": "A", "icon": "home", "tab": True,
                                          "tiles": [tile("weather", 0, 3, 2, 2)]}]}
        self.assertEqual(boxes(PL.with_text_size(lay)["pages"][0]), [("weather", 0, 3, 2, 2)])
        self.assertEqual(boxes(PL.clean(lay)[0]["pages"][0]), [("weather", 0, 3, 2, 2)])


class ValidationTests(unittest.TestCase):
    def layout(self, *pages, today=False):
        return {"today": today, "pages": list(pages)}

    def page(self, pid, tab=True, tiles=()):
        return {"id": pid, "name": pid.title(), "icon": "home", "tab": tab, "tiles": list(tiles)}

    def test_good_and_bad(self):
        ok, errors = PL.clean(self.layout(self.page("a", tiles=[tile("camera", 0, 0, 4, 3, entities=["c"]),
                                                               tile("dial", 0, 3, 2, 2, textSize=110)])))
        self.assertEqual(errors, [])
        self.assertEqual(ok["pages"][0]["tiles"][1]["textSize"], 110)
        cases = {
            "overlap": [tile("camera", 0, 0, 4, 3), tile("dial", 2, 2, 2, 2)],
            "off the phone": [tile("camera", 1, 0, 4, 3)],
            "Unknown tile type": [tile("toaster", 0, 0, 2, 2)],
        }
        for want, tiles in cases.items():
            _, errors = PL.clean(self.layout(self.page("a", tiles=tiles)))
            self.assertTrue(any(want in e for e in errors), (want, errors))
        _, errors = PL.clean(self.layout(*[self.page(f"p{i}") for i in range(4)]))
        self.assertTrue(any("At most 3" in e for e in errors), errors)
        _, errors = PL.clean(self.layout(self.page("a", tab=False)))
        self.assertTrue(any("tab" in e for e in errors), errors)
        _, errors = PL.clean(self.layout(*[self.page(f"p{i}", tab=i < 2) for i in range(9)]))
        self.assertTrue(any("8 pages" in e for e in errors), errors)
        _, errors = PL.clean(self.layout(self.page("a", tiles=[tile("camera", 0, 0, 4, 9)])))
        self.assertTrue(errors)  # 8 rows high at most


class PhoneLayoutApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", data)
        self.h = H(data)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        r = await self.client.post("/api/auth/setup", json={"username": "Chris", "name": "Chris", "password": "correct horse"})
        self.chris = (await r.json())["user"]["id"]
        self.ids = {}
        for name, role in (("Marlina", "family"), ("Dante", "family"), ("Visitor", "guest"), ("Hallway", "tablet")):
            u = await (await self.client.post("/api/users", json={"username": name.lower(), "name": name, "role": role,
                                                                   "password": "another pass"})).json()
            self.ids[name] = u["id"]
        # The admin's wall dashboard - what the first household default is laid out from.
        await self.client.post("/api/dashboards", json=SAMPLE)
        self.open = []

    async def asyncTearDown(self):
        for c in reversed(self.open):
            await c.close()
        await self.client.close()
        self.tmp.cleanup()

    async def login(self, username, password="another pass", kind="app"):
        c = Browser(self.client)
        self.open.append(c)
        body = await (await c.post("/api/auth/login", json={"username": username, "password": password, "kind": kind})).json()
        return c, {"Authorization": "Bearer " + body["token"]}

    async def test_default_own_reset_and_permissions(self):
        m, mh = await self.login("marlina")
        r = await (await m.get("/api/phone-layout", headers=mh)).json()
        self.assertEqual(r["source"], "default")
        self.assertEqual([p["name"] for p in r["layout"]["pages"]], ["Overview", "Security", "Lights", "Climate"])
        default = r["layout"]
        # Kept: the default doesn't change under the phones when the wall dashboard does.
        self.assertIsNotNone(self.h.store.phone_layout(""))
        # Family: their own layout (from a copy of the default), then back to the default.
        mine = json.loads(json.dumps(default))
        mine["pages"][0]["tiles"] = mine["pages"][0]["tiles"][:2]
        r = await m.put("/api/phone-layouts/me", json=mine, headers=mh)
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((await r.json())["source"], "own")
        self.assertEqual(len((await (await m.get("/api/phone-layout", headers=mh)).json())["layout"]["pages"][0]["tiles"]), 2)
        # ...but nobody else's, and not the household default.
        self.assertEqual((await m.put("/api/phone-layouts/default", json=mine, headers=mh)).status, 403)
        self.assertEqual((await m.put(f"/api/phone-layouts/{self.ids['Dante']}", json=mine, headers=mh)).status, 403)
        self.assertEqual((await m.get("/api/phone-layouts", headers=mh)).status, 403)
        self.assertEqual((await m.post("/api/phone-layouts/reset-all", headers=mh)).status, 403)
        r = await (await m.post("/api/phone-layouts/me/reset", headers=mh)).json()
        self.assertEqual((r["source"], r["layout"]), ("default", default))
        # Guests have phones too: their own only.
        g, gh = await self.login("visitor")
        self.assertEqual((await g.put("/api/phone-layouts/me", json=mine, headers=gh)).status, 200)
        self.assertEqual((await g.get(f"/api/phone-layouts/{self.ids['Marlina']}", headers=gh)).status, 403)
        # A wall tablet has none.
        t, th = await self.login("hallway", kind="tablet")
        self.assertEqual((await t.get("/api/phone-layout", headers=th)).status, 403)
        self.assertEqual((await t.get("/api/phone-layouts/me", headers=th)).status, 403)
        self.assertEqual((await self.client.get(f"/api/phone-layouts/{self.ids['Hallway']}")).status, 404)
        # The admin: everyone's (tablets left out), anyone's layout, reset everyone.
        listing = await (await self.client.get("/api/phone-layouts")).json()
        self.assertEqual({p["name"]: p["source"] for p in listing["people"]},
                         {"Chris": "default", "Marlina": "default", "Dante": "default", "Visitor": "own"})
        self.assertEqual(listing["default"], default)
        r = await self.client.put(f"/api/phone-layouts/{self.ids['Dante']}", json={"layout": mine})
        self.assertEqual((await r.json())["source"], "own")
        r = await self.client.post(f"/api/phone-layouts/{self.ids['Dante']}/reset")
        self.assertEqual((await r.json())["source"], "default")
        await self.client.put(f"/api/phone-layouts/{self.ids['Dante']}", json=mine)
        self.assertEqual((await (await self.client.post("/api/phone-layouts/reset-all")).json())["reset"], 2)
        listing = await (await self.client.get("/api/phone-layouts")).json()
        self.assertEqual({p["source"] for p in listing["people"]}, {"default"})
        # Bad layouts are refused with the reason.
        bad = json.loads(json.dumps(default))
        for p in bad["pages"]:
            p["tab"] = True
        r = await self.client.put("/api/phone-layouts/default", json=bad)
        self.assertEqual(r.status, 400)
        self.assertIn("At most 3", (await r.json())["error"])

    async def test_saved_wall_only_tiles_not_sent(self):
        m, mh = await self.login("marlina")
        self.h.store.save_phone_layout(self.ids["Marlina"], stored_with_wall_only())
        self.h.store.save_phone_layout("", stored_with_wall_only())
        for got in ((await (await m.get("/api/phone-layout", headers=mh)).json())["layout"],
                    (await (await self.client.get("/api/phone-layouts/default")).json())["layout"],
                    (await (await self.client.get(f"/api/phone-layouts/{self.ids['Marlina']}")).json())["layout"]):
            self.assertEqual([t["type"] for t in got["pages"][0]["tiles"]], ["weather", "dial", "camera"])
        # Sent back as it came, it saves.
        r = await m.put("/api/phone-layouts/me", json=stored_with_wall_only(), headers=mh)
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual([t["type"] for t in (await r.json())["layout"]["pages"][0]["tiles"]], ["weather", "dial", "camera"])

    async def test_live_message_goes_to_exactly_those_whose_phone_changed(self):
        heard = {}

        async def listen(name, client, headers):
            ws = await client._s.ws_connect(self.client.make_url("/api/ws"), headers=headers)
            heard[name] = []

            async def run():
                async for msg in ws:
                    data = json.loads(msg.data)
                    if data.get("type") == "dashboard":
                        heard[name].append(data)
            self.open.append(ws)
            return asyncio.get_running_loop().create_task(run())

        tasks = []
        for name in ("marlina", "dante", "visitor"):
            c, hd = await self.login(name)
            tasks.append(await listen(name, c, hd))
        t, th = await self.login("hallway", kind="tablet")
        tasks.append(await listen("hallway", t, th))
        await asyncio.sleep(0.1)
        settle = lambda: asyncio.sleep(0.15)  # noqa: E731

        default = (await (await self.client.get("/api/phone-layouts/default")).json())["layout"]
        # Dante has his own layout; the default changing reaches only those using it.
        await self.client.put(f"/api/phone-layouts/{self.ids['Dante']}", json=default)
        await settle()
        self.assertEqual({k: len(v) for k, v in heard.items()}, {"marlina": 0, "dante": 1, "visitor": 0, "hallway": 0})
        default["today"] = False
        await self.client.put("/api/phone-layouts/default", json=default)
        await settle()
        self.assertEqual({k: len(v) for k, v in heard.items()}, {"marlina": 1, "dante": 1, "visitor": 1, "hallway": 0})
        # Resetting Dante moves him back to the default: he hears it, nobody else does.
        await self.client.post(f"/api/phone-layouts/{self.ids['Dante']}/reset")
        await settle()
        self.assertEqual({k: len(v) for k, v in heard.items()}, {"marlina": 1, "dante": 2, "visitor": 1, "hallway": 0})
        # A reset of someone already on the default changes nothing, so says nothing.
        await self.client.post(f"/api/phone-layouts/{self.ids['Marlina']}/reset")
        await settle()
        self.assertEqual(len(heard["marlina"]), 1)
        for task in tasks:
            task.cancel()


if __name__ == "__main__":
    unittest.main()
