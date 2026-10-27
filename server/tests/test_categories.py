"""Categories everywhere (round 2): a card for every category in use - built-in, brand-new or made in
Newman - a device's category and icon overrides, and the household Home layout with who may change their own."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from newman import kinds  # noqa: E402
from newman.kinds import CATEGORIES, CATEGORY, KINDS, Category, Kind, icons, meta_json  # noqa: E402
from test_phone_home import Base  # noqa: E402


def cards_of(home):
    return {c["id"]: c for c in home["cards"]}


class NewCategoryTests(Base):
    """A category and kind nobody wrote code for get everything with no change anywhere."""

    async def asyncSetUp(self):
        self.cat = Category("sprinklers_t", "Sprinklers")
        CATEGORIES.append(self.cat)
        CATEGORY[self.cat.id] = self.cat
        KINDS["valve_t"] = Kind("valve_t", "Valve", "Valves", "sprinklers_t", [("on", "Watering"), ("off", "Off")],
                                active_state="on")
        await super().asyncSetUp()

    async def asyncTearDown(self):
        await super().asyncTearDown()
        CATEGORIES.remove(self.cat)
        CATEGORY.pop(self.cat.id, None)
        KINDS.pop("valve_t", None)

    async def test_a_new_category_gets_a_card_off_and_new(self):
        home = await (await self.client.get("/api/home")).json()  # seen: everything there is today
        self.assertNotIn("sprinklers_t", cards_of(home))
        hub = self.h.hub
        for i, st in ((1, "on"), (2, "off")):
            hub.upsert(f"fake.v{i}", kind="valve_t", name=f"Zone {i}", integration="fake")
            await hub.update(f"fake.v{i}", state=st)
        home = await (await self.client.get("/api/home")).json()
        c = cards_of(home)["sprinklers_t"]
        self.assertEqual((c["on"], c["new"], c["title"], c["category"], c["screen"], c["value"], c["detail"]),
                         (False, True, "Sprinklers", "sprinklers_t", None, "2 valves", "1 watering"))
        cats = {x["id"]: x for x in await (await self.client.get("/api/categories")).json()}
        self.assertEqual((cats["sprinklers_t"]["count"], cats["sprinklers_t"]["builtIn"]), (2, True))
        # Switching it on (the household layout) makes it a normal card.
        order = [{"id": x["id"], "on": x["on"] or x["id"] == "sprinklers_t"} for x in home["cards"]]
        r = await self.client.put("/api/home/household", json={"cards": order})
        self.assertEqual(r.status, 200, await r.text())
        c = cards_of(await (await self.client.get("/api/home")).json())["sprinklers_t"]
        self.assertEqual((c["on"], c["new"]), (True, False))


    async def test_built_in_cards_are_never_new(self):
        # Seen on a fresh hub: Security (its first lock, added after the first look) and People came up "new" and off.
        home = await (await self.client.get("/api/home")).json()
        for cid in ("today", "people"):
            self.assertFalse(cards_of(home)[cid]["new"], cid)
        hub = self.h.hub
        hub.upsert("fake.firstlock", kind="lock", name="Front lock", integration="fake")
        await hub.update("fake.firstlock", state="locked")
        # As if Newman had never shown Security or People before (a fresh hub that got its first lock later).
        v = dict(self.h.store.settings().get("home") or {})
        v["seen"] = [x for x in v.get("seen") or [] if x not in ("security", "people")]
        self.h.store.set_setting("home", v)
        cards = cards_of(await (await self.client.get("/api/home")).json())
        self.assertEqual((cards["security"]["on"], cards["security"]["new"]), (True, False))
        self.assertEqual((cards["people"]["on"], cards["people"]["new"]), (True, False))

class OverrideTests(Base):
    async def test_move_a_device_rename_and_vanish(self):
        hub = self.h.hub
        r = await self.client.put("/api/entities/fake.plug/category", json={"category": "appliances"})
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((await r.json())["category"], "appliances")
        self.assertEqual(hub.get("fake.plug").category, "appliances")
        home = cards_of(await (await self.client.get("/api/home")).json())
        self.assertEqual(home["appliances"]["value"], "Plug")  # one device: its name
        # A new category of its own; renaming it; moving the device back makes it disappear.
        r = await self.client.put("/api/entities/fake.plug/category", json={"newName": "Pool"})
        cid = (await r.json())["category"]
        self.assertEqual(cid, "cat.pool")
        cats = {x["id"]: x for x in await (await self.client.get("/api/categories")).json()}
        self.assertEqual((cats[cid]["name"], cats[cid]["builtIn"], cats[cid]["count"], cats[cid]["screen"]),
                         ("Pool", False, 1, None))
        self.assertEqual((await self.client.put(f"/api/categories/{cid}", json={"name": "Pool & hot tub"})).status, 200)
        self.assertEqual((await self.client.put("/api/categories/lighting", json={"name": "x"})).status, 400)
        home = await (await self.client.get("/api/home")).json()
        self.assertEqual(cards_of(home)[cid]["title"], "Pool & hot tub")
        self.assertEqual((await self.client.put("/api/entities/fake.plug/category", json={"category": None})).status, 200)
        self.assertEqual(hub.get("fake.plug").category, "lighting")
        self.assertNotIn(cid, [x["id"] for x in await (await self.client.get("/api/categories")).json()])
        self.assertEqual((await self.client.put("/api/entities/fake.plug/category", json={"category": "nope"})).status, 400)

    async def test_a_moved_light_leaves_the_lights_screen_role(self):
        r = await self.client.put("/api/entities/fake.c/category", json={"category": "appliances"})
        self.assertEqual(r.status, 200)
        home = cards_of(await (await self.client.get("/api/home")).json())
        self.assertEqual(home["lighting"]["detail"], "2 lights")  # Coffee + Kitchen Work, no longer Kitchen
        # Groups only take lights that are on the lights screen.
        r = await self.client.post("/api/groups", json={"name": "G", "members": ["fake.a", "fake.c"]})
        self.assertEqual(r.status, 400)

    async def test_icon(self):
        self.assertIn("appliance", icons())
        self.assertIn("lock", meta_json("t")["icons"])
        self.assertEqual(meta_json("t")["categories"][0], {"id": "security", "name": "Security", "screen": "security"})
        r = await self.client.put("/api/entities/fake.plug/icon", json={"icon": "appliance"})
        self.assertEqual((r.status, (await r.json())["icon"]), (200, "appliance"))
        self.assertEqual((await self.client.put("/api/entities/fake.plug/icon", json={"icon": "rocket"})).status, 400)
        r = await self.client.put("/api/entities/fake.plug/icon", json={"icon": None})
        self.assertIsNone((await r.json())["icon"])

    async def test_family_cant_move_or_rename(self):
        fam, _ = await self.family()
        self.assertEqual((await fam.put("/api/entities/fake.plug/category", json={"category": "appliances"})).status, 403)
        self.assertEqual((await fam.put("/api/entities/fake.plug/icon", json={"icon": "plug"})).status, 403)
        self.assertEqual((await fam.get("/api/categories")).status, 200)
        await fam.close()

    async def test_replace_and_remove_carry_the_overrides(self):
        hub = self.h.hub
        await self.client.put("/api/entities/fake.plug/category", json={"newName": "Pool"})
        await self.client.put("/api/entities/fake.plug/icon", json={"icon": "pump"})
        hub.upsert("fake.plug2", kind="switch", name="Plug 2", integration="fake")
        await hub.update("fake.plug2", state="off")
        r = await self.client.post("/api/entities/replace", json={"pairs": [{"from": "fake.plug", "to": "fake.plug2"}]})
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((hub.get("fake.plug2").category, self.h.categories.icon_of("fake.plug2")), ("cat.pool", "pump"))
        self.h.categories.forget("fake.plug2")
        self.assertEqual(hub.get("fake.plug2").category, "lighting")
        self.assertNotIn("cat.pool", [x["id"] for x in self.h.categories.list()])


class LayoutTests(Base):
    async def test_household_own_reset_and_can_edit(self):
        fam, uid = await self.family()
        home = await (await fam.get("/api/home")).json()
        self.assertEqual((home["canEditHome"], home["layout"]), (True, "household"))
        # The admin sets the household layout: everyone without their own follows it.
        r = await self.client.put("/api/home/household", json={"cards": [{"id": "security", "on": True},
                                                                         {"id": "cameras", "on": False}],
                                                               "favourites": ["fake.garage"]})
        self.assertEqual(r.status, 200, await r.text())
        home = await (await fam.get("/api/home")).json()
        self.assertEqual(([c["id"] for c in home["cards"]][:2], home["favourites"]), (["security", "cameras"], ["fake.garage"]))
        self.assertFalse(cards_of(home)["cameras"]["on"])
        # They make their own; the admin sees it and resets it.
        self.assertEqual((await fam.put("/api/home/me", json={"cards": [{"id": "cameras", "on": True}]})).status, 200)
        home = await (await fam.get("/api/home")).json()
        self.assertEqual((home["layout"], home["cards"][0]["id"], home["favourites"]), ("own", "cameras", ["fake.garage"]))
        lay = await (await self.client.get("/api/home/layouts")).json()
        me = next(p for p in lay["people"] if p["userId"] == uid)
        self.assertEqual((me["layout"], me["canEditHome"]), ("own", True))
        self.assertIn("security", [c["id"] for c in lay["household"]["cards"]])
        self.assertEqual((await self.client.post(f"/api/home/people/{uid}/reset")).status, 200)
        self.assertEqual((await (await fam.get("/api/home")).json())["layout"], "household")
        # Not allowed to edit: Edit Home refused, the household layout shown.
        await fam.put("/api/home/me", json={"cards": [{"id": "cameras", "on": True}]})
        r = await self.client.patch(f"/api/home/people/{uid}", json={"canEditHome": False})
        self.assertEqual(r.status, 200)
        home = await (await fam.get("/api/home")).json()
        self.assertEqual((home["canEditHome"], home["layout"]), (False, "household"))
        self.assertEqual((await fam.put("/api/home/me", json={"cards": []})).status, 403)
        # Admin-only, and an admin can't be locked out of their own.
        self.assertEqual((await fam.get("/api/home/layouts")).status, 403)
        self.assertEqual((await fam.put("/api/home/household", json={"cards": []})).status, 403)
        r = await self.client.patch(f"/api/home/people/{self.admin_id}", json={"canEditHome": False})
        self.assertEqual(r.status, 400)
        await fam.close()


class TodayRolesTests(unittest.TestCase):
    def test_today_roles_are_kind_metadata(self):
        self.assertEqual({k.today for k in KINDS.values() if k.today}, {"events", "weather"})  # tasks come from calendar servers


if __name__ == "__main__":
    unittest.main()
