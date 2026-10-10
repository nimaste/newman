"""Cook mode: a recipe (from the Forkast connection) open on one wall tablet, worked through by voice or touch.

    GET    /api/recipes                the recipe list ({available, status, detail, recipes}); ?q= filters,
                                       ?refresh=1 reads it from Forkast again
    GET    /api/recipes/{id}/image     its picture, through the hub
    GET    /api/cook                   this tablet's open recipe (or null)
    POST   /api/cook {"recipeId"}      open one on this tablet
    PATCH  /api/cook {"step"?, "servings"?}  move to a step (0-based) / scale it
    DELETE /api/cook                   close it

Each account (a wall tablet) has its own cook mode, kept on the hub and pushed to that account's
sockets as {"type": "cook", "cook": View | null}; it closes itself after IDLE_S untouched. Voice
("pull up the pancakes", "next step", "how much flour", "double it", "close the recipe") arrives
through Kitchen.voice from the voice pipeline (voice/intents.py works out what was asked).

The amounts are worked out here once, for the screen and for speech: scaled from the recipe's
servings, exactly: a kitchen fraction only when the amount IS one (1½ cups, ⅓ tsp), otherwise the exact
decimal (0.6 cup, 1.02 lb, 105 g), with unit spellings normalised ("Tablespoon", "tbsp" -> "2 tablespoons").
"""
from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from aiohttp import web

from .integrations.base import CommandError
from .integrations.forkast import ForkastIntegration, NeedsSignIn
from .kinds import join_names

if TYPE_CHECKING:
    from .app import Newman

log = logging.getLogger(__name__)

IDLE_S = 3 * 3600        # cook mode closes after this long untouched
OFFER_S = 3 * 60         # "Want a 25 minute timer?" - a yes counts for this long
CHOICE_S = 2 * 60        # "Which one?" - an answer counts for this long
MAX_SERVINGS = 100
RECIPE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def forkast_of(h: "Newman") -> ForkastIntegration | None:
    return next((i for i in h.integrations.instances.values() if isinstance(i, ForkastIntegration)), None)

# -- units and amounts ----------------------------------------------------------------------------------

