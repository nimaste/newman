"""The wall's finer grid (24 x 16): older dashboards (12 x 8) upgraded once and losslessly, the catalog's
sizes, every tile's Look / When tapped / When held settings, and a tile running an automation."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import dashboards as D  # noqa: E402
from newman import phone_layouts as PL  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.store import Store  # noqa: E402
from test_locations import H  # noqa: E402


def old_dash():
    """A dashboard as saved before the finer grid: no "grid", tiles in 12 x 8 cells, settings from then."""
    return {"name": "Wall", "pages": [
        {"id": "overview", "name": "Overview", "icon": "home", "tiles": [
            {"id": "t1", "type": "climate", "x": 9, "y": 0, "w": 3, "h": 5, "config": {"entity": "x", "page": "climate"}},
            {"id": "t2", "type": "devices", "x": 9, "y": 5, "w": 3, "h": 3, "config": {"entities": ["a"], "style": "list"}},
            {"id": "t3", "type": "devices", "x": 0, "y": 0, "w": 4, "h": 3, "config": {"entities": ["b"]}},
            {"id": "t4", "type": "climate", "x": 4, "y": 0, "w": 5, "h": 8, "config": {"page": "gone"}}]},
        {"id": "climate", "name": "Climate", "icon": "thermometer", "tiles": [
            {"id": "t5", "type": "dial", "x": 0, "y": 0, "w": 8, "h": 8, "config": {"entity": "x"}}]}]}


class UpgradeTests(unittest.TestCase):
    def test_old_dashboards_double_and_keep_their_settings(self):
        up = D.upgrade(old_dash())
        self.assertEqual(up["grid"], [24, 16])
        tiles = {t["id"]: t for p in up["pages"] for t in p["tiles"]}
        self.assertEqual([(t["x"], t["y"], t["w"], t["h"]) for t in tiles.values()],
                         [(18, 0, 6, 10), (18, 10, 6, 6), (0, 0, 8, 6), (8, 0, 10, 16), (0, 0, 16, 16)])
        # Climate glance's "Opens page" is now When tapped: open that page (one since removed: dropped).
        self.assertEqual(tiles["t1"]["config"], {"entity": "x", "tap": {"do": "page", "page": "climate"}})
        self.assertEqual(tiles["t4"]["config"], {})
        # A devices tile's style is its Look (no style was the big tiles).
        self.assertEqual(tiles["t2"]["config"]["look"], "list")
        self.assertEqual(tiles["t3"]["config"]["look"], "tiles")
        # Only once: on the new grid nothing changes again.
        self.assertEqual(D.upgrade(up), up)
        clean, errors = D.clean(up)
        self.assertEqual(errors, [])
        self.assertEqual(D.with_home(up)["pages"], up["pages"])

    def test_a_page_from_before_saves_on_the_old_grid(self):
        # The editor sends "grid"; a page still open from before the upgrade doesn't - its tiles double.
        clean, errors = D.clean(old_dash())
        self.assertEqual(errors, [])
        self.assertEqual(clean["grid"], [24, 16])
        self.assertEqual(clean["pages"][0]["tiles"][0]["w"], 6)

    def test_saved_once_on_the_new_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(Path(tmp))
            d = s.save_dashboard("u_1", old_dash())
            self.assertEqual(json.loads(s._db.execute("SELECT body FROM dashboards").fetchone()[0]).get("grid"), None)
            self.assertEqual(d["pages"][0]["tiles"][0]["w"], 6)  # read on the new grid already
            s2 = Store(Path(tmp))  # the next start saves it upgraded
            body = json.loads(s2._db.execute("SELECT body FROM dashboards").fetchone()[0])
            self.assertEqual((body["grid"], body["pages"][0]["tiles"][0]["w"]), ([24, 16], 6))
            self.assertEqual(s2.dashboard(d["id"])["pages"][0]["tiles"][0]["w"], 6)
            s._db.close()
            s2._db.close()

    def test_phone_flow_reads_wall_sizes_on_either_grid(self):
        self.assertEqual(PL.from_dashboard(old_dash()), PL.from_dashboard(D.upgrade(old_dash())))


class CatalogTests(unittest.TestCase):
    def test_sizes_and_shared_fields(self):
        self.assertEqual((D.GRID_COLS, D.GRID_ROWS), (24, 16))
        self.assertEqual((D.TYPES["camera"]["min"], D.TYPES["camera"]["default"]), ([6, 6], [10, 8]))
        for t in D.TILE_TYPES:
            keys = [f["key"] for f in t["fields"]]
            self.assertEqual(keys[-3:], ["tap", "hold", "textSize"], t["type"])
            self.assertEqual("look" in keys, bool(t.get("lists")), t["type"])
            self.assertTrue(t["min"][0] <= t["default"][0] <= D.GRID_COLS and t["min"][1] <= t["default"][1] <= D.GRID_ROWS)
        self.assertEqual({t["type"] for t in D.TILE_TYPES if t.get("lists")}, {"climate", "devices", "values"})
        self.assertNotIn("own", [o["value"] for o in D.HOLD["options"]])
        # The wall's compact layout gives these a swipe page of their own (web/src/wall/compact.js).
        self.assertEqual({t["type"] for t in D.TILE_TYPES if t.get("ownPage")}, {"camera", "dial"})

    def test_starter_fits_the_grid(self):
        from test_dashboard_home import Hub
        clean, errors = D.clean(D.starter(Hub()))
        self.assertEqual(errors, [])
        self.assertEqual(clean["pages"][0]["tiles"][0]["w"], 24)  # the greeting, across the whole width


class ActionTests(unittest.TestCase):
    def tile(self, **config):
        return {"pages": [{"id": "a", "name": "A", "tiles": [{"id": "t", "type": "weather", "x": 0, "y": 0, "w": 8, "h": 6,
                                                               "config": config}]},
                          {"id": "b", "name": "B", "tiles": []}], "grid": D.GRID}

    def config(self, **c):
        clean, errors = D.clean(self.tile(**c))
        return clean["pages"][0]["tiles"][0]["config"], errors

    def test_choices(self):
        self.assertEqual(self.config(tap={"do": "page", "page": "b"}), ({"tap": {"do": "page", "page": "b"}}, []))
        self.assertEqual(self.config(tap={"do": "screen", "screen": "calendar", "extra": 1}),
                         ({"tap": {"do": "screen", "screen": "calendar"}}, []))
        self.assertEqual(self.config(hold={"do": "automation", "automation": "a_1"}),
                         ({"hold": {"do": "automation", "automation": "a_1"}}, []))
        self.assertEqual(self.config(tap={"do": "device", "entity": "x.lock", "command": "lock"}),
                         ({"tap": {"do": "device", "entity": "x.lock", "command": "lock"}}, []))
        self.assertEqual(self.config(tap={"do": "none"}), ({"tap": {"do": "none"}}, []))
        # The defaults aren't kept: its own thing on a tap, nothing on a hold (which has no "own").
        self.assertEqual(self.config(tap={"do": "own"}, hold={"do": "none"}), ({}, []))
        self.assertEqual(self.config(hold={"do": "own"}), ({}, []))

    def test_whats_missing_is_said(self):
        _, errors = self.config(tap={"do": "page", "page": "nope"})
        self.assertEqual(errors, ["Weather tile on “A”: When tapped - choose a page"])
        self.assertEqual(self.config(hold={"do": "screen", "screen": "nope"})[1], ["Weather tile on “A”: When held - choose a screen"])
        self.assertEqual(self.config(tap={"do": "automation"})[1], ["Weather tile on “A”: When tapped - choose an automation"])
        self.assertEqual(self.config(tap={"do": "device", "entity": "x"})[1],
                         ["Weather tile on “A”: When tapped - choose a device and what to do"])
        self.assertEqual(self.config(tap={"do": "dance"})[1], ["Weather tile on “A”: When tapped - unknown choice “dance”"])

    def test_look_only_where_it_means_something(self):
        self.assertEqual(self.config(look="list")[0], {})  # weather shows no list
        clean, _ = D.clean({"grid": D.GRID, "pages": [{"id": "a", "name": "A", "tiles": [
            {"type": "devices", "x": 0, "y": 0, "w": 6, "h": 6, "config": {"entities": ["x"], "look": "list"}},
            {"type": "devices", "x": 6, "y": 0, "w": 6, "h": 6, "config": {"entities": ["x"], "look": "auto"}},
            {"type": "values", "x": 12, "y": 0, "w": 6, "h": 6, "config": {"items": [], "look": "sideways"}}]}]})
        a, b, c = (t["config"] for t in clean["pages"][0]["tiles"])
        # The phone apps still go by a devices tile's "style".
        self.assertEqual((a["look"], a["style"], b["look"], b["style"]), ("list", "list", "auto", "tiles"))
        self.assertNotIn("look", c)


class TileRunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", self.data)
        self.h = H(self.data)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        ids = {}
        for name, role in (("Marlina", "family"), ("Hallway", "tablet")):
            r = await self.client.post("/api/users", json={"username": name.lower(), "name": name, "role": role,
                                                          "password": "another pass"})
            ids[name] = (await r.json())["id"]
        self.chris = await self.token("chris", "correct horse")
        self.marlina = await self.token("marlina")
        self.tablet = await self.token("hallway", kind="tablet")
        self.auto = self.h.engine.save({"name": "Movie night", "trigger": {"type": "time", "time": "21:00"},
                                        "recipients": [self.h.store.people()[0]["id"]]})
        st, self.dash = await self.call("POST", "/api/dashboards", self.chris, json={
            "name": "Hall", "userId": ids["Hallway"], "grid": D.GRID, "pages": [{"id": "a", "name": "A", "tiles": [
                {"id": "t_run", "type": "weather", "x": 0, "y": 0, "w": 8, "h": 6,
                 "config": {"hold": {"do": "automation", "automation": self.auto["id"]}}},
                {"id": "t_page", "type": "weather", "x": 8, "y": 0, "w": 8, "h": 6,
                 "config": {"tap": {"do": "page", "page": "a"}}}]}]})
        self.assertEqual(st, 200, self.dash)

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def token(self, username, password="another pass", kind="app"):
        r = await self.client.post("/api/auth/login", json={"username": username, "password": password, "kind": kind})
        return {"Authorization": "Bearer " + (await r.json())["token"]}

    async def call(self, method, path, who, **kw):
        r = await self.client.request(method, path, headers=who, **kw)
        try:
            body = await r.json()
        except Exception:
            body = None
        return r.status, body

    async def test_the_tablet_runs_what_its_tile_says(self):
        url = f"/api/dashboards/{self.dash['id']}/tiles/t_run/run"
        st, run = await self.call("POST", url, self.tablet, json={"which": "hold"})
        self.assertEqual(st, 200, run)
        self.assertEqual(run["automationId"], self.auto["id"])
        # Only the action that tile has: not its tap, not another tile, not someone else's dashboard.
        self.assertEqual((await self.call("POST", url, self.tablet, json={"which": "tap"}))[0], 404)
        self.assertEqual((await self.call("POST", url.replace("t_run", "t_page"), self.tablet, json={"which": "tap"}))[0], 404)
        self.assertEqual((await self.call("POST", url, self.tablet, json={"which": "sideways"}))[0], 400)
        self.assertEqual((await self.call("POST", url, self.marlina, json={"which": "hold"}))[0], 404)
        # The tablet still can't run automations itself.
        self.assertEqual((await self.call("POST", f"/api/automations/{self.auto['id']}/run", self.tablet, json={}))[0], 403)

    async def test_the_catalog_lists_the_screens(self):
        st, cat = await self.call("GET", "/api/dashboards/tiles", self.tablet)
        self.assertEqual(st, 200)
        self.assertEqual((cat["cols"], cat["rows"]), (24, 16))
        self.assertEqual([s["id"] for s in cat["screens"]], ["cameras", "calendar", "weather", "todo"])


if __name__ == "__main__":
    unittest.main()
