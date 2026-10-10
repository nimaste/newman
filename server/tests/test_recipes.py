"""Forkast recipes and cook mode: amounts (scaling, kitchen fractions, unit names), times in steps, the voice
phrases (opening by a fuzzy name, "which one?", steps, ingredients, scaling, timer offers), cook mode itself,
and the connection against a fake Forkast (only the sign-in token kept; a 401 asks to sign in again)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from newman import recipes as R  # noqa: E402
from newman.api import build_app  # noqa: E402
from newman.config import config  # noqa: E402
from newman.voice import intents as I  # noqa: E402
from test_auth import Browser, H  # noqa: E402

EMAIL, PASSWORD = "cook@example.com", "cook pass 1"
PNG = b"\x89PNG\r\n\x1a\nfakepng"


def ing(name, q=None, unit=None, notes=None, n=0):
    return {"name": name, "quantity": q, "unit": unit, "notes": notes, "sortOrder": n}


def recipe(rid, title, servings, ingredients, steps, image=None, prep=None, cook=None):
    return {"id": rid, "title": title, "baseServings": servings, "imageUrl": image, "prepTimeMinutes": prep,
            "cookTimeMinutes": cook, "updatedAt": "2026-10-01T10:00:00.000Z",
            "ingredients": [{**i, "sortOrder": n} for n, i in enumerate(ingredients)],
            "instructions": [{"stepNumber": n + 1, "text": t} for n, t in enumerate(steps)]}


# Made-up recipes (mixed unit spellings, duplicates and amounts without a quantity on purpose).
RECIPES = [
    recipe("r1", "Fluffy Buttermilk Pancakes", 4, [
        ing("All-purpose flour", 2, "Cups"), ing("Sugar", 2, "tablespoon"), ing("Baking powder", 1.5, "tsp"),
        ing("Salt", 0.5, "teaspoon"), ing("Buttermilk", 1.75, "cup"), ing("Eggs", 2, "Large"),
        ing("Butter", 3, "Tablespoons", "melted"), ing("Maple syrup", None, None, "to serve")], [
        "Whisk the flour, sugar, baking powder and salt in a big bowl.",
        "Whisk the buttermilk, eggs and melted butter, then stir into the dry ingredients.",
        "Let the batter rest for 10 minutes.",
        "Cook on a 375°F griddle for 2-3 minutes per side."], image="/uploads/pancakes.png", prep=10, cook=20),
    recipe("r2", "Grandma's Lemon Chicken Thighs", 6, [
        ing("Chicken thighs", 3, "lb"), ing("Lemons", 2), ing("Garlic", 4, "cloves"), ing("Olive oil", 0.25, "cup")], [
        "Marinate the chicken with lemon and garlic for 1 hour 30 minutes.", "Roast at 425°F for 35 to 40 minutes."]),
    recipe("r3", "Honey Garlic Chicken Wings Recipe", 4, [
        ing("Chicken wings", 2, "pound"), ing("Honey", 0.333, "cup"), ing("Soy sauce", 2, "Tbsp"),
        ing("Dark soy sauce", 1, "tbsp")], ["Bake the wings 45 minutes.", "Toss in the sauce."]),
    recipe("r4", "Lemon Drizzle Cake", 8, [
        ing("Sugar", 1, "cup", "for the cake"), ing("Sugar", 0.25, "cup", "for the drizzle"),
        ing("Self-raising flour", 225, "gram"), ing("Milk", 120, "ml"), ing("Salt to taste"),
        ing("Cilantro/Parsley to top")], ["Cream the butter and sugar.", "Bake 45 to 50 minutes.", "Drizzle."]),
]
TITLES = [{"id": r["id"], "title": r["title"]} for r in RECIPES]


def summary(r):
    s = {k: v for k, v in r.items() if k not in ("instructions",)}
    s["_count"] = {"instructions": len(r["instructions"])}
    s["tags"] = []
    return s


class FakeForkast:
    """Just enough of Forkast: POST /api/auth/login, GET /api/recipes (2 a page, to make Newman page through),
    GET /api/recipes/:id, /uploads/… - a bearer token from the login is required."""

    def __init__(self):
        self.password, self.tokens, self.calls, self.logins, self.runner = PASSWORD, set(), [], [], None
        self.recipes = list(RECIPES)

    def authed(self, req):
        h = req.headers.get("Authorization", "")
        return h.startswith("Bearer ") and h[7:] in self.tokens

    async def login(self, req):
        data = await req.json()
        self.logins.append(data)
        if data.get("email") != EMAIL or data.get("password") != self.password:
            return web.json_response({"error": "Invalid credentials"}, status=401)
        tok = f"jwt-{len(self.tokens) + 1}"
        self.tokens.add(tok)
        return web.json_response({"token": tok, "user": {"id": "u1", "email": EMAIL, "isAdmin": True}})

    async def listing(self, req):
        self.calls.append(("list", dict(req.query)))
        if not self.authed(req):
            return web.json_response({"error": "Authentication required"}, status=401)
        page, limit = int(req.query.get("page", 1)), min(2, int(req.query.get("limit", 20)))
        got = self.recipes[(page - 1) * limit:page * limit]
        return web.json_response({"recipes": [summary(r) for r in got], "total": len(self.recipes), "page": page,
                                  "totalPages": -(-len(self.recipes) // limit)})

    async def one(self, req):
        self.calls.append(("get", req.match_info["id"]))
        if not self.authed(req):
            return web.json_response({"error": "Authentication required"}, status=401)
        r = next((x for x in self.recipes if x["id"] == req.match_info["id"]), None)
        if not r:
            return web.json_response({"error": "Recipe not found"}, status=404)
        return web.json_response({**r, "tags": [], "averageRating": None})

    async def upload(self, req):
        return web.Response(body=PNG, content_type="image/png")

    async def start(self):
        app = web.Application()
        app.router.add_post("/api/auth/login", self.login)
        app.router.add_get("/api/recipes", self.listing)
        app.router.add_get("/api/recipes/{id}", self.one)
        app.router.add_get("/uploads/{name}", self.upload)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

    async def stop(self):
        await self.runner.cleanup()


async def wait_for(cond, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


# -- amounts and times ----------------------------------------------------------------------------------

class AmountTests(unittest.TestCase):
    def test_shown(self):
        cases = [((1.5, "Cups"), "1½ cups"), ((1, "cup"), "1 cup"), ((0.75, "cup"), "¾ cup"), ((0.333, "teaspoon"), "⅓ tsp"),
                 ((2, "Tablespoon"), "2 tbsp"), ((2, "T"), "2 tbsp"), ((250.4, "gram"), "250.4 g"), ((7.3, "g"), "7.3 g"), ((52.5 * 2, "g"), "105 g"),
                 ((1234, "ml"), "1234 ml"), ((2, "Large"), "2 large"), ((3, "cloves"), "3 cloves"), ((1, "clove"), "1 clove"),
                 ((2, ""), "2"), ((0.66, None), "0.66"), ((1.02, "lb"), "1.02 lb"), ((0.01, "tsp"), "0.01 tsp"), ((0.6, "cup"), "0.6 cup"),
                 ((2, "sprinkles"), "2 sprinkles")]
        for (q, u), want in cases:
            self.assertEqual(R.show_amount(q, u), want, (q, u))

    def test_said(self):
        cases = [((2, "Tbsp", "soy sauce"), "2 tablespoons of soy sauce"), ((1, "teaspoon", "salt"), "1 teaspoon of salt"),
                 ((0.5, "cup", "sugar"), "half a cup of sugar"), ((1.5, "lb", "chicken wings"), "1 and a half pounds of chicken wings"),
                 ((0.333, "Cups", "milk"), "a third of a cup of milk"), ((2.75, "cup", "flour"), "2 and three quarters cups of flour"),
                 ((2, "Large", "eggs"), "2 large eggs"), ((3, "", "lemons"), "3 lemons"), ((0.5, "", "onion"), "half an onion"),
                 ((225, "gram", "flour"), "225 grams of flour"), ((1, "g", "yeast"), "1 gram of yeast"),
                 ((120, "ml", "milk"), "120 millilitres of milk")]
        for (q, u, n), want in cases:
            self.assertEqual(R.say_amount(q, u, n), want, (q, u, n))

    def test_no_quantity(self):
        self.assertEqual(R.say_amount(None, "", "Salt to taste"), "Salt to taste")
        self.assertEqual(R.say_amount(None, "", "Pepper", "to taste"), "Pepper, to taste")
        self.assertEqual(R.say_amount(None, "", "Cilantro/Parsley to top"), "Cilantro or Parsley to top, as much as you like")
        self.assertEqual(R.say_amount(None, "", "Oil"), "Oil, as needed")
        self.assertEqual(R.say_amount(None, "pinch", "nutmeg"), "a pinch of nutmeg, as needed")

    def test_times_in_steps(self):
        self.assertEqual(R.step_timer("Bake for 25 minutes."), {"seconds": 1500, "range": False})
        self.assertEqual(R.step_timer("Simmer 10 to 12 minutes, stirring."), {"seconds": 720, "range": True})
        self.assertEqual(R.step_timer("Cook 2-3 mins per side"), {"seconds": 180, "range": True})
        self.assertEqual(R.step_timer("Marinate for 1 hour 30 minutes."), {"seconds": 5400, "range": False})
        self.assertEqual(R.step_timer("Rest for half an hour."), {"seconds": 1800, "range": False})
        self.assertEqual(R.step_timer("Fry 2 minutes, then bake 20 minutes."), {"seconds": 1200, "range": False})
        self.assertIsNone(R.step_timer("Stir well and serve."))

    def test_spoken_steps(self):
        self.assertEqual(R.speakable("Cook on a 375°F griddle for 2-3 minutes per side"),
                         "Cook on a 375 degrees Fahrenheit griddle for 2 to 3 minutes per side.")
        self.assertEqual(R.speakable("Add 1 1/2 tbsp oil"), "Add 1 and a half tablespoons oil.")
        self.assertEqual(R.spoken_title("Honey Garlic Chicken Wings Recipe"), "Honey Garlic Chicken Wings")


# -- what was said ------------------------------------------------------------------------------------------

def world(cook=None, devices=(), timers=(), recipes=TITLES):
    return I.World(devices=list(devices), timers=list(timers), recipes=recipes, cook=cook)


OPEN = {"title": "Fluffy Buttermilk Pancakes", "steps": 4, "step": 0, "read": False, "offer": False, "choosing": None,
        "ingredients": [i["name"] for i in RECIPES[0]["ingredients"]]}


class CookIntentTests(unittest.TestCase):
    def cook(self, text, **kw):
        it = I.parse(text, world(**kw))
        return it.value if it.type == "cook" else it.type

    def test_opening_by_name(self):
        self.assertEqual(self.cook("Hey Newman, pull up the pancakes recipe"), {"do": "open", "id": "r1"})
        self.assertEqual(self.cook("show me the pancakes"), {"do": "open", "id": "r1"})
        self.assertEqual(self.cook("open the lemon chicken recipe"), {"do": "open", "id": "r2"})
        self.assertEqual(self.cook("pull up my grandmas lemon chicken"), {"do": "open", "id": "r2"})
        self.assertEqual(self.cook("bring up the honey garlic wings"), {"do": "open", "id": "r3"})
        self.assertEqual(self.cook("pull up the buttermilk pan cakes"), {"do": "open", "id": "r1"})  # split by speech-to-text
        self.assertEqual(self.cook("how do I make pancakes"), {"do": "open", "id": "r1"})
        # Two chicken recipes: ask which.
        self.assertEqual(self.cook("pull up the chicken recipe"), {"do": "choose", "ids": ["r2", "r3"]})
        # Not a recipe we have: the hub looks again (it may be new) and says so.
        self.assertEqual(self.cook("pull up the lasagna recipe"), {"do": "open", "words": ["lasagna"]})

    def test_other_things_stay_what_they_were(self):
        door = I.Device("cam.front", "Front Door", "camera", "online")
        self.assertEqual(I.parse("show me the front door", world(devices=[door])).show, "camera")
        self.assertEqual(self.cook("show me the weather"), "show")
        self.assertEqual(self.cook("next"), "unknown")                 # no recipe open: not cook mode
        self.assertEqual(self.cook("set a timer for 10 minutes", cook=OPEN), "timer_set")
        thermo = I.Device("t.main", "Main Floor", "thermostat", "heat", {"targetTemp": 20})
        self.assertEqual(I.parse("set the main floor to 21", world(cook=OPEN, devices=[thermo])).type, "command")
        self.assertIsNone(I.parse("next", world(recipes=None)).value)  # no recipe connection at all

    def test_steps(self):
        cases = {"next": "next", "next step": "next", "what's next": "next", "okay what's next": "next",
                 "go back": "previous", "previous step": "previous", "go back a step": "previous",
                 "repeat that": "repeat", "say that again": "repeat", "what's the last step": "last",
                 "what's the final step": "last", "how many steps are there": "count", "what step am I on": "where"}
        for text, do in cases.items():
            self.assertEqual(self.cook(text, cook=OPEN), {"do": do}, text)
        self.assertEqual(self.cook("read me step 1", cook=OPEN), {"do": "step", "step": 1})
        self.assertEqual(self.cook("what's step four", cook=OPEN), {"do": "step", "step": 4})
        self.assertEqual(self.cook("what's the 3rd step", cook=OPEN), {"do": "step", "step": 3})
        self.assertEqual(self.cook("what's the first step", cook=OPEN), {"do": "step", "step": 1})
        self.assertEqual(self.cook("go to step 12", cook=OPEN), {"do": "step", "step": 12})
        # Step words with no recipe open still reach cook mode (it says nothing is open).
        self.assertEqual(self.cook("read me step 2"), {"do": "step", "step": 2})

    def test_ingredients_and_scaling(self):
        self.assertEqual(self.cook("what are the ingredients", cook=OPEN), {"do": "ingredients"})
        self.assertEqual(self.cook("read the ingredients", cook=OPEN), {"do": "ingredients"})
        it = self.cook("how much buttermilk do I need", cook=OPEN)
        self.assertEqual((it["do"], it["ingredients"]), ("how_much", [4]))
        self.assertEqual(self.cook("how many eggs", cook=OPEN)["ingredients"], [5])
        self.assertEqual(self.cook("how much saffron", cook=OPEN), {"do": "how_much", "ingredients": [], "words": ["saffron"]})
        self.assertEqual(self.cook("how much time is left", cook=OPEN), "timer_query")
        cases = {"double it": {"factor": 2.0}, "double the recipe": {"factor": 2.0}, "half": {"factor": 0.5},
                 "cut it in half": {"factor": 0.5}, "make it for 8": {"servings": 8.0},
                 "scale it to 6 servings": {"servings": 6.0}, "for 10 people": {"servings": 10.0},
                 "back to the original amounts": {"reset": True}}
        for text, want in cases.items():
            self.assertEqual(self.cook(text, cook=OPEN), {"do": "scale", **want}, text)
        self.assertEqual(self.cook("how many servings does it make", cook=OPEN), {"do": "servings_info"})

    def test_close(self):
        for text in ("close the recipe", "I'm done cooking", "stop cooking", "exit cook mode", "I'm done"):
            self.assertEqual(self.cook(text, cook=OPEN), {"do": "close"}, text)

    def test_timer_offer(self):
        offered = {**OPEN, "offer": True}
        for text in ("yes", "yes please", "yeah", "sure", "okay", "do it", "start the timer"):
            self.assertEqual(self.cook(text, cook=offered), {"do": "timer"}, text)
        for text in ("no", "no thanks", "don't bother", "nope"):
            self.assertEqual(self.cook(text, cook=offered), {"do": "no_timer"}, text)
        self.assertEqual(self.cook("yes"), "unknown")  # nothing offered: no timer
        ringing = [{"id": "t", "label": "", "seconds": 60, "state": "ringing", "endsAt": 0}]
        self.assertEqual(self.cook("okay", cook=offered, timers=ringing), "stop")

    def test_which_one(self):
        choosing = {"choosing": [{"id": "r2", "title": "Grandma's Lemon Chicken Thighs"},
                                 {"id": "r3", "title": "Honey Garlic Chicken Wings Recipe"}]}
        self.assertEqual(self.cook("the honey one", cook=choosing), {"do": "open", "id": "r3"})
        self.assertEqual(self.cook("lemon chicken", cook=choosing), {"do": "open", "id": "r2"})
        self.assertEqual(self.cook("the second one", cook=choosing), {"do": "open", "id": "r3"})
        self.assertEqual(self.cook("the first one", cook=choosing), {"do": "open", "id": "r2"})


# -- cook mode itself ----------------------------------------------------------------------------------------

class FakeBook:
    status = "connected"

    def __init__(self):
        self.refreshed = 0

    def summaries(self):
        return [{"id": r["id"], "title": r["title"]} for r in RECIPES]

    async def refresh(self):
        self.refreshed += 1

    async def recipe(self, rid):
        return next(r for r in RECIPES if r["id"] == rid)


class KitchenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.pushed = []

        async def push(uid, msg):
            self.pushed.append((uid, msg))
        self.book = FakeBook()
        self.k = R.Kitchen(lambda: self.book, push, clock=lambda: self.now)

    async def say(self, **req):
        return (await self.k.voice("tab", req))

    async def test_steps_and_ranges(self):
        out = await self.say(do="open", id="r1")
        self.assertEqual(out["reply"], "Here's Fluffy Buttermilk Pancakes: 8 ingredients and 4 steps.")
        self.assertEqual(self.pushed[-1][1]["cook"]["recipe"]["title"], "Fluffy Buttermilk Pancakes")
        self.assertEqual((await self.say(do="next"))["reply"],
                         "Step 1: Whisk the flour, sugar, baking powder and salt in a big bowl.")
        self.assertTrue((await self.say(do="next"))["reply"].startswith("Step 2: Whisk the buttermilk"))
        self.assertTrue((await self.say(do="previous"))["reply"].startswith("Step 1:"))
        self.assertEqual((await self.say(do="previous"))["reply"], "This is the first step.")
        self.assertEqual((await self.say(do="step", step=9))["reply"], "There are only 4 steps.")
        self.assertEqual((await self.say(do="step", step=0))["reply"], "The steps go from 1 to 4.")
        self.assertTrue((await self.say(do="last"))["reply"].startswith("Step 4: Cook on a 375 degrees Fahrenheit"))
        self.assertEqual((await self.say(do="next"))["reply"], "That was the last step.")
        self.assertEqual((await self.say(do="where"))["reply"], "You're on step 4 of 4.")
        self.assertEqual((await self.say(do="count"))["reply"], "There are 4 steps. You're on step 4.")
        self.assertEqual(self.k.view("tab")["step"], 3)

    async def test_timer_offer_needs_a_yes(self):
        await self.say(do="open", id="r1")
        out = await self.say(do="step", step=3)
        self.assertEqual(out["reply"], "Step 3: Let the batter rest for 10 minutes. Want a timer for 10 minutes?")
        self.assertTrue(self.k.voice_json("tab")["offer"])
        self.assertEqual(await self.say(do="timer"), {"timer": {"seconds": 600, "label": "step 3"}, "reply": ""})
        self.assertEqual((await self.say(do="timer"))["reply"], "Okay.")  # only once
        out = await self.say(do="step", step=4)
        self.assertTrue(out["reply"].endswith("Want a timer for 3 minutes? That's the longer time."), out)
        self.assertEqual((await self.say(do="no_timer"))["reply"], "Okay, no timer.")
        await self.say(do="step", step=4)
        await self.say(do="ingredients")  # anything else and the offer is gone
        self.assertFalse(self.k.voice_json("tab")["offer"])
        out = await self.say(do="step", step=1)
        self.assertNotIn("timer", out["reply"])
        self.assertFalse(self.k.voice_json("tab")["offer"])
        # An offer goes stale.
        await self.say(do="step", step=3)
        self.now += R.OFFER_S + 1
        self.assertEqual((await self.say(do="timer"))["reply"], "Okay.")

    async def test_ingredients_amounts_and_scaling(self):
        await self.say(do="open", id="r4")
        out = await self.say(do="how_much", ingredients=[0, 1], words=["sugar"])
        self.assertEqual(out["reply"], "1 cup of Sugar, for the cake and a quarter of a cup of Sugar, for the drizzle.")
        out = await self.say(do="how_much", ingredients=[4], words=["salt"])
        self.assertEqual(out["reply"], "Salt to taste.")
        out = await self.say(do="how_much", ingredients=[5], words=["cilantro"])
        self.assertEqual(out["reply"], "Cilantro or Parsley to top, as much as you like.")
        out = await self.say(do="how_much", ingredients=[], words=["saffron"])
        self.assertEqual(out["reply"], "I don't see saffron in this recipe.")
        self.assertEqual((await self.say(do="scale", factor=0.5))["reply"], "Okay, 4 servings, half the recipe.")
        v = self.k.view("tab")
        self.assertTrue(v["scaled"])
        self.assertEqual([i["amount"] for i in v["ingredients"]], ["½ cup", "⅛ cup", "112.5 g", "60 ml", "", ""])
        out = await self.say(do="how_much", ingredients=[2], words=["flour"])
        self.assertEqual(out["reply"], "For 4 servings: 112.5 grams of Self-raising flour.")
        self.assertEqual((await self.say(do="servings_info"))["reply"], "It's scaled to 4 servings. The recipe makes 8.")
        self.assertEqual((await self.say(do="scale", reset=True))["reply"], "Okay, back to the original 8 servings.")
        self.assertFalse(self.k.view("tab")["scaled"])
        await self.say(do="open", id="r1")
        self.assertEqual((await self.say(do="scale", servings=8))["reply"], "Okay, 8 servings, double the recipe.")
        v = self.k.view("tab")
        self.assertEqual(v["ingredients"][0]["amount"], "4 cups")
        self.assertEqual(v["ingredients"][4]["amount"], "3½ cups")
        self.assertEqual(v["ingredients"][6]["amount"], "6 tbsp")
        out = await self.say(do="ingredients")
        self.assertTrue(out["reply"].startswith("For 8 servings: you need 4 cups of All-purpose flour, 4 tablespoons of Sugar"), out)
        self.assertTrue(out["reply"].endswith("and Maple syrup, as much as you like."), out)
        self.assertEqual((await self.say(do="scale", servings=500))["reply"], "I can scale it from 1 to 100 servings.")

    async def test_which_one_close_and_expiry(self):
        out = await self.say(do="choose", ids=["r2", "r3"])
        self.assertEqual(out["reply"], "I found two: Grandma's Lemon Chicken Thighs or Honey Garlic Chicken Wings. Which one?")
        self.assertEqual([c["id"] for c in self.k.voice_json("tab")["choosing"]], ["r2", "r3"])
        await self.say(do="open", id="r3")
        self.assertIsNone(self.k.voice_json("tab")["choosing"])
        out = await self.say(do="open", words=["lasagna"])
        self.assertEqual(out["reply"], "I couldn't find a recipe called lasagna.")
        self.assertEqual(self.book.refreshed, 1)  # looked again first
        self.assertEqual((await self.say(do="close"))["reply"], "Okay, I closed the recipe.")
        self.assertIsNone(self.pushed[-1][1]["cook"])
        self.assertEqual((await self.say(do="next"))["reply"], "There's no recipe open. Ask me to pull one up first.")
        await self.say(do="open", id="r1")
        self.now += R.IDLE_S + 1
        self.assertIsNone(self.k.view("tab"))
        self.assertIsNone(self.k.voice_json("tab"))
        # Each tablet has its own.
        await self.say(do="open", id="r1")
        self.assertIsNone(self.k.view("other tablet"))

    async def test_touch(self):
        await self.say(do="open", id="r1")
        v = await self.k.change("tab", step=2, servings=2)
        self.assertEqual((v["step"], v["servingsText"], v["ingredients"][0]["amount"]), (2, "2", "1 cup"))
        self.assertEqual((await self.say(do="next"))["reply"][:7], "Step 4:")


# -- the connection and the routes ----------------------------------------------------------------------------

class ForkastHubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        object.__setattr__(config, "data_dir", self.data)
        self.fake = FakeForkast()
        self.url = await self.fake.start()
        self.h = H(self.data)
        self.client = TestClient(TestServer(build_app(self.h)))
        await self.client.start_server()
        await self.client.post("/api/auth/setup", json={"username": "chris", "name": "Chris", "password": "correct horse"})
        r = await self.client.post("/api/users", json={"username": "kitchen", "name": "Kitchen tablet", "role": "tablet",
                                                       "password": "wall tablet pass"})
        self.tab_id = (await r.json())["id"]
        self.tab = Browser(self.client)
        r = await self.tab.post("/api/auth/login", json={"username": "kitchen", "password": "wall tablet pass", "kind": "tablet"})
        self.tok = (await r.json())["token"]
        self.auth = {"Authorization": "Bearer " + self.tok}

    async def asyncTearDown(self):
        await self.tab.close()
        await self.h.integrations.stop_all()
        await self.client.close()
        await self.fake.stop()
        self.tmp.cleanup()

    async def connect(self, password=PASSWORD):
        r = await self.client.post("/api/integrations", json={"type": "forkast", "name": "Forkast", "config": {
            "url": self.url, "username": EMAIL, "password": password}})
        self.assertEqual(r.status, 200, await r.text())
        rec = await r.json()
        self.inst = self.h.integrations.instances[rec["id"]]
        await wait_for(lambda: self.inst.status in ("connected", "pairing", "error"))
        return rec

    def stored(self, iid):
        return next(r for r in self.h.store.integrations() if r["id"] == iid)["config"]

    async def say(self, text):
        r = await self.tab.post("/api/voice/utterance?speak=0", json={"text": text}, headers=self.auth)
        self.assertEqual(r.status, 200, await r.text())
        return await r.json()

    async def test_sign_in_keeps_only_the_token(self):
        rec = await self.connect()
        self.assertEqual(self.inst.status, "connected", self.inst.detail)
        self.assertEqual(self.inst.detail, "4 recipes")
        cfg = self.stored(rec["id"])
        self.assertEqual(cfg, {"url": self.url, "username": EMAIL})          # the password is gone…
        tok = json.loads((self.data / "integrations" / rec["id"] / "token.json").read_text())
        self.assertEqual(tok, {"email": EMAIL, "token": "jwt-1"})          # …only the token is kept
        self.assertNotIn(PASSWORD, json.dumps(tok))
        self.assertEqual(oct((self.data / "integrations" / rec["id"] / "token.json").stat().st_mode & 0o777), "0o600")
        self.assertEqual([c[1]["page"] for c in self.fake.calls if c[0] == "list"], ["1", "2"])  # paged through
        book = self.h.hub.get(f"{rec['id']}.recipes")
        self.assertEqual((book.kind, book.state, book.attrs["recipes"]), ("recipe_book", "ready", 4))
        # A restart signs in with the kept token alone (no second login).
        await self.h.integrations.update(rec["id"], name="Recipes")
        self.inst = self.h.integrations.instances[rec["id"]]
        self.assertTrue(await wait_for(lambda: self.inst.status == "connected"))
        self.assertEqual(len(self.fake.logins), 1)

    async def test_401_asks_to_sign_in_again(self):
        rec = await self.connect()
        self.fake.tokens.clear()  # Forkast stops accepting the token
        r = await self.tab.get("/api/recipes?refresh=1", headers=self.auth)
        self.assertEqual((await r.json())["available"], False)
        self.assertEqual(self.inst.status, "pairing")
        self.assertIn("Settings", self.inst.detail)
        self.assertFalse((self.data / "integrations" / rec["id"] / "token.json").exists())
        self.assertEqual((await self.say("pull up the pancakes"))["reply"], "Forkast needs signing in again on the hub.")
        # The password once more: signed in again, and it's dropped again.
        await self.h.integrations.update(rec["id"], cfg={"password": PASSWORD})
        self.inst = self.h.integrations.instances[rec["id"]]
        self.assertTrue(await wait_for(lambda: self.inst.status == "connected"))
        self.assertNotIn("password", self.stored(rec["id"]))

    async def test_wrong_password(self):
        await self.connect("nope nope")
        self.assertEqual(self.inst.status, "pairing")
        self.assertIn("didn't accept", self.inst.detail)

    async def test_cook_mode_by_voice_and_touch(self):
        await self.connect()
        ws = await self.tab._s.ws_connect(self.client.make_url("/api/ws"), headers=self.auth)
        got = []

        async def listen():
            async for msg in ws:
                d = json.loads(msg.data)
                if d.get("type") == "cook":
                    got.append(d["cook"])
        task = asyncio.create_task(listen())
        out = await self.say("Hey Newman, pull up the chicken recipe")
        self.assertEqual(out["reply"], "I found two: Grandma's Lemon Chicken Thighs or Honey Garlic Chicken Wings. Which one?")
        out = await self.say("the honey one")
        self.assertEqual(out["reply"], "Here's Honey Garlic Chicken Wings: 4 ingredients and 2 steps.")
        self.assertTrue(await wait_for(lambda: got and got[-1] and got[-1]["recipe"]["id"] == "r3"))
        out = await self.say("how much soy sauce")
        self.assertEqual(out["reply"], "2 tablespoons of Soy sauce and 1 tablespoon of Dark soy sauce.")
        out = await self.say("read me step one")
        self.assertEqual(out["reply"], "Step 1: Bake the wings 45 minutes. Want a timer for 45 minutes?")
        out = await self.say("yes please")
        self.assertEqual(out["reply"], "Okay, step 1 timer for 45 minutes, starting now.")
        r = await self.tab.get("/api/voice/timers", headers=self.auth)
        timers = (await r.json())["timers"]
        self.assertEqual([(t["label"], t["seconds"]) for t in timers], [("step 1", 2700)])
        self.assertEqual((await self.say("double it"))["reply"], "Okay, 8 servings, double the recipe.")
        r = await self.tab.get("/api/cook", headers=self.auth)
        v = (await r.json())["cook"]
        self.assertEqual((v["servingsText"], v["ingredients"][1]["amount"], v["step"]), ("8", "⅔ cup", 0))
        # Touch: a step, the servings, the picture, close.
        r = await self.tab.patch("/api/cook", json={"step": 1, "servings": 2}, headers=self.auth)
        self.assertEqual(r.status, 200, await r.text())
        v = (await r.json())["cook"]
        self.assertEqual((v["step"], v["ingredients"][0]["amount"]), (1, "1 lb"))
        r = await self.tab.patch("/api/cook", json={"step": "two"}, headers=self.auth)
        self.assertEqual(r.status, 400)
        r = await self.tab.delete("/api/cook", headers=self.auth)
        self.assertEqual(r.status, 200)
        self.assertTrue(await wait_for(lambda: got[-1] is None))
        r = await self.tab.post("/api/cook", json={"recipeId": "r1"}, headers=self.auth)
        v = (await r.json())["cook"]
        self.assertEqual((v["recipe"]["image"], v["recipe"]["totalMinutes"]), ("api/recipes/r1/image", 30))
        r = await self.tab._s.get(self.client.make_url("/api/recipes/r1/image?token=" + self.tok))
        self.assertEqual((r.status, await r.read()), (200, PNG))
        self.assertEqual((await self.say("I'm done cooking"))["reply"], "Okay, I closed the recipe.")
        self.assertEqual((await self.say("next step"))["reply"], "There's no recipe open. Ask me to pull one up first.")
        task.cancel()
        await ws.close()

    async def test_recipe_list_for_the_tile(self):
        await self.connect()
        r = await self.tab.get("/api/recipes?q=lemon", headers=self.auth)
        d = await r.json()
        self.assertTrue(d["available"])
        self.assertEqual([x["title"] for x in d["recipes"]], ["Grandma's Lemon Chicken Thighs", "Lemon Drizzle Cake"])
        # A recipe added in Forkast is found by voice without waiting for the next refresh.
        self.fake.recipes.append(recipe("r5", "Garden Minestrone", 6, [ing("Beans", 1, "can")], ["Simmer 30 minutes."]))
        self.assertEqual((await self.say("pull up the minestrone recipe"))["reply"],
                         "Here's Garden Minestrone: 1 ingredient and 1 step.")