# canonical -> (short shown, singular said, plural said, rounding: "fraction" | "metric" | "size")
UNITS: dict[str, tuple[str, str, str, str]] = {
    "tsp": ("tsp", "teaspoon", "teaspoons", "fraction"),
    "tbsp": ("tbsp", "tablespoon", "tablespoons", "fraction"),
    "cup": ("", "cup", "cups", "fraction"),
    "fl oz": ("fl oz", "fluid ounce", "fluid ounces", "fraction"),
    "oz": ("oz", "ounce", "ounces", "fraction"),
    "lb": ("lb", "pound", "pounds", "fraction"),
    "pint": ("", "pint", "pints", "fraction"),
    "quart": ("", "quart", "quarts", "fraction"),
    "gallon": ("", "gallon", "gallons", "fraction"),
    "g": ("g", "gram", "grams", "metric"),
    "kg": ("kg", "kilogram", "kilograms", "metric"),
    "mg": ("mg", "milligram", "milligrams", "metric"),
    "ml": ("ml", "millilitre", "millilitres", "metric"),
    "l": ("L", "litre", "litres", "metric"),
    "cl": ("cl", "centilitre", "centilitres", "metric"),
    "clove": ("", "clove", "cloves", "fraction"),
    "can": ("", "can", "cans", "fraction"),
    "pinch": ("", "pinch", "pinches", "fraction"),
    "dash": ("", "dash", "dashes", "fraction"),
    "slice": ("", "slice", "slices", "fraction"),
    "piece": ("", "piece", "pieces", "fraction"),
    "stick": ("", "stick", "sticks", "fraction"),
    "sprig": ("", "sprig", "sprigs", "fraction"),
    "bunch": ("", "bunch", "bunches", "fraction"),
    "head": ("", "head", "heads", "fraction"),
    "package": ("", "package", "packages", "fraction"),
    "jar": ("", "jar", "jars", "fraction"),
    "bottle": ("", "bottle", "bottles", "fraction"),
    "handful": ("", "handful", "handfuls", "fraction"),
    "inch": ("", "inch", "inches", "fraction"),
    "large": ("", "large", "large", "size"),
    "medium": ("", "medium", "medium", "size"),
    "small": ("", "small", "small", "size"),
    "extra large": ("", "extra large", "extra large", "size"),
}
ALIASES = {
    "t": "tsp", "ts": "tsp", "tsp": "tsp", "tsps": "tsp", "tspn": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "tbsp": "tbsp", "tbsps": "tbsp", "tbs": "tbsp", "tbl": "tbsp", "tbls": "tbsp", "tblsp": "tbsp",
    "tablespoon": "tbsp", "tablespoons": "tbsp",
    "c": "cup", "cup": "cup", "cups": "cup",
    "fl oz": "fl oz", "fl. oz": "fl oz", "fluid ounce": "fl oz", "fluid ounces": "fl oz",
    "oz": "oz", "ozs": "oz", "ounce": "oz", "ounces": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "pt": "pint", "pint": "pint", "pints": "pint", "qt": "quart", "quart": "quart", "quarts": "quart",
    "gal": "gallon", "gallon": "gallon", "gallons": "gallon",
    "g": "g", "gr": "g", "grs": "g", "gram": "g", "grams": "g", "gramme": "g", "grammes": "g",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "mg": "mg", "milligram": "mg", "milligrams": "mg",
    "ml": "ml", "mls": "ml", "milliliter": "ml", "milliliters": "ml", "millilitre": "ml", "millilitres": "ml",
    "l": "l", "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "cl": "cl", "centiliter": "cl", "centiliters": "cl", "centilitre": "cl", "centilitres": "cl",
    "clove": "clove", "cloves": "clove", "can": "can", "cans": "can", "tin": "can", "tins": "can",
    "pinch": "pinch", "pinches": "pinch", "dash": "dash", "dashes": "dash", "slice": "slice", "slices": "slice",
    "piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece", "stick": "stick", "sticks": "stick",
    "sprig": "sprig", "sprigs": "sprig", "bunch": "bunch", "bunches": "bunch", "head": "head", "heads": "head",
    "package": "package", "packages": "package", "pkg": "package", "pkgs": "package", "packet": "package",
    "packets": "package", "jar": "jar", "jars": "jar", "bottle": "bottle", "bottles": "bottle",
    "handful": "handful", "handfuls": "handful", "inch": "inch", "inches": "inch",
    "large": "large", "lg": "large", "medium": "medium", "med": "medium", "small": "small", "sm": "small",
    "extra large": "extra large", "extra-large": "extra large", "xl": "extra large",
}

FRACTIONS = [(0.0, "", ""), (1 / 8, "⅛", "an eighth"), (1 / 4, "¼", "a quarter"), (1 / 3, "⅓", "a third"),
             (1 / 2, "½", "half"), (2 / 3, "⅔", "two thirds"), (3 / 4, "¾", "three quarters"), (1.0, "", "")]


def unit_key(unit: Any) -> str:
    """"Tablespoons" -> "tbsp"; "T" -> "tbsp" (a capital T is a tablespoon); unknown -> its own lowercase."""
    u = str(unit or "").strip().rstrip(".")
    if not u:
        return ""
    if u == "T":
        return "tbsp"
    low = re.sub(r"\s+", " ", u.lower())
    return ALIASES.get(low, low)


def _fraction(q: float) -> tuple[int, int] | None:
    """q -> (whole, index into FRACTIONS) only when q IS that fraction (0.333 counts as a third);
    anything else -> None and the amount is shown as its exact decimal. Never rounds to a nearby one."""
    q = round(q, 6)
    whole = int(q)
    rest = q - whole
    for i, (v, _glyph, _word) in enumerate(FRACTIONS):
        if abs(v - rest) < 0.005:
            return (whole + 1, 0) if i == len(FRACTIONS) - 1 else (whole, i)
    return None


def _metric(q: float) -> float:
    # The real maths (52.5 g x 2 = 105 g); only floating-point noise is trimmed, to two decimals.
    return round(q, 2)


def _num(v: float) -> str:
    return f"{v:g}"


def rounding(unit: Any) -> str:
    k = unit_key(unit)
    if k in UNITS:
        return UNITS[k][3]
    return "fraction"


def show_amount(q: float | None, unit: Any) -> str:
    """For the screen: "1½ cups", "⅓ tsp", "250 g", "2 large", "3 cloves" ("" without a quantity)."""
    k = unit_key(unit)
    if q is None:
        return UNITS[k][1] if k in UNITS else str(unit or "")
    info = UNITS.get(k)
    if info and info[3] == "metric":
        return f"{_num(_metric(q))} {info[0] or k}"
    fr = _fraction(q)
    n = (f"{fr[0] if fr[0] else ''}{FRACTIONS[fr[1]][1]}" or "0") if fr else _num(_metric(q))
    if not k:
        return n
    if not info:
        return f"{n} {unit}".strip()
    if info[0]:
        return f"{n} {info[0]}"
    many = _metric(q) > 1
    return f"{n} {info[2] if many else info[1]}"


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def spoken_name(name: str) -> str:
    return re.sub(r"\s*/\s*", " or ", str(name or "")).strip()


def say_amount(q: float | None, unit: Any, name: str, notes: str = "") -> str:
    """For speech: "2 tablespoons of soy sauce", "half a cup of sugar", "1 and a half pounds of chicken wings",
    "250 grams of flour", "2 large eggs", "salt, to taste"."""
    k = unit_key(unit)
    info = UNITS.get(k)
    name = spoken_name(name)
    if q is None:
        text = f"{name} {notes}".lower()
        lead = f"{_article(info[1])} {info[1]} of {name}" if info and info[3] != "size" else name
        if "taste" in text:
            return lead + ("" if "to taste" in name.lower() else ", to taste")
        if any(w in text for w in ("to top", "garnish", "for serving", "to serve", "optional")):
            return lead + ", as much as you like"
        return lead + ", as needed"
    if info and info[3] == "metric":
        v = _metric(q)
        return f"{_num(v)} {info[1] if v == 1 else info[2]} of {name}"
    fr = _fraction(q)
    if info and info[3] == "size":
        unit_said, unit_one, of = info[1], info[1], ""
    elif info:
        unit_said, unit_one, of = info[2], info[1], " of"
    elif k:
        unit_said = unit_one = str(unit).strip().lower()
        of = " of"
    else:
        unit_said = unit_one = ""
        of = ""
    if fr is None:
        # Not a kitchen fraction: say the exact number ("1.02 pounds of chicken wings").
        v = _metric(q)
        u = unit_one if v == 1 else unit_said
        return f"{_num(v)} {u}{of} {name}".replace("  ", " ") if u else f"{_num(v)} {name}"
    whole, i = fr
    frac_word = FRACTIONS[i][2]
    if whole == 0 and i == 0:
        return f"0 {unit_said}{of} {name}".replace("  ", " ") if unit_said else f"0 {name}"
    if whole == 0:
        target = (f"{unit_one}{of} {name}" if unit_one else name).strip()
        if frac_word == "half":
            return f"half {_article(target)} {target}"
        return f"{frac_word} of {_article(target)} {target}"
    n = str(whole) if not i else f"{whole} and a half" if frac_word == "half" else f"{whole} and {frac_word}"
    many = whole > 1 or i > 0
    u = unit_said if many else unit_one
    return f"{n} {u}{of} {name}".replace("  ", " ") if u else f"{n} {name}"


def show_servings(s: float) -> str:
    fr = _fraction(s)
    return (f"{fr[0] if fr[0] else ''}{FRACTIONS[fr[1]][1]}" or "0") if fr else _num(_metric(s))


# -- timers in the steps ----------------------------------------------------------------------------------

_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
              "thirty": 30, "forty": 40, "forty-five": 45, "sixty": 60, "ninety": 90}
_NUM = r"(\d+(?:\.\d+)?(?:\s*(?:½|1/2))?|½|half an?|forty-five|" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")"
_UNIT = r"(hours?|hrs?|minutes?|mins?|seconds?|secs?)"
_DURATION = re.compile(rf"\b{_NUM}(?:\s*(?:-|–|—|to|or)\s*{_NUM})?(?:\s|-)*{_UNIT}\b", re.I)
_UNIT_S = {"h": 3600, "m": 60, "s": 1}


def _amount(word: str) -> float:
    w = word.lower().strip()
    if w.startswith("half"):
        return 0.5
    if w in _NUM_WORDS:
        return float(_NUM_WORDS[w])
    half = "½" in w or "1/2" in w
    base = re.sub(r"(½|1/2)", "", w).strip()
    return (float(base) if base else 0.0) + (0.5 if half else 0.0)


def step_timer(text: str) -> dict[str, Any] | None:
    """The longest time a step mentions ("bake 25 minutes", "10 to 12 minutes", "1 hour 15 minutes"):
    {"seconds", "range": bool} - a range uses its upper end. None when it mentions no time."""
    found: list[tuple[int, int, float, bool, int]] = []  # (start, end, seconds, range, unit)
    for m in _DURATION.finditer(text or ""):
        unit = _UNIT_S[m.group(3)[0].lower()]
        lo, hi = _amount(m.group(1)), _amount(m.group(2)) if m.group(2) else None
        if m.group(1).lower() in ("a", "an") and hi is not None:
            continue
        secs = (hi if hi is not None else lo) * unit
        found.append((m.start(), m.end(), secs, hi is not None, unit))
    if not found:
        return None
    # "1 hour 15 minutes", "1 hour and 15 minutes": one time
    merged: list[tuple[float, bool]] = []
    i = 0
    while i < len(found):
        _, e, secs, rng, unit = found[i]
        while i + 1 < len(found) and re.fullmatch(r"\s*(and)?\s*", text[e:found[i + 1][0]]) and \
                found[i + 1][4] < unit:
            i += 1
            e, secs, rng, unit = found[i][1], secs + found[i][2], rng or found[i][3], found[i][4]
        merged.append((secs, rng))
        i += 1
    secs, rng = max(merged, key=lambda x: x[0])
    if not 1 <= secs <= 24 * 3600:
        return None
    return {"seconds": int(round(secs)), "range": rng}


_SPEAK = [(re.compile(r"°\s*F\b"), " degrees Fahrenheit"), (re.compile(r"°\s*C\b"), " degrees Celsius"),
          (re.compile(r"°"), " degrees"), (re.compile(r"\btbsps?\b", re.I), "tablespoons"),
          (re.compile(r"\btsps?\b", re.I), "teaspoons"), (re.compile(r"\blbs?\b", re.I), "pounds"),
          (re.compile(r"\boz\b", re.I), "ounces"), (re.compile(r"\bmins?\b", re.I), "minutes"),
          (re.compile(r"\bhrs?\b", re.I), "hours"), (re.compile(r"(\d)\s*(?:-|–)\s*(\d)"), r"\1 to \2"),
          (re.compile(r"\b(\d+)\s+1/2\b"), r"\1 and a half"), (re.compile(r"(\d+)\s*½"), r"\1 and a half"),
          (re.compile(r"\b1/2\b|½"), "half"), (re.compile(r"\b1/4\b|¼"), "a quarter"),
          (re.compile(r"\b3/4\b|¾"), "three quarters"), (re.compile(r"\b1/3\b|⅓"), "a third"),
          (re.compile(r"\b2/3\b|⅔"), "two thirds")]


def speakable(text: str) -> str:
    """A step as read out: "350°F" -> "350 degrees Fahrenheit", "2 tbsp" -> "2 tablespoons", "10-12" -> "10 to 12"."""
    t = str(text or "").strip()
    for rx, sub in _SPEAK:
        t = rx.sub(sub, t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if re.search(r"[.!?]$", t) else t + "."


# -- a recipe, as cook mode keeps it ----------------------------------------------------------------------

def spoken_title(title: str) -> str:
    """"Beef and Broccoli Recipe" -> "Beef and Broccoli"; "The Best Pancakes" -> "Best Pancakes"."""
    t = re.sub(r"\s+recipe$", "", str(title or "").strip(), flags=re.I)
    t = re.sub(r"^the\s+", "", t, flags=re.I)
    return t or str(title or "")


def recipe_json(raw: dict[str, Any]) -> dict[str, Any]:
    """Forkast's recipe -> what cook mode keeps."""
    ings = sorted([i for i in raw.get("ingredients") or [] if isinstance(i, dict)],
                  key=lambda i: (i.get("sortOrder") or 0))
    steps = sorted([s for s in raw.get("instructions") or [] if isinstance(s, dict)],
                   key=lambda s: (s.get("stepNumber") or 0))
    try:
        base = float(raw.get("baseServings") or 0) or None
    except (TypeError, ValueError):
        base = None

    def qty(v: Any) -> float | None:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None
    return {"id": str(raw.get("id")), "title": str(raw.get("title") or "Untitled"),
            "spoken": spoken_title(str(raw.get("title") or "")), "baseServings": base,
            "hasImage": bool(raw.get("imageUrl")), "prepMinutes": raw.get("prepTimeMinutes"),
            "cookMinutes": raw.get("cookTimeMinutes"),
            "ingredients": [{"name": str(i.get("name") or "").strip(), "quantity": qty(i.get("quantity")),
                             "unit": str(i.get("unit") or "").strip(), "notes": str(i.get("notes") or "").strip()}
                            for i in ings if str(i.get("name") or "").strip()],
            "steps": [str(s.get("text") or "").strip() for s in steps if str(s.get("text") or "").strip()]}


class Kitchen:
    """Every tablet's cook mode. `source()` is the recipe connection (or None); `push(uid, msg)` reaches that
    account's open walls."""

    def __init__(self, source: Callable[[], Any], push: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
                 clock: Callable[[], float] = time.time) -> None:
        self.source, self.push, self.clock = source, push, clock
        self.sessions: dict[str, dict[str, Any]] = {}
        self.choices: dict[str, dict[str, Any]] = {}

    # -- state ---------------------------------------------------------------------------------

    def session(self, uid: str) -> dict[str, Any] | None:
        s = self.sessions.get(uid)
        if s and self.clock() - s["at"] > IDLE_S:
            self.sessions.pop(uid, None)
            return None
        return s

    def factor(self, s: dict[str, Any]) -> float:
        base = s["recipe"]["baseServings"]
        return (s["servings"] / base) if base else 1.0

    def view(self, uid: str) -> dict[str, Any] | None:
        """What the wall draws."""
        s = self.session(uid)
        if not s:
            return None
        r, f = s["recipe"], self.factor(s)
        ings = []
        for i in r["ingredients"]:
            q = i["quantity"] * f if i["quantity"] is not None else None
            ings.append({"amount": show_amount(q, i["unit"]) if q is not None or i["unit"] else "",
                         "name": i["name"], "notes": i["notes"]})
        total = sum(int(m) for m in (r["prepMinutes"], r["cookMinutes"]) if isinstance(m, (int, float)))
        return {"recipe": {"id": r["id"], "title": r["title"], "image": f"api/recipes/{r['id']}/image" if r["hasImage"] else None,
                           "baseServings": r["baseServings"], "totalMinutes": total or None,
                           "prepMinutes": r["prepMinutes"], "cookMinutes": r["cookMinutes"]},
                "servings": s["servings"], "servingsText": show_servings(s["servings"]) if s["servings"] else "",
                "scaled": bool(r["baseServings"]) and abs(f - 1) > 1e-9, "factor": round(f, 3),
                "ingredients": ings, "steps": r["steps"], "step": s["step"], "read": s["read"],
                "closesIn": max(0, int(IDLE_S - (self.clock() - s["at"])))}

    def voice_json(self, uid: str) -> dict[str, Any] | None:
        """What the voice parser needs (intents.World.cook)."""
        s = self.session(uid)
        c = self.choices.get(uid)
        if c and self.clock() - c["at"] > CHOICE_S:
            self.choices.pop(uid, None)
            c = None
        if not s and not c:
            return None
        out: dict[str, Any] = {"choosing": c["recipes"] if c else None}
        if s:
            offer = s.get("offer")
            if offer and self.clock() - offer["at"] > OFFER_S:
                s["offer"] = offer = None
            out.update(title=s["recipe"]["spoken"], steps=len(s["recipe"]["steps"]), step=s["step"], read=s["read"],
                       offer=bool(offer), ingredients=[i["name"] for i in s["recipe"]["ingredients"]])
        return out

    async def _changed(self, uid: str) -> None:
        if self.push:
            await self.push(uid, {"type": "cook", "cook": self.view(uid)})

    def _src(self) -> Any:
        src = self.source()
        if src is None:
            raise CommandError("Recipes aren't set up - add the Forkast connection.")
        return src

    async def open(self, uid: str, rid: str) -> dict[str, Any]:
        src = self._src()
        try:
            raw = await src.recipe(rid)
        except NeedsSignIn as e:
            raise CommandError("Forkast needs signing in again on the hub.") from e
        r = recipe_json(raw)
        self.sessions[uid] = {"recipe": r, "servings": r["baseServings"] or 0, "step": 0, "read": False,
                              "offer": None, "at": self.clock()}
        self.choices.pop(uid, None)
        await self._changed(uid)
        return self.sessions[uid]

    async def close(self, uid: str) -> bool:
        had = self.sessions.pop(uid, None) is not None
        self.choices.pop(uid, None)
        await self._changed(uid)
        return had

    async def change(self, uid: str, step: int | None = None, servings: float | None = None) -> dict[str, Any] | None:
        """From the screen (touch): no speech, no timer offers."""
        s = self.session(uid)
        if not s:
            return None
        if step is not None:
            s["step"] = max(0, min(len(s["recipe"]["steps"]) - 1, int(step)))
            s["read"] = True
        if servings is not None and s["recipe"]["baseServings"]:
            s["servings"] = max(0.25, min(MAX_SERVINGS, float(servings)))
        s["offer"], s["at"] = None, self.clock()
        await self._changed(uid)
        return self.view(uid)

    # -- voice ----------------------------------------------------------------------------------

    async def voice(self, uid: str, req: dict[str, Any]) -> dict[str, Any]:
        """One spoken cook-mode request (intents.Intent("cook").value) -> {"reply", "timer"?: {"seconds", "label"}}."""
        do = req.get("do")
        try:
            if do in ("open", "choose"):
                return await self._voice_open(uid, req)
            if do == "close":
                had = await self.close(uid)
                return {"reply": "Okay, I closed the recipe." if had else "There's no recipe open."}
            s = self.session(uid)
            if not s:
                return {"reply": "There's no recipe open. Ask me to pull one up first."}
            s["at"] = self.clock()
            offer, s["offer"] = s.get("offer"), None
            if do == "timer":
                if not offer or self.clock() - offer["at"] > OFFER_S:
                    return {"reply": "Okay."}
                return {"timer": {"seconds": offer["seconds"], "label": offer["label"]}, "reply": ""}
            if do == "no_timer":
                return {"reply": "Okay, no timer."}
            return await self._voice_in_recipe(uid, s, req)
        except CommandError as e:
            return {"reply": str(e)}

    async def _voice_open(self, uid: str, req: dict[str, Any]) -> dict[str, Any]:
        from .voice.intents import match_recipes  # (pure matching, shared with the parser)
        src = self._src()
        if src.status == "pairing":
            return {"reply": "Forkast needs signing in again on the hub."}
        rid = req.get("id")
        if req.get("ids"):
            by = {r["id"]: r for r in src.summaries()}
            found = [by[i] for i in req["ids"] if i in by]
            if len(found) > 1:
                return self.ask_which(uid, found)
            rid = found[0]["id"] if found else None
        if not rid:
            words = list(req.get("words") or [])
            try:
                await src.refresh()  # a recipe added since the last look
            except NeedsSignIn:
                return {"reply": "Forkast needs signing in again on the hub."}
            found = match_recipes(words, src.summaries())
            if not found:
                said = " ".join(words)
                return {"reply": f"I couldn't find a recipe called {said}." if said else "Which recipe?"}
            if len(found) > 1:
                return self.ask_which(uid, found)
            rid = found[0]["id"]
        s = await self.open(uid, str(rid))
        r = s["recipe"]
        n_i, n_s = len(r["ingredients"]), len(r["steps"])
        bits = []
        if n_i:
            bits.append(f"{n_i} ingredient{'s' if n_i != 1 else ''}")
        if n_s:
            bits.append(f"{n_s} step{'s' if n_s != 1 else ''}")
        return {"reply": f"Here's {r['spoken']}" + (f": {join_names(bits)}." if bits else "."), "opened": r["id"]}

    def ask_which(self, uid: str, found: list[dict[str, Any]]) -> dict[str, Any]:
        found = found[:3]
        self.choices[uid] = {"recipes": [{"id": r["id"], "title": r["title"]} for r in found], "at": self.clock()}
        names = [spoken_title(r["title"]) for r in found]
        n = {2: "two", 3: "three"}.get(len(found), str(len(found)))
        return {"reply": f"I found {n}: {join_names(names, 'or')}. Which one?"}

    def _offer(self, s: dict[str, Any], n: int) -> str:
        t = step_timer(s["recipe"]["steps"][n])
        if not t:
            return ""
        from .voice.intents import say_duration
        s["offer"] = {"seconds": t["seconds"], "label": f"step {n + 1}", "at": self.clock()}
        dur = say_duration(t["seconds"], exact=True)
        return f" Want a timer for {dur}?" + (" That's the longer time." if t["range"] else "")

    async def _read(self, uid: str, s: dict[str, Any], n: int) -> dict[str, Any]:
        s["step"], s["read"] = n, True
        reply = f"Step {n + 1}: {speakable(s['recipe']['steps'][n])}" + self._offer(s, n)
        await self._changed(uid)
        return {"reply": reply}

    async def _voice_in_recipe(self, uid: str, s: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
        do = req.get("do")
        r = s["recipe"]
        steps = r["steps"]
        n = len(steps)
        if do in ("step", "next", "previous", "repeat", "first", "last") and not n:
            return {"reply": "This recipe has no steps."}
        if do == "step":
            k = int(req.get("step") or 0)
            if k < 1:
                return {"reply": f"The steps go from 1 to {n}."}
            if k > n:
                return {"reply": f"There {'is only one step' if n == 1 else f'are only {n} steps'}."}
            return await self._read(uid, s, k - 1)
        if do == "next":
            k = s["step"] + 1 if s["read"] else s["step"]
            if k >= n:
                return {"reply": "That was the last step."}
            return await self._read(uid, s, k)
        if do == "previous":
            if not s["read"] or s["step"] == 0:
                return {"reply": "This is the first step."}
            return await self._read(uid, s, s["step"] - 1)
        if do == "repeat":
            return await self._read(uid, s, s["step"])
        if do == "first":
            return await self._read(uid, s, 0)
        if do == "last":
            return await self._read(uid, s, n - 1)
        if do == "count":
            where = f" You're on step {s['step'] + 1}." if s["read"] else ""
            return {"reply": f"There {'is one step' if n == 1 else f'are {n} steps'}.{where}"}
        if do == "where":
            if not s["read"]:
                return {"reply": f"You haven't started yet. There {'is one step' if n == 1 else f'are {n} steps'}."}
            return {"reply": f"You're on step {s['step'] + 1} of {n}."}
        f = self.factor(s)
        scaled = abs(f - 1) > 1e-9
        lead = f"For {show_servings(s['servings'])} servings: " if scaled else ""
        if do == "ingredients":
            if not r["ingredients"]:
                return {"reply": "This recipe doesn't list its ingredients."}
            parts = [say_amount(i["quantity"] * f if i["quantity"] is not None else None, i["unit"], i["name"],
                                i["notes"]) for i in r["ingredients"]]
            return {"reply": lead + ("You need " if not lead else "you need ") + join_names(parts) + "."}
        if do == "how_much":
            idx = list(req.get("ingredients") or [])
            hits = [r["ingredients"][i] for i in idx if 0 <= i < len(r["ingredients"])]
            if not hits:
                said = " ".join(req.get("words") or [])
                return {"reply": f"I don't see {said} in this recipe." if said else "Which ingredient?"}
            parts = []
            for i in hits:
                p = say_amount(i["quantity"] * f if i["quantity"] is not None else None, i["unit"], i["name"], i["notes"])
                if i["notes"] and i["quantity"] is not None:
                    p += f", {i['notes']}"
                parts.append(p)
            text = join_names(parts) if len(parts) < 3 else "; ".join(parts[:-1]) + "; and " + parts[-1]
            return {"reply": lead + (text if lead else text[:1].upper() + text[1:]) + "."}
        if do == "servings_info":
            base = r["baseServings"]
            if not base:
                return {"reply": "This recipe doesn't say how many it serves."}
            if scaled:
                return {"reply": f"It's scaled to {show_servings(s['servings'])} servings. The recipe makes {show_servings(base)}."}
            return {"reply": f"It makes {show_servings(base)} servings."}
        if do == "scale":
            base = r["baseServings"]
            if not base:
                return {"reply": "This recipe doesn't say how many it serves, so I can't scale it."}
            if req.get("reset"):
                new = base
            elif req.get("factor"):
                new = base * float(req["factor"])
            else:
                new = float(req.get("servings") or 0)
            if not 0 < new <= MAX_SERVINGS:
                return {"reply": f"I can scale it from 1 to {MAX_SERVINGS} servings."}
            s["servings"] = new
            await self._changed(uid)
            ratio = new / base
            how = {2.0: "double", 0.5: "half", 3.0: "triple", 1.0: "", 4.0: "four times", 1.5: "one and a half times"}.get(
                round(ratio, 3))
            if abs(ratio - 1) < 1e-9:
                return {"reply": f"Okay, back to the original {show_servings(base)} servings."}
            return {"reply": f"Okay, {show_servings(new)} servings" + (f", {how} the recipe." if how else ".")}
        return {"reply": "Sorry, I didn't understand that."}


def add_recipe_routes(r: web.RouteTableDef, h: "Newman", *, body: Callable[..., Awaitable[dict[str, Any]]],
                      send_to_user: Callable[[str, dict[str, Any]], Awaitable[None]]) -> Kitchen:
    kitchen = Kitchen(lambda: forkast_of(h), send_to_user)

    def _err(status: int, msg: str) -> web.Response:
        return web.json_response({"error": msg}, status=status)

    @r.get("/api/recipes")
    async def recipes(req: web.Request) -> web.Response:
        f = forkast_of(h)
        if not f:
            return web.json_response({"available": False, "status": None, "detail": "", "recipes": []})
        if req.query.get("refresh") and f.client:
            try:
                await f.refresh()
            except CommandError:
                pass
        q = (req.query.get("q") or "").strip().lower()
        words = q.split()
        out = [{"id": x["id"], "title": x["title"], "image": f"api/recipes/{x['id']}/image" if x["hasImage"] else None,
                "totalMinutes": sum(int(m) for m in (x["prepMinutes"], x["cookMinutes"]) if isinstance(m, (int, float))) or None,
                "steps": x["steps"], "servings": x["baseServings"]}
               for x in f.summaries() if all(w in x["title"].lower() for w in words)]
        return web.json_response({"available": f.connected(), "status": f.status, "detail": f.detail, "recipes": out})

    @r.get("/api/recipes/{rid}/image")
    async def image(req: web.Request) -> web.StreamResponse:
        rid = req.match_info["rid"]
        f = forkast_of(h)
        if not f or not RECIPE_ID.match(rid):
            raise web.HTTPNotFound()
        got = await f.image(rid)
        if not got:
            raise web.HTTPNotFound()
        data, ctype = got
        return web.Response(body=data, content_type=ctype.split(";")[0],
                            headers={"Cache-Control": "private, max-age=86400"})

    @r.get("/api/cook")
    async def cook_get(req: web.Request) -> web.Response:
        return web.json_response({"cook": kitchen.view(req["user"]["id"])})

    @r.post("/api/cook")
    async def cook_open(req: web.Request) -> web.Response:
        rid = str((await body(req)).get("recipeId") or "")
        if not RECIPE_ID.match(rid):
            return _err(400, "Send recipeId")
        try:
            await kitchen.open(req["user"]["id"], rid)
        except CommandError as e:
            return _err(404 if "isn't in Forkast" in str(e) else 503, str(e))
        return web.json_response({"cook": kitchen.view(req["user"]["id"])})

    @r.patch("/api/cook")
    async def cook_change(req: web.Request) -> web.Response:
        data = await body(req)
        step, servings = data.get("step"), data.get("servings")
        if step is not None and (not isinstance(step, int) or isinstance(step, bool)):
            return _err(400, "step is a number (0 = the first)")
        if servings is not None and (not isinstance(servings, (int, float)) or isinstance(servings, bool) or servings <= 0):
            return _err(400, "servings is a number above 0")
        v = await kitchen.change(req["user"]["id"], step=step, servings=servings)
        if v is None:
            return _err(404, "No recipe is open")
        return web.json_response({"cook": v})

    @r.delete("/api/cook")
    async def cook_close(req: web.Request) -> web.Response:
        await kitchen.close(req["user"]["id"])
        return web.json_response({"cook": None})

    return kitchen
