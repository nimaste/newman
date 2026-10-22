"""What someone said to a wall tablet -> what Newman should do, and what to say back.

Pure functions only (no hub, no I/O): `parse(text, world)` turns a transcript into an Intent, and
the `say_*` helpers word the replies. Nothing here knows about particular device kinds - the verbs
come from each kind's command labels in kinds.py ("Turn on", "Lock", "Set temperature"…), the
states from its state labels, the readings from its properties, and the devices from their names,
areas, kind nouns and this tablet's dashboard "show as" names. Sensitive commands (kinds.py
`sensitive`) are refused unless the tablet allows them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from ..kinds import KINDS, Command, Kind, join_names, properties_for

SAFETY = "For safety, do that from the screen."
NOT_UNDERSTOOD = "Sorry, I didn't understand that."

# -- input ------------------------------------------------------------------------------------------


@dataclass
class Device:
    id: str
    name: str
    kind: str
    state: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    available: bool = True
    shown_as: str = ""  # this tablet's dashboard "show as" name

    @property
    def k(self) -> Kind:
        return KINDS[self.kind]

    @property
    def area(self) -> str:
        return str((self.attrs or {}).get("area") or "")

    @property
    def label(self) -> str:
        """The name to say: the tablet's "show as" name when it has one."""
        return self.shown_as or self.name


@dataclass
class World:
    """What the tablet may talk about."""
    devices: list[Device] = field(default_factory=list)
    people: list[dict[str, Any]] = field(default_factory=list)  # locations.person_json for this tablet
    pages: list[dict[str, Any]] = field(default_factory=list)   # [{"id", "name"}] of its dashboard
    timers: list[dict[str, Any]] = field(default_factory=list)  # its timers (timers.Timers.json), in the order set
    alarms: list[dict[str, Any]] = field(default_factory=list)  # its alarms (alarms.Alarms.json)
    allow_sensitive: bool = False
    music: Device | None = None  # this tablet's own music player (the music connection), if any
    music_offer: bool = False    # it asked "Did you mean …?" (a song it wasn't sure of) and waits for a yes
    # Cook mode (recipes.py): the recipe list [{"id", "title"}] when a recipe connection is set up (else None),
    # and this tablet's open recipe ({"title", "steps", "step", "read", "offer", "ingredients": [names],
    # "choosing": [{"id", "title"}] | None}), or None.
    recipes: list[dict[str, Any]] | None = None
    cook: dict[str, Any] | None = None
    # "Run Good Night": the automations [{"id", "name", "sensitive"}] (sensitive: one of its actions is a
    # kinds.py `sensitive` command), or None when this tablet can't run them by voice.
    automations: list[dict[str, Any]] | None = None


@dataclass
class Intent:
    """type: command | refuse | ask_state | ask_value | describe | where | today | time | date | show |
    timer_set | timer_query | timer_cancel | timer_list | timer_add / timer_take (seconds more / less) |
    timer_pause | timer_resume | timer_restart | timer_change (seconds = the new full time) - which timer: label
    and ref ({"ordinal": 2 = "the second timer", -1 = the last; "seconds": "the 10 minute timer"}) |
    snooze (seconds or None = the tablet's own) | another_minute (seconds, 60 unless said) |
    alarm_set (value = {"hour", "minute", "ampm": "am" | "pm" | "", "days": repeat days 0 = Sunday, "once":
    "today" | "tomorrow" | "d<0-6>" | "", "label"}) | alarm_list | alarm_cancel | alarm_off | alarm_on | alarm_skip |
    alarm_change (value = {"target": which alarm (alarm_target), "hour", "minute", "ampm"}; the others' value is
    that target, alarm_skip's with "once") | stop | ambiguous | unknown |
    music_play (music = {title, artist, album, playlist, query, kind, shuffle, random}) | music_control
    (command = the player's command) | music_what | music_answer (value = True / False: the answer to
    "Did you mean …?") | cook (value = what to do in cook mode: {"do": open |
    close | step | next | previous | repeat | first | last | count | where | ingredients | how_much | scale |
    servings_info | timer | no_timer, …}) | automation_run (value = the automation's id).

    `missing`: set when a named target wasn't found as said - {"slots": the kinds of name the request needs
    ("device", "room", "person", "automation", "recipe", "page"), "words": what was said for it, "pool":
    {(slot, ref)} the names that would fit, or None for any of those slots}. The pipeline then looks for
    the name speech-to-text most likely misheard (voice/names.py)."""
    type: str
    devices: list[Device] = field(default_factory=list)
    command: Command | None = None
    value: Any = None
    state: str | None = None        # ask_state: the state asked about
    prop: str | None = None         # ask_value: property id
    people: list[dict[str, Any]] = field(default_factory=list)
    show: str | None = None         # camera | cameras | page | person | home | weather | calendar | todo
    page: dict[str, Any] | None = None
    seconds: int | None = None      # timer_set
    label: str = ""                 # a timer's name ("pasta")
    all: bool = False
    via: str = ""                   # the word that picked the devices when it wasn't a name ("outside")
    reply: str = ""                 # set when the answer is known at parse time
    music: dict[str, Any] = field(default_factory=dict)  # music_play: what to look for
    missing: dict[str, Any] | None = None
    ref: dict[str, Any] = field(default_factory=dict)  # which timer, besides its label (see the types)

# -- words --------------------------------------------------------------------------------------------

UNITS = {"°C": "degrees", "°F": "degrees", "%": "percent", "km/h": "kilometres an hour", "min": "minutes",
         "h": "hours", "W": "watts", "kWh": "kilowatt hours", "mm": "millimetres", "cm": "centimetres",
         "m²": "square metres", "V": "volts", "MB": "megabytes", "days": "days", "s": "seconds"}

SMALL = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
         "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
        "ninety": 90}
CONTRACTIONS = {"what's": "what is", "where's": "where is", "how's": "how is", "it's": "it is", "who's": "who is",
                "what're": "what are", "where're": "where are", "that's": "that is", "there's": "there is",
                "let's": "let us", "i'd": "i would", "isn't": "is not", "aren't": "are not",
                "don't": "do not", "today's": "today", "how're": "how are", "when's": "when is"}
WAKE = ("hey newman", "hi newman", "ok newman", "okay newman", "newman", "hey new man", "hey")
POLITE_START = ("please", "can you", "could you", "would you", "will you", "can i get you to", "i want you to",
                "i'd like you to", "i would like you to", "go ahead and", "just", "and", "so", "um", "uh", "ok", "okay")
POLITE_END = ("please", "for me", "thanks", "thank you", "now", "right now")
STOPWORDS = {"the", "a", "an", "my", "our", "your", "of", "in", "on", "at", "to", "for", "please", "is", "are",
             "it", "its", "that", "this", "those", "these", "there", "some", "any", "and", "with", "from", "by",
             "room", "me", "us", "be", "up", "set", "current", "currently", "right", "now", "what", "how",
             "much", "level", "called", "named"}
ALL_WORDS = {"all", "every", "everything", "both", "each", "anything", "any", "everywhere"}
STOP_HEADS = {"stop", "quiet", "silence", "enough", "shush", "hush", "dismiss", "nevermind", "shut", "cancel",
              "mute", "ok", "okay", "thanks", "thank"}
STOP_FILL = {"it", "that", "the", "alarm", "alarms", "talking", "please", "be", "ok", "okay", "thanks", "thank",
             "you", "now", "all", "timer", "timers", "sound", "noise", "ringing", "beeping", "up", "never", "mind",
             "quiet", "is", "enough", "stop", "newman"}
SHOW_VERBS = (("show", "me"), ("show",), ("display",), ("bring", "up"), ("pull", "up"), ("go", "to"),
              ("switch", "to"), ("take", "me", "to"), ("let", "me", "see"), ("let", "us", "see"),
              ("can", "i", "see"), ("open", "up"), ("put", "up"), ("put", "on"), ("open",))
# "Open" is also a command ("open the garage"): it only means "show" when a camera, page or person follows.
SOFT_SHOW_VERBS = {("open",)}
# Other ways to say a command's verb (a command label's first word on the right).
VERB_SYNONYMS = {"switch": "turn", "power": "turn", "dim": "set", "brighten": "set", "change": "set",
                 "make": "set", "put": "set", "adjust": "set", "begin": "start", "continue": "resume",
                 "unpause": "resume", "shut": "close", "activate": "turn on", "deactivate": "turn off",
                 "kill": "turn off", "relock": "lock", "reboot": "restart", "ring": "sound"}
QUESTION_HEADS = {"is", "are", "was", "were", "did", "does", "do", "has", "have"}
WH_HEADS = {"what", "how", "tell", "check", "whats", "status", "which"}
TEMPERATURE_WORDS = {"warm", "cold", "hot", "cool", "chilly", "temp", "temperatures"}
FULL_VIEWS = {"weather": "weather", "forecast": "weather", "calendar": "calendar", "agenda": "calendar",
              "schedule": "calendar", "events": "calendar", "todo": "todo", "chores": "todo", "tasks": "todo"}
DURATION_UNITS = {"hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600, "minute": 60, "minutes": 60,
                  "min": 60, "mins": 60, "m": 60, "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1}
TIMER_WORDS = {"timer", "timers", "countdown"}


def words_to_numbers(tokens: list[str]) -> list[str]:
    """["twenty", "one", "and", "a", "half"] -> ["21.5"]; "nineteen point five" -> "19.5"."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        val = None
        if t in TENS:
            val, i = TENS[t], i + 1
            if i < len(tokens) and tokens[i] in SMALL and 0 < SMALL[tokens[i]] < 10:
                val, i = val + SMALL[tokens[i]], i + 1
        elif t in SMALL and t != "oh":
            val, i = SMALL[t], i + 1
            if i < len(tokens) and tokens[i] == "hundred":
                val, i = val * 100, i + 1
                if i < len(tokens) and tokens[i] == "and":
                    i += 1
                if i < len(tokens) and tokens[i] in TENS:
                    val, i = val + TENS[tokens[i]], i + 1
                    if i < len(tokens) and tokens[i] in SMALL and 0 < SMALL[tokens[i]] < 10:
                        val, i = val + SMALL[tokens[i]], i + 1
                elif i < len(tokens) and tokens[i] in SMALL:
                    val, i = val + SMALL[tokens[i]], i + 1
        else:
            if re.fullmatch(r"\d+(\.\d+)?", t) and out is not None:
                val, i = float(t) if "." in t else int(t), i + 1
            else:
                out.append(t)
                i += 1
                continue
        # "and a half", "point five"
        if tokens[i:i + 3] == ["and", "a", "half"]:
            val, i = val + 0.5, i + 3
        elif i + 1 < len(tokens) and tokens[i] == "point" and (tokens[i + 1] in SMALL or tokens[i + 1].isdigit()):
            d = SMALL.get(tokens[i + 1], tokens[i + 1])
            val, i = float(f"{val}.{d}"), i + 2
        out.append(_num_text(val))
    return out


def _num_text(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _is_num(t: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?", t))


def normalize(text: str) -> list[str]:
    t = (text or "").lower()
    t = t.replace("°", " degrees ").replace("%", " percent ").replace("’", "'").replace("`", "'")
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)
    t = re.sub(r"(\d)\s*(am|pm)\b", r"\1 \2", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    t = re.sub(r"[^a-z0-9.' ]+", " ", t)
    words = []
    for w in t.split():
        w = CONTRACTIONS.get(w, w)
        if w.endswith("'s") and w not in CONTRACTIONS:
            w = w[:-2]  # "dante's" (possessive) -> "dante"
        words += w.replace("'", "").split()
    words = words_to_numbers(words)
    # "to do" (a list) reads as one word
    joined = " ".join(words).replace("to do list", "todo").replace("to do", "todo").replace("to dos", "todo")
    return joined.split()


def _strip_phrases(tokens: list[str], phrases: tuple[str, ...], at_start: bool) -> list[str]:
    changed = True
    while changed and tokens:
        changed = False
        for p in phrases:
            pt = p.split()
            if at_start and tokens[:len(pt)] == pt and len(tokens) > len(pt):
                tokens, changed = tokens[len(pt):], True
                break
            if not at_start and tokens[-len(pt):] == pt and len(tokens) > len(pt):
                tokens, changed = tokens[:-len(pt)], True
                break
    return tokens


def clean(text: str) -> list[str]:
    """Normalised words, with the wake word and politeness taken off."""
    tokens = normalize(text)
    tokens = _strip_phrases(tokens, WAKE, True)
    tokens = _strip_phrases(tokens, POLITE_START, True)
    tokens = _strip_phrases(tokens, WAKE, True)
    return _strip_phrases(tokens, POLITE_END, False)


def singular(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith(("ches", "shes", "sses", "xes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def same_word(a: str, b: str) -> bool:
    """Equal, or close enough for a speech-to-text spelling ("Marlena" / "Marlina")."""
    if a == b or singular(a) == singular(b):
        return True
    if min(len(a), len(b)) < 4 or _is_num(a) or _is_num(b):
        return False
    return SequenceMatcher(None, singular(a), singular(b)).ratio() >= 0.8


def same_name(a: str, b: str) -> bool:
    """A person's name heard a little differently ("Marlene" for Marlina)."""
    if same_word(a, b):
        return True
    return len(a) >= 4 and len(b) >= 4 and a[:3] == b[:3] and SequenceMatcher(None, a, b).ratio() >= 0.7


# What speech-to-text makes of a cough, a chime or the room (Whisper's usual guesses for no speech).
NOISE = {"thank you", "thanks for watching", "thank you for watching", "you", "bye", "blank audio", "silence",
         "music", "applause", "laughter", "okay", "hmm", "um", "uh", "oh", "so", "the", "i", "a"}


def is_noise(text: str) -> bool:
    words = " ".join(normalize(re.sub(r"[\[\(][^\]\)]*[\]\)]", " ", text or "")))
    return not words or words in NOISE


def tokens_of(text: str) -> list[str]:
    return [w for w in normalize(text) if w not in ("the", "a", "an", "and", "of")]


def has_phrase(tokens: list[str], phrase: list[str]) -> int:
    """Index where `phrase` starts in tokens (word by word), or -1."""
    n = len(phrase)
    for i in range(len(tokens) - n + 1):
        if all(same_word(tokens[i + j], phrase[j]) for j in range(n)):
            return i
    return -1

# -- devices --------------------------------------------------------------------------------------------


def kind_words(k: Kind) -> set[str]:
    """Words that mean "one of these" for a kind: its noun, name, plural and spoken words."""
    out: set[str] = set()
    for text in (k.noun, k.name, k.plural, *k.spoken):
        out.update(w for w in tokens_of(text) if len(w) > 2)
    return out - {"device", "sensor"}


@dataclass
class Match:
    device: Device
    recall: float       # share of the spoken words this device explains
    precision: float    # share of its name the words cover
    named: bool         # a name / area word matched (not only "lights")
    via: str = ""       # a kind's spoken word that picked it ("outside")

    @property
    def score(self) -> float:
        return self.recall * 2 + self.precision + (0.2 if self.named else 0)


def join_compounds(words: list[str], name_words: list[str]) -> list[str]:
    """Speech-to-text splits or joins compound names: "back yard" for a camera called "Backyard"
    (seen live). Two spoken words that make one name word become that one word."""
    out: list[str] = []
    i = 0
    while i < len(words):
        if i + 1 < len(words) and not any(same_word(words[i], nw) for nw in name_words) and \
                any(same_word(words[i] + words[i + 1], nw) for nw in name_words):
            out.append(words[i] + words[i + 1])
            i += 2
            continue
        out.append(words[i])
        i += 1
    return out


def match_device(words: list[str], d: Device) -> Match:
    names = [tokens_of(n) for n in {d.name, d.shown_as} if n]
    area = tokens_of(d.area) if d.area else []
    kw = kind_words(d.k)
    spoken = {w for s in d.k.spoken for w in tokens_of(s)}
    words = join_compounds(words, [nw for n in names for nw in n])
    hit, named, via = 0, False, ""
    covered: list[set[int]] = [set() for _ in names]
    for w in words:
        found = False
        for ni, n in enumerate(names):
            for j, nw in enumerate(n):
                if same_word(w, nw):
                    covered[ni].add(j)
                    found = True
            # ...and the other way round: "backyard" said for a device called "Back Yard".
            for j in range(len(n) - 1):
                if not found and same_word(w, n[j] + n[j + 1]):
                    covered[ni].update((j, j + 1))
                    found = True
        if found or any(same_word(w, a) for a in area):
            named = True
        elif any(same_word(w, k) for k in kw):
            found = True
            if w in spoken or singular(w) in spoken:
                via = via or w
        else:
            continue
        hit += 1
    precision = max((len(c) / len(n) for c, n in zip(covered, names) if n), default=0.0)
    return Match(d, hit / len(words) if words else 0.0, precision, named, via)


def is_plural(words: list[str], devices: list[Device]) -> bool:
    if any(w in ALL_WORDS for w in words):
        return True
    plurals = {w for d in devices for w in tokens_of(d.k.plural)}
    nouns = {w for d in devices for w in tokens_of(d.k.noun)}
    return any(w in plurals and w not in nouns or (w.endswith("s") and singular(w) in nouns and w not in nouns)
               for w in words)


def pick(words: list[str], devices: list[Device]) -> tuple[list[Device], str, str]:
    """The devices `words` mean: (devices, why-not, via). why-not is "ambiguous" or "unknown" when
    none could be picked; devices then holds the candidates of an ambiguous choice."""
    words = [w for w in words if w not in STOPWORDS]
    everything = any(w in ALL_WORDS for w in words)
    words = [w for w in words if w not in ALL_WORDS]
    if not devices:
        return [], "unknown", ""
    if not words:
        if everything or len(devices) == 1:
            return devices, "", ""
        return devices, "ambiguous", ""
    matches = [m for m in (match_device(words, d) for d in devices) if m.recall > 0]
    if not matches:
        return [], "unknown", ""
    best_recall = max(m.recall for m in matches)
    if best_recall < 0.5:
        return [], "unknown", ""
    top = [m for m in matches if m.recall >= best_recall - 1e-9]
    named = [m for m in top if m.named]
    via = next((m.via for m in top if m.via), "")
    plural = everything or is_plural(words, [m.device for m in top])
    if not named:  # only kind words ("the lights", "outside")
        if plural or len(top) == 1:
            return [m.device for m in top], "", via
        return [m.device for m in top], "ambiguous", via
    if plural:  # "all the lights": every light, and the things named lights
        return [m.device for m in top], "", via
    best = max(m.precision for m in named)
    exact = [m for m in named if m.precision >= best - 1e-9]
    if len(exact) == 1:
        return [exact[0].device], "", via
    return [m.device for m in exact], "ambiguous", via


def which_one(devices: list[Device]) -> str:
    names = [d.label for d in devices[:3]]
    more = len(devices) - 3
    return f"Which one: {join_names(names, 'or')}{f' or {more} more' if more > 0 else ''}?"


def didnt_catch(words: list[str], what: str = "a device") -> str:
    shown = " ".join(w for w in words if w not in STOPWORDS and w not in ALL_WORDS)
    return f"Sorry, I didn't catch {what} called {shown}." if shown else NOT_UNDERSTOOD


def device_pool(devices: list[Device]) -> set[tuple[str, str]]:
    """The names that could pick one of `devices`: theirs, and their rooms' (names.Name slot, ref)."""
    return {("device", d.id) for d in devices} | {("room", d.area.lower()) for d in devices if d.area}


def missing(slots: tuple[str, ...], words: list[str], pool: set[tuple[str, str]] | None = None) -> dict[str, Any] | None:
    """Intent.missing: what was said for a name that wasn't found (None when nothing was said for it)."""
    said = [w for w in words if w not in STOPWORDS and w not in ALL_WORDS]
    return {"slots": slots, "words": said, "pool": pool} if said else None


def not_found(words: list[str], what: str, slots: tuple[str, ...], pool: set[tuple[str, str]] | None) -> Intent:
    """"Sorry, I didn't catch a device called …", noting what's missing for the pipeline's second look."""
    return Intent("unknown", reply=didnt_catch(words, what), missing=missing(slots, words, pool))

# -- numbers and durations ---------------------------------------------------------------------------


def parse_duration(tokens: list[str]) -> tuple[int | None, set[int]]:
    """Seconds in "10 minutes", "an hour and a half", "half an hour", "1 hour 30 minutes", "90 seconds",
    "a 10 minute …" -> (seconds or None, indexes of the words used)."""
    total, used = 0.0, set()
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "half" and i + 2 < len(tokens) and tokens[i + 1] in ("an", "a") and tokens[i + 2] in DURATION_UNITS:
            total += DURATION_UNITS[tokens[i + 2]] / 2
            used.update({i, i + 1, i + 2})
            i += 3
            continue
        amount = None
        if _is_num(t):
            amount = float(t)
        elif t in ("a", "an", "one") and i + 1 < len(tokens) and tokens[i + 1] in DURATION_UNITS and \
                not (i + 2 < len(tokens) and (tokens[i + 2] in TIMER_WORDS or _is_num(tokens[i + 2]))):
            # not "a second timer" / "a second 10 minute timer" (seen live: read as 10 min 1 s)
            amount = 1.0
        if amount is not None and i + 1 < len(tokens) and tokens[i + 1] in DURATION_UNITS:
            unit = DURATION_UNITS[tokens[i + 1]]
            used.update({i, i + 1})
            j = i + 2
            if tokens[j:j + 3] == ["and", "a", "half"]:
                amount += 0.5
                used.update({j, j + 1, j + 2})
                j += 3
            total += amount * unit
            if j < len(tokens) and tokens[j] == "and":
                used.add(j)
                j += 1
            i = j
            continue
        i += 1
    return (int(round(total)) if total > 0 else None), used


def say_duration(seconds: float, exact: bool = False) -> str:
    """"10 minutes", "1 hour and 5 minutes", "2 minutes and 10 seconds", "45 seconds"."""
    s = max(0, int(round(seconds)))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    if not exact and s >= 600:  # past 10 minutes, minutes are enough
        if sec >= 30:
            m += 1
            if m == 60:
                h, m = h + 1, 0
        sec = 0
    parts = [f"{n} {u}{'s' if n != 1 else ''}" for n, u in ((h, "hour"), (m, "minute"), (sec, "second")) if n]
    return join_names(parts) or "0 seconds"


def timer_name(t: dict[str, Any]) -> str:
    """"pasta timer" / "10 minute timer"."""
    if t.get("label"):
        return f"{t['label']} timer"
    mins = t["seconds"] / 60
    if mins >= 1 and float(mins).is_integer():
        if mins >= 60 and float(mins / 60).is_integer():
            return f"{int(mins // 60)} hour timer"
        return f"{int(mins)} minute timer"
    return f"{say_duration(t['seconds'], exact=True).replace('seconds', 'second').replace('minutes', 'minute')} timer"

# -- values -------------------------------------------------------------------------------------------------


def say_number(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return _num_text(round(f, 1))


def say_value(p: dict[str, Any]) -> str:
    """A property's value read out: "21 degrees", "80 percent", "heating"."""
    v = p.get("value")
    if p.get("type") == "number":
        unit = p.get("unit") or ""
        words = UNITS.get(unit, unit)
        return f"{say_number(v)} {words}".strip()
    if p.get("type") == "boolean":
        return "yes" if str(v).lower() in ("true", "1", "yes", "on") else "no"
    label = next((o["label"] for o in p.get("options") or [] if o["value"] == v), None)
    return (label or str(v).replace("_", " ")).lower()


def say_state(d: Device) -> str:
    k = d.k
    if not d.available:
        return "offline"
    if k.unit and d.state is not None:
        return f"{say_number(d.state)} {UNITS.get(k.unit, k.unit)}"
    return k.state_label(d.state).lower()


def the(d: Device) -> str:
    """"the Side Door" - names read better with "the" unless they're a person's or already have it."""
    n = d.label
    return n if n.lower().startswith(("the ", "my ", "our ")) or "'s " in n else f"the {n}"


def be(d: Device) -> str:
    """"is" / "are" for a name ("Kitchen Lights are", "Side Door is")."""
    last = (d.label.split() or [""])[-1].lower()
    return "are" if last.endswith("s") and not last.endswith("ss") and len(last) > 3 else "is"


def Cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def summary(d: Device) -> str:
    """"partly cloudy, 12 degrees, high today 15 degrees" - the state and the kind's key readings."""
    props = {p["id"]: p for p in properties_for(_EntityView(d))}
    ids = list(d.k.summary) or [p.id for p in d.k.properties[:2]]
    bits = []
    for pid in ids:
        p = props.get(pid)
        if p and p.get("value") is not None and p.get("attr"):
            bits.append(f"{p['label'].lower()} {say_value(p)}")
    state = say_state(d) if (d.k.states or d.k.unit) else ""
    return ", ".join(([state] if state else []) + bits)


class _EntityView:
    """Just enough of state.Entity for kinds.properties_for."""

    def __init__(self, d: Device) -> None:
        self.kind, self.state, self.attrs = d.kind, d.state, d.attrs or {}

# -- parsing ------------------------------------------------------------------------------------------------


def parse(text: str, world: World) -> Intent:
    words = clean(text)
    if not words:
        return Intent("unknown", reply=NOT_UNDERSTOOD)
    for step in (_clock, _cook, _music_answer, _stop, _ringing, _alarm, _music, _timer, _show, _where, _today,
                 _automation, _question,
                 _command):
        it = step(words, world)
        if it is not None:
            return it
    return Intent("unknown", reply=NOT_UNDERSTOOD)


# -- the time and the date (answered by the pipeline in the hub's own time zone) --------------------

CLOCK_LEAD = ("do you know", "do you have", "have you got", "you know", "tell me", "can you tell me", "could you tell me")
TIME_ASKS = {"what time is it", "what time it is", "what is the time", "whats the time", "what is the current time",
             "whats the current time", "the time", "the current time", "what time is it here", "the time please",
             "what is the time here", "what time do you have", "what time have you got", "what is time"}
DATE_ASKS = {"what is the date", "whats the date", "what is the date today", "whats the date today", "what is today date",
             "whats today date", "what is todays date", "whats todays date", "what day is it", "what day is it today",
             "what day is today", "what day it is", "what is the day", "what is the day today", "what date is it",
             "what date is today", "what date is it today", "what date it is", "the date", "today date", "todays date",
             "the date today", "what day of the week is it", "what day of the week is today", "what is todays day",
             "what is the date and day", "what is today date and day", "what is the day and date",
             "what is the day and the date", "what is the date and the day"}


def _clock(words: list[str], world: World) -> Intent | None:
    """"What time is it?" / "What's the date?" - whole-sentence matches only, so "how much time is left"
    (timers) and devices named with "time" or "date" fall through."""
    words = _strip_phrases(list(words), CLOCK_LEAD, True)
    if len(words) > 1 and words[-1] == "right":  # "… right now" (the "now" is already off)
        words = words[:-1]
    said = " ".join(words)
    if said in TIME_ASKS:
        return Intent("time")
    if said in DATE_ASKS:
        return Intent("date")
    return None


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def say_time(now: Any) -> str:
    """A local datetime -> "It's 4:52 PM." ("It's 4 PM." on the hour; noon and midnight by name)."""
    if now.minute == 0 and now.hour in (0, 12):
        return "It's midnight." if now.hour == 0 else "It's noon."
    h, ampm = now.hour % 12 or 12, "AM" if now.hour < 12 else "PM"
    return f"It's {h} {ampm}." if now.minute == 0 else f"It's {h}:{now.minute:02d} {ampm}."


def say_date(now: Any) -> str:
    """A local date -> "It's Tuesday, October 6th."."""
    return f"It's {now.strftime('%A')}, {now.strftime('%B')} {_ordinal(now.day)}."


def _stop(words: list[str], world: World) -> Intent | None:
    if words[0] in STOP_HEADS and all(w in STOP_FILL for w in words[1:]):
        if words[0] == "cancel" and len(words) > 1 and any(w in TIMER_WORDS for w in words):
            return None  # "cancel the timer": a timer intent
        if words[0] == "cancel" and any(w in ALARM_WORDS for w in words) and not _ringing_now(world):
            return None  # "cancel the alarm" with nothing ringing: an alarm intent
        return Intent("stop", label="timer" if any(w in TIMER_WORDS for w in words) else "")
    if words in (["never", "mind"], ["be", "quiet"], ["that", "is", "enough"], ["thats", "enough"]):
        return Intent("stop")
    return None


def _ringing_now(world: World) -> bool:
    return any(t.get("state") == "ringing" for t in world.timers) or \
        any(a.get("state") == "ringing" for a in world.alarms)


# -- snooze / another minute (something ringing) ----------------------------------------------------

SNOOZE_FILL = {"snooze", "it", "the", "alarm", "that", "for", "please", "a", "again", "me", "another", "more", "bit",
               "few", "little", "timer"}
MORE_FILL = {"give", "me", "us", "just", "another", "1", "more", "minute", "minutes", "a", "please", "i", "need",
             "want", "can", "have", "one", "few", "extra", "it", "then", "ok", "okay"}


def _ringing(words: list[str], world: World) -> Intent | None:
    """"Snooze" / "snooze for 5 minutes"; "another minute" / "one more minute" while something rings."""
    if words[0] == "snooze" and all(w in SNOOZE_FILL or w in DURATION_UNITS or _is_num(w) for w in words[1:]):
        return Intent("snooze", seconds=parse_duration(words)[0])
    if not _ringing_now(world) or any(w in TIMER_WORDS for w in words):
        return None
    if ("another" in words or "more" in words or "extra" in words) and \
            all(w in MORE_FILL or w in DURATION_UNITS or _is_num(w) for w in words) and \
            any(w in DURATION_UNITS for w in words):
        secs = parse_duration(words)[0]
        return Intent("another_minute", seconds=secs if secs and secs > 60 else 60)
    return None


# -- alarms -------------------------------------------------------------------------------------------

ALARM_WORDS = {"alarm", "alarms"}
DAY_WORDS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}
AMPM = ((["in", "the", "morning"], "am"), (["this", "morning"], "am"), (["morning"], "am"), (["mornings"], "am"),
        (["a", "m"], "am"), (["am"], "am"), (["in", "the", "afternoon"], "pm"), (["afternoon"], "pm"),
        (["in", "the", "evening"], "pm"), (["this", "evening"], "pm"), (["evening"], "pm"), (["evenings"], "pm"),
        (["at", "night"], "pm"), (["tonight"], "pm"), (["night"], "pm"), (["p", "m"], "pm"), (["pm"], "pm"))
ALARM_SKIP = {"a", "an", "the", "my", "our", "your", "alarm", "alarms", "set", "create", "make", "add", "new", "another",
              "for", "at", "on", "to", "me", "us", "up", "wake", "please", "cancel", "delete", "remove", "clear", "turn",
              "switch", "off", "skip", "change", "move", "push", "what", "which", "do", "i", "we", "have", "is",
              "are", "there", "any", "list", "show", "all", "every", "each", "daily", "everyday", "day", "days",
              "tomorrow", "today", "tonight", "morning", "mornings", "evening", "evenings", "afternoon", "night", "am",
              "pm", "m", "p", "oclock", "o", "clock", "in", "this", "and", "just", "only", "it", "its", "one", "next",
              "so", "that", "called", "named", "from", "weekday", "weekdays", "weekend", "weekends", "work", "noon",
              "midnight", "half", "quarter", "past", "snooze", "get", "rid", "of", "be", "will", "would", "you", "can",
              "could", "until", "by", "time", "times", "now", "again", "go", "goes", "disable", "enable", "when",
              "how", "many", "much", "set", "put", "start", "keep", "workdays", "workday", "school", "oh", "instead",
              "reset", "update", "make", "shift", "except", "but", "not", "dont", "never", "mind", "very", "early"}


def _clock_at(words: list[str], i: int) -> tuple[int, int, set[int]] | None:
    """A time said at words[i]: "7", "7 30", "6 oh 5", "7 oclock", "6.3" (6:30), "noon", "half past 6",
    "quarter past / to 7", "20 past 6" -> (hour as said, minute, indexes used)."""
    w = words[i]
    nxt = words[i + 1] if i + 1 < len(words) else ""
    if w == "noon":
        return 12, 0, {i}
    if w == "midnight":
        return 0, 0, {i}
    if w in ("half", "quarter") and nxt in ("past", "after", "to", "before") and i + 2 < len(words) and \
            words[i + 2].isdigit() and 1 <= int(words[i + 2]) <= 12:
        h = int(words[i + 2])
        m = 30 if w == "half" else 15
        if nxt in ("to", "before"):
            if w == "half":
                return None
            return (h - 1) or 12, 45, {i, i + 1, i + 2}
        return h, m, {i, i + 1, i + 2}
    if w.isdigit() and nxt in ("past", "after") and i + 2 < len(words) and words[i + 2].isdigit() and \
            1 <= int(w) < 60 and 1 <= int(words[i + 2]) <= 12:
        return int(words[i + 2]), int(w), {i, i + 1, i + 2}
    if re.fullmatch(r"\d{1,2}\.\d{1,2}", w):
        h, m = w.split(".")
        mi = int(m) * (10 if len(m) == 1 else 1)
        if int(h) <= 24 and mi < 60:
            return int(h), mi, {i}
        return None
    if not w.isdigit():
        return None
    if len(w) in (3, 4) and int(w[-2:]) < 60 and int(w[:-2]) <= 24:  # "730", "0630"
        if nxt not in DURATION_UNITS:
            return int(w[:-2]), int(w[-2:]), {i}
        return None
    h = int(w)
    if h > 24 or nxt in DURATION_UNITS or nxt in TIMER_WORDS:
        return None
    used = {i}
    m = 0
    j = i + 1
    if j < len(words) and words[j] == "oh" and j + 1 < len(words) and words[j + 1].isdigit() and int(words[j + 1]) < 10:
        m, used = int(words[j + 1]), used | {j, j + 1}
        j += 2
    elif j < len(words) and words[j].isdigit() and int(words[j]) < 60 and \
            not (j + 1 < len(words) and words[j + 1] in DURATION_UNITS):
        m, used = int(words[j]), used | {j}
        j += 1
    if j < len(words) and words[j] == "oclock":
        used.add(j)
    elif j + 1 < len(words) and words[j:j + 2] == ["o", "clock"]:
        used |= {j, j + 1}
    return h, m, used


def parse_clock(words: list[str], start: int = 0, stop: int | None = None) -> dict[str, Any] | None:
    """The first time of day in words[start:stop] -> {"hour" (0-23 when AM / PM is known, else 1-12 as
    said), "minute", "ampm": "am" | "pm" | "", "used"}; None when no time is said."""
    stop = len(words) if stop is None else stop
    for i in range(start, stop):
        got = _clock_at(words[:stop], i)
        if got is None:
            continue
        h, m, used = got
        ampm = ""
        if words[i] in ("noon", "midnight"):
            ampm = "pm" if words[i] == "noon" else "am"
        j = max(used) + 1
        for phrase, which in AMPM:
            if words[j:j + len(phrase)] == phrase:
                ampm, used = which, used | set(range(j, j + len(phrase)))
                break
        if not ampm:  # "tomorrow morning at 7", "every evening at 6"
            for k, w in enumerate(words[:stop]):
                if k not in used and w in ("morning", "mornings", "tonight", "evening", "evenings", "afternoon"):
                    ampm = "am" if w.startswith("morning") else "pm"
                    used = used | {k}
                    break
        if h > 12:
            ampm, h = "pm", h
        elif h == 0:
            ampm = "am"
        elif ampm == "pm":
            h = 12 if h == 12 else h + 12
        elif ampm == "am":
            h = 0 if h == 12 else h
        return {"hour": h, "minute": m, "ampm": ampm if ampm or h > 12 or h == 0 else "", "used": used}
    return None


def parse_days(words: list[str]) -> tuple[set[int], str, set[int]]:
    """(repeat days 0 = Sunday, one day "today" | "tomorrow" | "d<n>" (the next such day), indexes used):
    "on weekdays", "every Saturday", "on Saturdays", "every day", "daily", "weekends", "tomorrow", "on Friday"."""
    rep: set[int] = set()
    once = ""
    used: set[int] = set()
    singles: list[int] = []
    for i, w in enumerate(words):
        prev = words[i - 1] if i else ""
        if w in ("daily", "everyday") or (w in ("day", "morning", "night") and prev in ("every", "each")):
            rep, used = set(range(7)), used | {i, i - 1}
        elif w in ("weekdays", "weekday", "workdays", "workday") or (w == "days" and prev in ("work", "school")):
            rep |= {1, 2, 3, 4, 5}
            used |= {i}
        elif w in ("weekends", "weekend"):
            rep |= {0, 6}
            used |= {i}
        elif w in DAY_WORDS or (w.endswith("s") and w[:-1] in DAY_WORDS):
            d = DAY_WORDS[w if w in DAY_WORDS else w[:-1]]
            used.add(i)
            if w not in DAY_WORDS or prev in ("every", "each"):
                rep.add(d)
                if prev in ("every", "each"):
                    used.add(i - 1)
            else:
                singles.append(d)
        elif w == "tomorrow":
            once, used = "tomorrow", used | {i}
        elif w in ("today", "tonight"):
            once, used = "today", used | {i}
    if len(singles) > 1 or (singles and rep):
        rep |= set(singles)  # "Monday and Wednesday"
    elif singles:
        once = f"d{singles[0]}"
    used.discard(-1)
    return rep, once, used


def _alarm_label(words: list[str], used: set[int]) -> str:
    """The name in "a gym alarm", "an alarm called gym", "the gym alarm"."""
    for lead in ("called", "named"):
        if lead in words:
            i = words.index(lead)
            after = [w for j, w in enumerate(words[i + 1:], i + 1) if j not in used and w not in ALARM_SKIP
                     and not _is_num(w) and w not in DAY_WORDS]
            if after:
                return " ".join(after[:2])
    i = next((j for j, w in enumerate(words) if w in ALARM_WORDS), len(words))
    before = [w for j, w in enumerate(words[:i]) if j not in used and w not in ALARM_SKIP and not _is_num(w)
              and w not in DAY_WORDS and not (w.endswith("s") and w[:-1] in DAY_WORDS)]
    return " ".join(before[-2:])


def alarm_target(words: list[str]) -> dict[str, Any]:
    """Which alarm words name: {"hour", "minute", "ampm", "days", "once", "label", "all"} (each optional)."""
    clock = parse_clock(words)
    used = set(clock["used"]) if clock else set()
    rep, once, dused = parse_days(words)
    t: dict[str, Any] = {"label": _alarm_label(words, used | dused), "days": sorted(rep), "once": once,
                         "all": "alarms" in words or any(w in ALL_WORDS for w in words)}
    if clock:
        t.update(hour=clock["hour"], minute=clock["minute"], ampm=clock["ampm"])
    return t


def find_alarms(alarms: list[dict[str, Any]], target: dict[str, Any]) -> list[dict[str, Any]]:
    """The alarms a target (alarm_target) fits."""
    out = list(alarms)
    if target.get("label"):
        lw = target["label"].split()
        named = [a for a in out if a.get("label") and all(any(same_word(x, y) for y in a["label"].split()) for x in lw)]
        out = named
    if target.get("hour") is not None:
        h, m = target["hour"], target.get("minute") or 0
        same = (lambda a: a["hour"] == h) if target.get("ampm") or h > 12 or h == 0 else (lambda a: a["hour"] % 12 == h % 12)
        out = [a for a in out if same(a) and a["minute"] == m]
    if target.get("days"):
        want = set(target["days"])
        out = [a for a in out if a.get("days") and want <= set(a["days"])]
    return out


def ampm_answer(words: list[str]) -> str:
    """The answer to "6:30 in the morning or the evening?": "am", "pm" or ""."""
    for i in range(len(words)):
        for phrase, which in AMPM:
            if words[i:i + len(phrase)] == phrase:
                return which
    return ""


def pick_alarm(words: list[str], cands: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """The answer to "Which one: the gym alarm or the 7:00 AM alarm?"."""
    got = pick_answer(words, cands)
    if got:
        return got
    t = alarm_target(words)
    if t.get("hour") is None and not t["days"] and not t["label"]:
        return None
    return find_alarms(cands, t) or None


def alarm_called(a: dict[str, Any]) -> str:
    return f"the {a['label']} alarm" if a.get("label") else f"the {a['time']} alarm"


def which_alarm(cands: list[dict[str, Any]]) -> str:
    names = [alarm_called(a) for a in cands[:4]]
    if len(set(names)) < len(names):
        names = [f"the {k} one" for k in ("first", "second", "third", "fourth")[:len(names)]]
    return f"Which one: {join_names(names, 'or')}?"


def _alarm(words: list[str], world: World) -> Intent | None:
    has = any(w in ALARM_WORDS for w in words)
    wake = words[:2] in (["wake", "me"], ["wake", "us"]) or words[0] == "wakeup"
    if not has and not wake:
        return None
    head = words[0]
    ringing = any(a.get("state") == "ringing" for a in world.alarms)
    target = alarm_target(words)
    qualified = bool(target.get("hour") is not None or target["days"] or target["once"] or target["label"] or
                     "my" in words or "alarms" in words)
    off = head in ("turn", "switch", "shut") and "off" in words or head == "disable"
    on = head in ("turn", "switch") and "on" in words and "off" not in words or head == "enable"
    if (off or head in STOP_HEADS) and ringing and not (target.get("hour") is not None or target["label"]):
        return Intent("stop")
    if head in ("cancel", "delete", "remove", "clear", "erase") or words[:3] == ["get", "rid", "of"]:
        return Intent("alarm_cancel", value=target, all=target["all"])
    if off or on:
        if not qualified:
            return None  # "turn off the alarm": the security alarm (a device), not one of these
        return Intent("alarm_off" if off else "alarm_on", value=target, all=target["all"])
    if head == "skip":
        return Intent("alarm_skip", value=target)
    if head in ("change", "move", "push", "make", "set", "reset", "update", "shift") and "to" in words[1:]:
        k = len(words) - 1 - words[::-1].index("to")
        new = parse_clock(words, k + 1)
        before = alarm_target(words[:k])
        if new is not None and (before.get("hour") is not None or before["label"] or before["days"] or "my" in words[:k]
                                or words[1:3] in (["the", "alarm"], ["my", "alarm"])):
            return Intent("alarm_change", value={"target": before, "hour": new["hour"], "minute": new["minute"],
                                                 "ampm": new["ampm"]})
    asking = head in ("what", "which", "list", "show", "when", "how", "tell") or words[:3] in (["do", "i", "have"],
                                                                                           ["do", "we", "have"]) \
        or (head in QUESTION_HEADS and ("any" in words or "set" in words))
    if asking and not wake and ("alarms" in words or "my" in words or "set" in words or "have" in words or
                                "time" in words or head == "when"):
        return Intent("alarm_list")  # (not "what's the alarm": the security alarm's state)
    if wake or head in ("set", "create", "make", "add", "new", "put", "start") or (has and words[0] in ("alarm", "an", "a")):
        clock = parse_clock(words)
        if clock is None:
            return Intent("unknown", reply="What time? Say something like “set an alarm for 7”.")
        rep, once, dused = parse_days(words)
        label = _alarm_label(words, set(clock["used"]) | dused) if has else ""
        return Intent("alarm_set", value={"hour": clock["hour"], "minute": clock["minute"], "ampm": clock["ampm"],
                                          "days": sorted(rep), "once": once, "label": label})
    return None


# -- music (this tablet's own player) ---------------------------------------------------------------

MUSIC_NOUNS = {"music", "song", "songs", "track", "tune", "tunes"}
MUSIC_FILL = MUSIC_NOUNS | {"the", "this", "that", "it", "playing", "please", "now", "a", "current", "my", "our"}
VOLUME_FILL = {"set", "the", "volume", "to", "up", "down", "turn", "it", "a", "bit", "little", "louder", "quieter",
               "softer", "percent", "make", "lower", "raise", "increase", "decrease", "change", "put", "at", "of",
               "this", "please", "more", "much", "lot", "notch", "level"} | MUSIC_NOUNS
WHAT_FILL = {"what", "is", "playing", "this", "song", "the", "that", "music", "track", "called", "name", "of",
             "right", "now", "on", "who", "sings", "by", "tune", "artist", "it"}
NEXT = (["next"], ["skip"], ["skip", "ahead"], ["skip", "forward"], ["next", "one"], ["play", "the", "next"],
        ["play", "next"])
PREVIOUS = (["previous"], ["go", "back"], ["back"], ["last"], ["play", "the", "previous"], ["play", "the", "last"],
            ["play", "previous"], ["play", "that", "again"], ["start", "over"], ["restart"])
RANDOM = (["music"], ["some", "music"], ["something"], ["anything"], ["some", "tunes"], ["tunes"],
          ["random", "music"], ["random", "songs"], ["everything"], ["my", "music"], ["all", "music"])
# How a music request starts ("can you play" loses its "can you" with the other politeness). Speech-to-text
# often hears "play" as "played" / "plays" / "playing" (seen live: "Played Bed of Roses").
PLAY_HEARD = {"plays", "played", "playing"}
PLAY_LEADS = (["let", "us", "hear"], ["lets", "hear"], ["let", "me", "hear"], ["i", "want", "to", "hear"],
              ["i", "wanna", "hear"], ["i", "would", "like", "to", "hear"], ["put", "on"], ["listen", "to"],
              ["play"], ["shuffle"], *[[w] for w in sorted(PLAY_HEARD)])
# Words naming what kind of thing follows: a song ("play the song X") or the album / artist forms below.
KIND_LEADS = ((["a", "song", "called"], "song"), (["the", "song", "called"], "song"), (["song", "called"], "song"),
              (["the", "song"], "song"), (["song"], "song"), (["the", "track"], "song"), (["track"], "song"),
              (["something", "from", "the", "album"], "album"), (["songs", "from", "the", "album"], "album"),
              (["from", "the", "album"], "album"), (["from", "album"], "album"),
              (["the", "band"], "artist"), (["band"], "artist"), (["the", "group"], "artist"))


def _rest_is(words: list[str], fill: set[str]) -> bool:
    return all(w in fill for w in words)


def _music(words: list[str], world: World) -> Intent | None:
    m = world.music
    if m is None:
        return None
    cmd = m.k.command
    head, rest = words[0], words[1:]
    said_music = any(w in MUSIC_NOUNS for w in words)
    playing = m.state == "playing"

    # "what's playing", "what song is this", "who sings this"
    if (words[:2] == ["what", "is"] or words[:2] == ["what", "song"] or words[:2] in (["who", "is"], ["who", "sings"])
            or words[:3] == ["name", "that", "tune"]) and _rest_is(words, WHAT_FILL) and \
            (said_music or "playing" in words or "sings" in words or words[:3] == ["who", "is", "this"]):
        return Intent("music_what", devices=[m])

    # "show the lyrics", "lyrics", "what are the lyrics": the wall's player with the words
    if any(w in ("lyrics", "lyric") for w in words) and len(words) <= 8:
        if m.state not in ("playing", "paused"):
            return Intent("show", show="lyrics", reply="Nothing is playing.")
        return Intent("show", show="lyrics")

    def control(cid: str) -> Intent | None:
        c = cmd(cid)
        return Intent("music_control", devices=[m], command=c) if c else None

    # pause / resume / stop / next / previous
    if head == "pause" and _rest_is(rest, MUSIC_FILL) and (said_music or playing or not rest):
        return control("pause") if (said_music or playing) else None
    if head in ("resume", "unpause", "continue") and _rest_is(rest, MUSIC_FILL | {"playing"}):
        return control("play")
    if head == "stop" and rest and _rest_is(rest, MUSIC_FILL) and (said_music or "playing" in rest):
        return control("stop")
    for p in NEXT:
        if words[:len(p)] == p and _rest_is(words[len(p):], MUSIC_FILL | {"one"}):
            return control("next")
    for p in PREVIOUS:
        if words[:len(p)] == p and _rest_is(words[len(p):], MUSIC_FILL | {"one"}) and (said_music or len(p) > 1 or
                                                                                      m.state != "idle"):
            return control("previous")

    # volume: "volume up", "turn it down", "set the volume to 40", "louder"
    if ("volume" in words or head in ("louder", "quieter", "softer") or
            (head == "turn" and said_music) or words[:3] in (["turn", "it", "up"], ["turn", "it", "down"])) and \
            all(w in VOLUME_FILL or _is_num(w) for w in words):
        nums = [w for w in words if _is_num(w)]
        if nums:
            v = float(nums[0])
            if not 0 <= v <= 100:
                return Intent("unknown", reply="The volume goes from 0 to 100.")
            c = cmd("set_volume")
            return Intent("music_control", devices=[m], command=c, value=v) if c else None
        if any(w in ("up", "louder", "raise", "increase", "more") for w in words):
            return control("volume_up")
        if any(w in ("down", "quieter", "softer", "lower", "decrease") for w in words):
            return control("volume_down")
        return None

    # "play …", "shuffle …", "put on …" (and what speech-to-text makes of "play": "played …")
    verb = next((p for p in PLAY_LEADS if words[:len(p)] == p), None)
    if verb is None:
        return None
    shuffle = verb == ["shuffle"]
    rest = words[len(verb):]
    if verb[0] in PLAY_HEARD and (not rest or _rest_is(rest, MUSIC_FILL)):
        return None  # "playing", "played that": not a request
    if rest[-2:] == ["on", "shuffle"] or rest[-1:] == ["shuffled"]:
        rest, shuffle = rest[:-2] if rest[-1:] == ["shuffle"] else rest[:-1], True
    while rest[:1] in (["me"], ["us"], ["some"]) and len(rest) > 1:
        rest = rest[1:]
    # "the song X" (a song, not an album or artist of that name), "the band X", "from the album X"
    kind = ""
    for words_, k in KIND_LEADS:
        if rest[:len(words_)] == words_ and len(rest) > len(words_):
            rest = rest[len(words_):]
            if k == "song":
                kind = "song"
            else:
                rest = [k] + rest  # handled below like "album X" / "artist X"
            break
    if rest[:1] == ["by"] and len(rest) > 1:
        rest = ["artist"] + rest[1:]  # "play by Queen"
    if not rest or rest in (["it"], ["the", "music"], ["music", "again"]):
        if shuffle:
            return Intent("music_play", devices=[m], music={"random": True, "shuffle": True})
        return control("play") if verb == ["play"] else None
    if rest in RANDOM:
        if m.attrs.get("title") and m.state == "paused" and not shuffle:
            return control("play")
        return Intent("music_play", devices=[m], music={"random": True, "shuffle": True})
    # Something another device plays ("play the TV"): not music.
    others = [d for d in world.devices if d.id != m.id and d.k.command("play")]
    found, why, _ = pick(rest, others)
    if found and not why and all(match_device([w for w in rest if w not in STOPWORDS], d).recall >= 1 for d in found):
        return None
    if "playlist" in rest:
        name = [w for w in rest if w not in ("my", "the", "our", "playlist", "called", "named")]
        if name:
            return Intent("music_play", devices=[m], music={"playlist": " ".join(name), "shuffle": shuffle})
    album = rest[2:] if rest[:2] == ["the", "album"] else rest[1:] if rest[:1] == ["album"] else None
    if album is None and rest[-1:] == ["album"] and len(rest) > 1:
        album = [w for w in rest[:-1] if w not in ("the", "my")]
    if album:
        artist = ""
        if "by" in album[1:]:
            i = len(album) - 1 - album[::-1].index("by")
            album, artist = album[:i], " ".join(album[i + 1:])
        return Intent("music_play", devices=[m], music={"album": " ".join(album), "artist": artist, "shuffle": shuffle})
    for lead in (["music", "by"], ["songs", "by"], ["something", "by"], ["anything", "by"], ["stuff", "by"],
                 ["artist"], ["the", "artist"]):
        if rest[:len(lead)] == lead and len(rest) > len(lead):
            return Intent("music_play", devices=[m], music={"artist": " ".join(rest[len(lead):]), "shuffle": shuffle})
    if "by" in rest[1:-1]:
        i = len(rest) - 1 - rest[::-1].index("by")
        return Intent("music_play", devices=[m], music={"title": " ".join(rest[:i]), "artist": " ".join(rest[i + 1:]),
                                                        "shuffle": shuffle})
    if kind == "song":
        return Intent("music_play", devices=[m], music={"title": " ".join(rest), "artist": "", "kind": "song",
                                                        "shuffle": shuffle})
    return Intent("music_play", devices=[m], music={"query": " ".join(rest), "shuffle": shuffle})


def _music_answer(words: list[str], world: World) -> Intent | None:
    """"Did you mean Bed of Roses by Bon Jovi?" -> "yes" / "yes, play it" / "no"."""
    if not world.music_offer or world.music is None:
        return None
    said = [w for w in words if w not in ("play", "it", "that", "one", "please")] or words
    yn = _yes_no(said, world)
    return Intent("music_answer", devices=[world.music], value=yn == "yes") if yn else None


def say_music_what(d: Device) -> str:
    a = d.attrs or {}
    if not a.get("title") or d.state == "idle":
        return "Nothing is playing."
    by = f" by {a['artist']}" if a.get("artist") else ""
    return f"{'This is' if d.state == 'playing' else 'Paused on'} {a['title']}{by}."


# -- cooking (a recipe open on this tablet: recipes.py) ------------------------------------------------

COOK_FILL = {"the", "a", "an", "my", "our", "me", "us", "for", "of", "please", "that", "this", "up", "some", "to",
             "recipe", "recipes", "one", "again", "now", "it", "is"}
TITLE_SKIP = {"the", "a", "an", "and", "of", "my", "our", "with", "recipe", "recipes"}
OPEN_LEADS = (["show", "me"], ["show", "us"], ["show"], ["pull", "up"], ["bring", "up"], ["open", "up"], ["open"],
              ["display"], ["let", "us", "see"], ["let", "me", "see"], ["find"], ["get"], ["put", "up"], ["go", "to"],
              ["let", "us", "make"], ["let", "us", "cook"], ["i", "want", "to", "make"], ["i", "want", "to", "cook"],
              ["how", "do", "i", "make"], ["how", "do", "you", "make"], ["we", "are", "making"], ["cook"], ["make"])
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
            "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15}
# What speech-to-text writes for a small number said right after "step" ("step for" = step 4).
STEP_SOUNDS = {"for": 4, "four": 4, "to": 2, "too": 2, "two": 2, "won": 1, "tree": 3, "ate": 8, "sex": 6}
YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "absolutely", "definitely", "go", "start", "set",
       "do", "yah", "ya"}
YES_FILL = YES | {"it", "the", "a", "timer", "ahead", "thanks", "thank", "you", "that", "one", "would", "be", "great",
                  "good", "sounds", "lovely", "perfect", "of", "course", "start", "for", "me", "us", "yes"}
NO_FILL = {"no", "nope", "nah", "not", "do", "thanks", "thank", "you", "need", "it", "the", "a", "timer", "i", "we",
           "that", "is", "ok", "okay", "fine", "skip", "right", "now", "bother", "please", "necessary", "worry",
           "about", "dont", "im", "good", "all"}
CLOSE_HEADS = {"close", "exit", "quit", "hide", "dismiss", "stop", "end", "leave", "finish", "finished", "done"}
CLOSE_FILL = CLOSE_HEADS | {"the", "recipe", "recipes", "cook", "cooking", "mode", "it", "this", "that", "screen", "with",
                            "i", "am", "im", "we", "are", "all", "now", "please", "thanks", "thank", "you", "for", "so",
                            "out", "of", "were"}
NEXT_FILL = {"next", "step", "what", "is", "the", "do", "i", "we", "then", "after", "that", "ok", "okay", "go", "to",
             "on", "onto", "and", "read", "me", "now", "one", "so", "move", "please", "alright", "right", "got", "it",
             "done", "comes", "whats", "us", "tell", "give", "a", "all"}
PREV_FILL = {"previous", "back", "go", "step", "one", "a", "the", "what", "was", "read", "me", "please", "before",
             "that", "to", "again", "take", "us", "move"}
REPEAT_FILL = {"repeat", "that", "again", "say", "read", "it", "the", "step", "this", "current", "what", "was", "is",
               "please", "one", "more", "time", "can", "you", "come", "sorry", "pardon", "huh", "me", "i", "missed",
               "did", "not", "catch", "hear", "once"}
HOW_FILL = {"do", "does", "i", "we", "you", "need", "use", "of", "the", "is", "are", "there", "in", "it", "this",
            "recipe", "add", "put", "go", "goes", "for", "a", "an", "get", "have", "to", "into", "should", "take",
            "takes", "call", "calls", "want", "much", "many", "how", "ingredient", "ingredients", "say", "said"}
SCALE_WORDS = {"double": 2.0, "doubled": 2.0, "twice": 2.0, "triple": 3.0, "tripled": 3.0, "quadruple": 4.0,
               "half": 0.5, "halve": 0.5, "halved": 0.5}
SCALE_FILL = {"it", "the", "recipe", "make", "a", "batch", "this", "please", "do", "can", "you", "let", "us", "cut",
              "in", "amounts", "ingredients", "everything", "lets", "as", "much", "size", "portion", "go", "with",
              "scale", "by", "all", "of", "that", "just", "we", "want", "to", "i"}
RESET_WORDS = {"normal", "original", "reset", "default", "regular", "unscaled"}
RESET_FILL = RESET_WORDS | {"back", "to", "the", "amount", "amounts", "recipe", "servings", "size", "go", "make", "it",
                            "please", "set", "put", "scaling", "scale", "undo", "as", "written", "was"}
PEOPLE_WORDS = {"servings", "serving", "people", "portions", "portion", "persons", "person", "serves", "guests"}
SCALE_VERBS = {"make", "scale", "set", "change", "adjust", "do", "cook", "resize"}
SCALE_VERB_FILL = SCALE_VERBS | {"it", "the", "recipe", "this", "for", "to", "everything", "enough", "please", "up",
                                 "by", "can", "you", "let", "us", "number"}


def _title_words(title: str) -> list[str]:
    return [w for w in tokens_of(title) if w not in TITLE_SKIP]


def match_recipes(words: list[str], recipes: list[dict[str, Any]], whole: bool = False) -> list[dict[str, Any]]:
    """The recipes `words` name, best first: one when it's clear, several when it isn't ("chicken" for two
    chicken recipes), none when nothing fits. Fuzzy per word, so "sesame chicken" finds "The Sesame Chicken
    My Dad's Made 100,000 Times" and speech-to-text spellings still work. `whole`: every word must fit."""
    target = [w for w in words if w not in COOK_FILL and w not in TITLE_SKIP]
    if len(target) > 1 and target[-1] == "1":
        target = target[:-1]  # "the sesame one" (speech numbers: one -> 1)
    if not target:
        return []
    scored = []
    for r in recipes:
        tw = _title_words(str(r.get("title") or ""))
        if not tw:
            continue
        said = join_compounds(target, tw)
        hits = sum(1 for w in said if any(same_word(w, x) for x in tw))
        covered = sum(1 for x in tw if any(same_word(w, x) for w in said))
        scored.append((hits / len(said), covered / len(tw), r))
    if not scored:
        return []
    best = max(s[0] for s in scored)
    if best < (1.0 if whole else 0.6) - 1e-9:
        return []
    top = sorted([s for s in scored if s[0] >= best - 1e-9], key=lambda s: -s[1])
    exact = [s for s in top if s[1] >= 1 - 1e-9]
    if len(exact) == 1:
        return [exact[0][2]]
    return [s[2] for s in top]


def match_ingredients(words: list[str], names: list[str]) -> list[int]:
    """Indexes of the ingredients `words` name - every one that fits best ("sugar" -> both sugars)."""
    target = [w for w in words if w not in HOW_FILL]
    if not target:
        return []
    scored = []
    for i, n in enumerate(names):
        nw = [w for w in tokens_of(n.replace("/", " ")) if w not in ("to", "for", "top", "taste")]
        if not nw:
            continue
        said = join_compounds(target, nw)
        hits = sum(1 for w in said if any(same_word(w, x) for x in nw))
        scored.append((hits / len(said), i))
    best = max((s[0] for s in scored), default=0.0)
    if best < 0.5:
        return []
    return [i for s, i in scored if s >= best - 1e-9]


def _as_int(w: str) -> int | None:
    if w.isdigit():
        return int(w)
    m = re.fullmatch(r"(\d+)(st|nd|rd|th)", w)
    if m:
        return int(m.group(1))
    return ORDINALS.get(w)


def _step_number(words: list[str]) -> int | None:
    """"step 4", "step number four", "the 4th step", "the fourth step", "step for" (speech-to-text)."""
    for i, w in enumerate(words):
        if w not in ("step", "steps"):
            continue
        j = i + 1 + (1 if words[i + 1:i + 2] == ["number"] else 0)
        if j < len(words):
            n = _as_int(words[j])
            if n is None and j == len(words) - 1:
                n = STEP_SOUNDS.get(words[j])
            if n is not None:
                return n
        if i > 0 and words[i - 1] not in ("next", "last", "final", "previous"):
            n = _as_int(words[i - 1])
            if n is not None:
                return n
    return None


def _yes_no(words: list[str], world: World) -> str | None:
    if words in (["why", "not"], ["go", "for", "it"]):
        return "yes"
    if any(w in ("no", "nope", "nah", "not", "dont", "skip") for w in words):
        return "no" if all(w in NO_FILL for w in words) else None
    if words[0] in YES and all(w in YES_FILL for w in words):
        if set(words) <= {"ok", "okay", "thanks", "thank", "you"} and _ringing_now(world):
            return None  # "okay" to a ringing timer or alarm: stop it
        return "yes"
    return None


def _scale(words: list[str]) -> dict[str, Any] | None:
    if parse_duration(words)[0] or any(w in TIMER_WORDS for w in words):
        return None
    if any(w in RESET_WORDS for w in words) and all(w in RESET_FILL or w in PEOPLE_WORDS for w in words):
        return {"do": "scale", "reset": True}
    factors = [SCALE_WORDS[w] for w in words if w in SCALE_WORDS]
    if factors and all(w in SCALE_FILL or w in SCALE_WORDS for w in words):
        return {"do": "scale", "factor": factors[0]}
    nums = [float(w) for w in words if _is_num(w)]
    if len(nums) != 1:
        return None
    if any(w in PEOPLE_WORDS for w in words) or \
            all(w in SCALE_VERB_FILL or _is_num(w) for w in words) and (words[0] in SCALE_VERBS or words[0] in ("for", "enough")):
        return {"do": "scale", "servings": nums[0]}
    return None


def _cook(words: list[str], world: World) -> Intent | None:
    c = world.cook or {}
    if world.recipes is None and not c:
        return None
    open_ = bool(c.get("title"))

    def cook(do: str, **kw: Any) -> Intent:
        return Intent("cook", value={"do": do, **kw})

    # "Which one?" -> "the beef one", "the second one"
    if c.get("choosing"):
        pick_ = _choose(words, c["choosing"])
        if pick_ is not None:
            return cook("open", id=pick_["id"])
    # "Want a 25 minute timer?" -> "yes" / "no thanks"
    if c.get("offer"):
        yn = _yes_no(words, world)
        if yn:
            return cook("timer" if yn == "yes" else "no_timer")
    said_cook = any(w in ("recipe", "recipes", "cook", "cooking") for w in words)
    # close
    if (any(w in CLOSE_HEADS for w in words) and all(w in CLOSE_FILL for w in words) and
            (said_cook or (open_ and words in (["im", "done"], ["i", "am", "done"], ["we", "are", "done"],
                                                ["all", "done"], ["were", "done"])))):
        return cook("close")
    # scaling: "double it", "make it for 8", "back to the original amounts"
    if open_:
        sc = _scale(words)
        if sc:
            return Intent("cook", value=sc)
    has_step = any(w in ("step", "steps") for w in words)
    head2 = words[:2]
    # how many steps / servings, how much of an ingredient
    if head2 in (["how", "many"], ["how", "much"]):
        rest = words[2:]
        if "steps" in rest or "step" in rest:
            return cook("count")
        if any(w in PEOPLE_WORDS or w in ("serve", "feed", "feeds", "make", "makes") for w in rest) and \
                (open_ or "recipe" in rest):
            return cook("servings_info")
        if open_:
            idx = match_ingredients(rest, c.get("ingredients") or [])
            target = [w for w in rest if w not in HOW_FILL]
            if idx:
                return cook("how_much", ingredients=idx, words=target)
            if target and not any(w in ("time", "left", "long", "remaining", "timer", "timers") for w in target) and \
                    pick(target, world.devices)[1] == "unknown":
                return cook("how_much", ingredients=[], words=target)
        return None
    # ingredients
    if any(w in ("ingredients", "ingredient") for w in words) or (open_ and words in (
            ["what", "do", "i", "need"], ["what", "do", "we", "need"], ["what", "goes", "in", "it"])):
        return cook("ingredients")
    # steps
    if has_step or open_:
        n = _step_number(words)
        if n is not None:
            return cook("step", step=n)
        if has_step and any(w in ("first", "beginning") for w in words):
            return cook("first")
        if has_step and any(w in ("last", "final") for w in words) and not any(w in ("back", "previous") for w in words):
            return cook("last")
        if words in (["start", "over"], ["from", "the", "top"], ["start", "from", "the", "beginning"],
                     ["from", "the", "beginning"], ["go", "to", "the", "beginning"]) and open_:
            return cook("first")
        if has_step and any(w in ("where", "which", "am", "are") for w in words) and "on" in words or \
                (open_ and words in (["where", "am", "i"], ["where", "are", "we"], ["where", "was", "i"])):
            return cook("where")
        if "next" in words and all(w in NEXT_FILL for w in words) or \
                (open_ and words in (["then", "what"], ["what", "now"], ["and", "then"], ["keep", "going"],
                                     ["continue"], ["move", "on"], ["go", "on"], ["what", "do", "i", "do", "now"],
                                     ["what", "do", "we", "do", "now"], ["what", "is", "after", "that"])):
            return cook("next")
        if (any(w in ("previous", "back") for w in words) and all(w in PREV_FILL for w in words)) or \
                words in (["what", "was", "before", "that"], ["step", "back"], ["the", "step", "before"]):
            return cook("previous")
        if (any(w in ("repeat", "again", "pardon") for w in words) or words in (
                ["what", "was", "that"], ["come", "again"], ["what", "did", "you", "say"], ["current", "step"],
                ["this", "step"], ["what", "is", "this", "step"], ["read", "the", "step"], ["read", "it"],
                ["what", "step"], ["sorry"])) and all(w in REPEAT_FILL for w in words):
            return cook("repeat")
        if has_step and (open_ or world.recipes is not None) and words[0] in ("what", "read", "tell", "go", "skip",
                                                                              "which", "say"):
            return cook("repeat")
    # "pull up the sesame chicken (recipe)"
    return _cook_open(words, world, said_cook)


def _choose(words: list[str], choices: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The answer to "Which one?": a name's words, or "the first / second / last one"."""
    found = match_recipes([w for w in words if w not in ("i", "mean", "meant", "want", "how", "about", "lets", "let",
                                                         "us", "do", "go", "with", "ok", "okay", "um", "uh", "oh")],
                          choices)
    if len(found) == 1:
        return found[0]
    for w in words:
        n = _as_int(w)
        if n is not None and 1 <= n <= len(choices) and all(x in COOK_FILL or x == "number" or _as_int(x) is not None
                                                            for x in words):
            return choices[n - 1]
        if w in ("last", "latter") and all(x in COOK_FILL | {w} for x in words):
            return choices[-1]
        if w == "former" and all(x in COOK_FILL | {w} for x in words):
            return choices[0]
    return None


def _cook_open(words: list[str], world: World, said_cook: bool) -> Intent | None:
    rest = None
    for lead in OPEN_LEADS:
        if words[:len(lead)] == lead:
            rest = words[len(lead):]
            break
    if rest is None:
        # "the sesame chicken recipe", "recipe for pancakes"
        if not said_cook or not any(w in ("recipe", "recipes") for w in words):
            return None
        rest = words
    if rest[:2] in (["the", "recipe"], ["a", "recipe"], ["recipe", "for"]) or rest[:1] == ["recipe"]:
        rest = rest[3:] if rest[2:3] == ["for"] else rest[2:] if rest[:2] == ["recipe", "for"] or rest[1:2] == ["recipe"] \
            else rest[1:]
    target = [w for w in rest if w not in COOK_FILL]
    said_recipe = any(w in ("recipe", "recipes") for w in words)
    if world.recipes is None:
        return Intent("cook", value={"do": "open", "words": target}) if said_recipe else None
    if not target:
        return Intent("unknown", reply="Which recipe?") if said_recipe else None
    found = match_recipes(target, world.recipes, whole=not said_recipe)
    if not said_recipe:
        if not found:
            return None
        # "show the front door": a camera, a page or a device that fits as well is meant instead
        if _best_page(target, world.pages) or pick(target, world.devices)[1] != "unknown":
            return None
    if len(found) == 1:
        return Intent("cook", value={"do": "open", "id": found[0]["id"]})
    if found:
        return Intent("cook", value={"do": "choose", "ids": [r["id"] for r in found[:3]]})
    return Intent("cook", value={"do": "open", "words": target},
                  missing=missing(("recipe",), target, {("recipe", str(r["id"])) for r in world.recipes}))


def _timer_label(words: list[str], used: set[int]) -> str:
    """The name in "a pasta timer", "a timer for the pasta", "a timer called pasta", "the eggs"."""
    skip = {"a", "an", "the", "my", "timer", "timers", "for", "set", "start", "make", "create", "add", "new",
            "another", "second", "on", "of", "and", "called", "named", "minute", "minutes", "hour", "hours",
            "second", "seconds", "put", "cancel", "stop", "delete", "remove", "clear", "end", "is", "left",
            "how", "much", "time", "long", "until", "till", "remaining", "what", "check", "status", "please",
            "there", "in", "with", "to", "go", "off", "turn", "get", "rid", "it", "s", "all", "every", "one",
            "countdown", "kill", "does", "do", "have", "has", "remain", "before", "done", "until", "the",
            "first", "third", "fourth", "fifth", "last", "1st", "2nd", "3rd", "4th", "5th", "extend", "by", "give",
            "more", "extra", "take", "knock", "subtract", "shave", "minus", "pause", "resume", "unpause", "continue",
            "restart", "reset", "again", "over", "back", "change", "adjust", "update", "be", "so", "that", "from",
            "hold", "both", "me", "us", "i", "we", "just", "instead", "now", "running", "up", "cut", "freeze"}
    i = next((j for j, w in enumerate(words) if w in TIMER_WORDS), len(words))
    before = [w for j, w in enumerate(words[:i]) if j not in used and w not in skip and not _is_num(w)]
    after = [w for j, w in enumerate(words[i + 1:], i + 1) if j not in used and w not in skip and not _is_num(w)]
    pick_ = before[-2:] if before else after[:2]
    return " ".join(pick_)


def _timer_ref(words: list[str]) -> tuple[dict[str, Any], set[int]]:
    """"the second timer" ({"ordinal": 2}; "the last timer": -1) and "the 10 minute timer" ({"seconds": 600})."""
    ref: dict[str, Any] = {}
    used: set[int] = set()
    for i, w in enumerate(words):
        if w not in TIMER_WORDS and w != "one":
            continue
        if i >= 1 and (words[i - 1] in ORDINALS or words[i - 1] == "last" or re.fullmatch(r"\d+(st|nd|rd|th)", words[i - 1])):
            ref["ordinal"] = -1 if words[i - 1] == "last" else _as_int(words[i - 1])
            used.add(i - 1)
        if i >= 2 and words[i - 1] in DURATION_UNITS and _is_num(words[i - 2]):
            ref["seconds"] = int(round(float(words[i - 2]) * DURATION_UNITS[words[i - 1]]))
            used |= {i - 2, i - 1}
    return ref, used


def find_timers(timers: list[dict[str, Any]], label: str = "", ref: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The timers a name ("pasta"), "the second timer" or "the 10 minute timer" fits (all of them when none is said)."""
    ref = ref or {}
    out = list(timers)
    if ref.get("ordinal"):
        n = ref["ordinal"]
        if n == -1:
            return out[-1:]
        return [out[n - 1]] if 1 <= n <= len(out) else []
    if ref.get("seconds"):
        out = [t for t in out if t["seconds"] == ref["seconds"]]
    if not label:
        return out
    lw = label.split()
    return [t for t in out if t.get("label") and all(any(same_word(a, b) for b in t["label"].split()) for a in lw)] \
        or [t for t in out if timer_name(t).startswith(label)]


def _find_timers(world: World, label: str) -> list[dict[str, Any]]:
    return find_timers(world.timers, label)


def _timer_edit(words: list[str], world: World, has_timer: bool) -> Intent | None:
    """Changing a timer that's there: more / less time, pause, resume, restart, a new length."""
    if not world.timers:
        return None
    head = words[0]
    ref, rused = _timer_ref(words)
    masked = [w if j not in rused else "_" for j, w in enumerate(words)]
    # "2 more minutes", "a few extra minutes": the amount without the "more"
    keep = [j for j, w in enumerate(masked) if not (w in ("more", "extra") and 0 < j < len(masked) - 1 and
                                                    masked[j + 1] in DURATION_UNITS)]
    amount, sub_used = parse_duration([masked[j] for j in keep])
    used = {keep[k] for k in sub_used} | rused
    label = _timer_label(words, used)

    def it(kind: str, seconds: int | None = None) -> Intent:
        return Intent(kind, seconds=seconds, label=label, ref=ref)
    # less time: "take 2 minutes off the timer", "knock a minute off the eggs", "remove a minute from the timer"
    if amount and (head in ("take", "knock", "subtract", "shave", "cut", "minus") and ("off" in words or "from" in words or
                                                                                       "away" in words)
                   or head in ("remove", "subtract") and "from" in words):
        return it("timer_take", amount)
    # more time: "extend the timer by 4 minutes", "add 4 minutes to the pasta timer", "give it 2 more minutes"
    article = any(w in ("a", "an", "another", "new") for w in words[1:next((j for j, w in enumerate(words) if w in TIMER_WORDS),
                                                                            len(words))])
    if amount and (head == "extend" or (head == "add" and ("to" in words or "more" in words or not has_timer)) or
                   (head in ("give", "put") and ("more" in words or "it" in words or "another" in words or "extra" in words)
                    and not article) or
                   (("more" in words or "extra" in words) and head not in ("set", "start", "make", "create", "new"))):
        if head == "add" and has_timer and article and "to" not in words:
            return None  # "add a timer for 5 minutes": a new one
        return it("timer_add", amount)
    if not has_timer:
        return None
    if head in ("pause", "hold", "freeze") or (head == "stop" and "for" in words and "now" in words):
        return it("timer_pause")
    if head in ("resume", "unpause", "continue", "unfreeze") or (head == "start" and "again" in words and not amount):
        return it("timer_resume")
    if head in ("restart", "reset") and not amount or (head == "start" and "over" in words):
        return it("timer_restart")
    if amount and (head in ("change", "adjust", "update", "reset") or
                   head in ("make", "set") and not article and (label or ref or "the" in words or "my" in words)):
        if amount > 24 * 3600:
            return Intent("unknown", reply="Timers can be up to 24 hours.")
        return it("timer_change", amount)
    return None


def _timer(words: list[str], world: World) -> Intent | None:
    has_timer = any(w in TIMER_WORDS for w in words)
    edit = _timer_edit(words, world, has_timer)
    if edit is not None:
        return edit
    seconds, used = parse_duration(words)
    head = words[0]
    if not has_timer:
        # "how much time is left" (no device named) is about the timers
        if {"time", "left"} <= set(words) or has_phrase(words, ["how", "long"]) >= 0 or "remaining" in words:
            rest = [w for w in words if w not in {"how", "much", "time", "is", "left", "long", "remaining", "there",
                                                  "until", "till", "done", "still", "have", "i", "we", "do", "does"}]
            if not [w for w in rest if w not in STOPWORDS]:
                return Intent("timer_query", reply="" if world.timers else "You don't have any timers.")
        return None
    ref, rused = _timer_ref(words)
    label = _timer_label(words, used | rused)
    if head in ("cancel", "stop", "delete", "remove", "clear", "end", "kill", "turn") or \
            words[:3] == ["get", "rid", "of"]:
        _, used2 = parse_duration([w if j not in rused else "_" for j, w in enumerate(words)])
        return Intent("timer_cancel", label=_timer_label(words, used2 | rused), ref=ref,
                      all=any(w in ALL_WORDS for w in words) or "timers" in words)
    if has_phrase(words, ["how", "long"]) >= 0 or any(w in ("remaining", "left", "status") for w in words) or \
            head in ("check",):
        _, used2 = parse_duration([w if j not in rused else "_" for j, w in enumerate(words)])
        return Intent("timer_query", label=_timer_label(words, used2 | rused), ref=ref)
    if head in ("list", "which") or has_phrase(words, ["what", "timers"]) >= 0 or \
            has_phrase(words, ["how", "many"]) >= 0 or (head in QUESTION_HEADS and "any" in words) or \
            (head in ("show",) and not seconds):
        return Intent("timer_list")
    if seconds:
        if seconds > 24 * 3600:
            return Intent("unknown", reply="Timers can be up to 24 hours.")
        return Intent("timer_set", seconds=seconds, label=_timer_label(words, used))
    if head in ("set", "start", "make", "create", "add", "put", "new"):
        return Intent("unknown", reply="How long? Say something like “set a timer for 10 minutes”.")
    return Intent("timer_query", label=label, ref=ref)


def timer_called(t: dict[str, Any]) -> str:
    """How a question names a timer: its name ("pasta"), else "the 10 minute timer"."""
    return t["label"] if t.get("label") else f"the {timer_name(t)}"


def which_timer(cands: list[dict[str, Any]]) -> str:
    """"Which one: pasta or eggs?" ("the first one or the second one" when they'd sound the same)."""
    names = [timer_called(t) for t in cands[:4]]
    if len(set(names)) < len(names):
        names = [f"the {k} one" for k in ("first", "second", "third", "fourth")[:len(names)]]
    return f"Which one: {join_names(names, 'or')}?"


def pick_answer(words: list[str], cands: list[dict[str, Any]], label_of=None) -> list[dict[str, Any]] | None:
    """The answer to "Which one: …?" ("pasta", "the eggs one", "the second one", "the 10 minute one",
    "both"): the timers or alarms it picks, or None when it isn't an answer."""
    if not words:
        return None
    if any(w in ("both", "all", "every", "each") for w in words) and len(words) <= 4:
        return list(cands)
    for w in words:
        n = -1 if w == "last" else _as_int(w) if (w in ORDINALS or re.fullmatch(r"\d+(st|nd|rd|th)", w)) else None
        if n:
            return [cands[n - 1 if n > 0 else -1]] if -1 <= n <= len(cands) and n != 0 else None
    label_of = label_of or (lambda t: t.get("label") or "")
    said = [w for w in words if w not in ("the", "one", "1", "a", "an", "my", "timer", "alarm", "please", "that", "it",
                                          "is", "for", "on", "just", "oh", "um", "uh")]
    if not said:
        return None
    hits = [c for c in cands if label_of(c) and all(any(same_word(a, b) for b in label_of(c).split()) for a in said)]
    if hits:
        return hits
    secs, _ = parse_duration(said)
    if secs:
        hits = [c for c in cands if c.get("seconds") == secs]
        return hits or None
    return None


def _show(words: list[str], world: World) -> Intent | None:
    rest, verb = None, None
    for v in SHOW_VERBS:
        if tuple(words[:len(v)]) == v:
            rest, verb = words[len(v):], v
            break
    if rest is None:
        return None
    if verb in SOFT_SHOW_VERBS:
        # "Open the garage door" is a command, unless a camera word is said ("open the doorbell camera").
        target = [w for w in rest if w not in STOPWORDS]
        cam_words = {"camera", "cameras", "cam", "feed", "video", "map", "page", "tab", "screen"}
        if not any(w in cam_words for w in target):
            # A command only when another device fits the words better than any camera does
            # (seen live: "open the front porch" lost to "Front Door" on the word "front").
            words = [w for w in target if w not in ALL_WORDS]
            def best(devs):
                ms = [match_device(words, d) for d in devs]
                return max((m.score for m in ms if m.recall > 0), default=0.0)
            if best([d for d in world.devices if not d.k.has_camera]) > best([d for d in world.devices if d.k.has_camera]):
                return None
        shown = _show(["show", *rest], world)
        return shown if shown and shown.type == "show" else None
    target = [w for w in rest if w not in STOPWORDS or w in ("on",)]
    target = [w for w in target if w not in ("on",)]
    if not target:
        return Intent("show", show="home")
    if target in (["home"], ["home", "page"], ["home", "screen"], ["main", "page"], ["start"]):
        return Intent("show", show="home")
    if all(w in ("all", "camera", "cameras", "every") for w in target) and \
            (len(target) > 1 or target[0] == "cameras"):
        return Intent("show", show="cameras")
    # A person: "show Dante on the map", "show me where Dante is"
    person, pwords = _person_in(target, world)
    if person is not None and all(w in pwords or w in ("map", "where", "location", "on", "is") for w in target):
        if not person.get("sharing"):
            return Intent("show", show="person", people=[person],
                          reply=f"I can't see where {person['name']} is.")
        return Intent("show", show="person", people=[person])
    if person is not None and "map" in target:
        return Intent("show", show="person", people=[person],
                      reply="" if person.get("sharing") else f"I can't see where {person['name']} is.")
    # A camera
    cams = [d for d in world.devices if d.k.has_camera and "camera" not in (d.attrs or {})]
    cams = cams or [d for d in world.devices if d.k.has_camera]
    found, why, _ = pick(target, cams)
    if found and not why:
        return Intent("show", show="camera", devices=found[:1])
    # A page of the tablet's dashboard
    page = _best_page(target, world.pages)
    if page:
        return Intent("show", show="page", page=page)
    if found and why == "ambiguous":
        return Intent("ambiguous", devices=found, reply=which_one(found))
    for w in target:
        if w in FULL_VIEWS:
            return Intent("show", show=FULL_VIEWS[w])
    return not_found(target, "a camera or page", ("device", "page"),
                     {("device", d.id) for d in cams} | {("page", str(p.get("id"))) for p in world.pages})


def _best_page(target: list[str], pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    words = [w for w in target if w not in ("page", "tab", "screen")]
    best, score = None, 0.0
    for p in pages:
        pw = tokens_of(p.get("name") or "")
        if not pw or not words:
            continue
        hits = sum(1 for w in words if any(same_word(w, x) for x in pw))
        s = hits / max(len(words), len(pw))
        if hits == len(words) and s > score:
            best, score = p, s
    return best


def _person_in(words: list[str], world: World) -> tuple[dict[str, Any] | None, set[str]]:
    for p in world.people:
        pw = tokens_of(p.get("name") or "")
        if pw and all(any(same_name(w, x) for w in words) for x in pw[:1]):
            return p, {w for w in words if any(same_name(w, x) for x in pw)}
    return None, set()


def _where(words: list[str], world: World) -> Intent | None:
    everyone = {"everyone", "everybody", "people", "family"}
    if words[:2] == ["who", "is"] and words[-1:] == ["home"]:
        return Intent("where", people=list(world.people), all=True)
    if words[:2] in (["where", "is"], ["where", "are"]) or (words[:1] == ["is"] and words[-1:] == ["home"]):
        target = [w for w in words[2 if words[0] == "where" else 1:] if w not in ("right", "now", "at", "home")]
        if any(w in everyone for w in target):
            return Intent("where", people=list(world.people), all=True)
        person, _ = _person_in(target, world)
        if person is not None:
            return Intent("where", people=[person])
        if words[0] == "where" and not any(same_word(w, x) for w in target for d in world.devices
                                           for x in tokens_of(d.name)):
            return not_found(target, "anyone", ("person",), {("person", str(p.get("id"))) for p in world.people})
    return None


TODAY = (["what", "is", "on", "today"], ["what", "is", "happening", "today"], ["what", "do", "i", "have", "today"],
         ["what", "do", "we", "have", "today"], ["what", "is", "on", "the", "calendar"], ["anything", "on", "today"],
         ["what", "is", "on", "my", "calendar"], ["what", "is", "on", "our", "calendar"], ["what", "is", "the", "plan"],
         ["what", "is", "today"], ["what", "is", "going", "on", "today"], ["whats", "on", "today"],
         ["what", "is", "up", "today"], ["what", "is", "on", "for", "today"], ["what", "is", "on", "the", "agenda"],
         ["what", "is", "planned", "today"], ["what", "is", "on", "the", "schedule"], ["anything", "today"],
         ["what", "is", "happening"], ["what", "is", "on", "tap", "today"], ["what", "do", "i", "need", "to", "do", "today"])


def _today(words: list[str], world: World) -> Intent | None:
    if any(words[:len(p)] == p for p in TODAY) or words in (["today"], ["agenda"], ["my", "day"]):
        return Intent("today")
    return None


# -- running an automation ("run Good Night") ----------------------------------------------------------

RUN_LEADS = (["run"], ["trigger"], ["start"], ["activate"], ["do"])
AUTO_WORDS = {"automation", "automations", "routine", "routines"}
AUTO_FILL = {"the", "my", "our", "a", "now", "please", "again", "for", "me", "us"}


def _automation(words: list[str], world: World) -> Intent | None:
    """"Run Good Night", "trigger the leaving automation", "start the movie night routine". "Run" and
    "trigger" always mean an automation; "start" / "activate" / "do" only with "automation" / "routine" said,
    or an automation's whole name (else "start the vacuum" is a device's)."""
    if world.automations is None:
        return None
    lead = next((p for p in RUN_LEADS if words[:len(p)] == p), None)
    if lead is None:
        return None
    rest = [w for w in words[len(lead):] if w not in AUTO_FILL]
    said_auto = any(w in AUTO_WORDS for w in rest)
    rest = [w for w in rest if w not in AUTO_WORDS]
    sure = lead[0] in ("run", "trigger") or said_auto
    if not rest:
        return Intent("unknown", reply="Which automation?") if sure else None
    items = [{"id": a["id"], "title": a["name"], "a": a} for a in world.automations if a.get("name")]
    found = [r["a"] for r in match_recipes(rest, items, whole=True)]
    if not sure and len(found) != 1:
        return None
    if len(found) == 1:
        a = found[0]
        if a.get("sensitive") and not world.allow_sensitive:
            return Intent("refuse", value=a["id"], label=a["name"], reply=SAFETY)
        return Intent("automation_run", value=a["id"], label=a["name"])
    if found:
        return Intent("ambiguous", reply=f"Which one: {join_names([a['name'] for a in found[:3]], 'or')}?")
    return not_found(rest, "an automation", ("automation",), {("automation", str(a["id"])) for a in world.automations})


def _state_words(devices: list[Device]) -> dict[str, list[tuple[Device, str]]]:
    """Spoken state word -> [(device, state id)] for every device whose kind has that state."""
    out: dict[str, list[tuple[Device, str]]] = {}
    for d in devices:
        for sid, label in d.k.states:
            for w in tokens_of(label.rstrip("!")):
                out.setdefault(w, []).append((d, sid))
    return out


def _question(words: list[str], world: World) -> Intent | None:
    head = words[0]
    if head in QUESTION_HEADS:
        return _ask_state(words[1:], world)
    if head in WH_HEADS or words[:2] in (["how", "is"], ["how", "are"]):
        return _ask_value(words, world)
    return None


def _ask_state(words: list[str], world: World) -> Intent | None:
    table = _state_words(world.devices)
    state_word = next((w for w in words if w in table), None) or \
        next((w for w in words for s in table if same_word(w, s) and len(w) > 3), None)
    if state_word is None:
        return None
    key = state_word if state_word in table else next(s for s in table if same_word(state_word, s))
    cands = table[key]
    devs = [d for d, _ in cands]
    target = [w for w in words if w != state_word and w not in ("still", "currently", "now", "not")]
    found, why, via = pick(target, devs)
    if why == "unknown":
        if any(w in ALL_WORDS or w in ("anything", "something", "everything", "nothing") for w in words):
            found, why = devs, ""
        else:
            return not_found(target, "a device", ("device", "room"), device_pool(devs))
    if why == "ambiguous" and not any(w in ALL_WORDS for w in words):
        return Intent("ambiguous", devices=found, reply=which_one(found))
    states = {d.id: s for d, s in cands}
    return Intent("ask_state", devices=found, state=states[found[0].id], via=via,
                  all=len(found) > 1 or any(w in ALL_WORDS for w in words))


def _ask_value(words: list[str], world: World) -> Intent | None:
    w2 = ["temperature" if w in TEMPERATURE_WORDS else w for w in words]
    question = {"what", "is", "are", "how", "tell", "me", "about", "check", "status", "the", "of", "whats", "which",
                "doing", "going", "currently", "it", "set", "to", "at", "on", "in", "s"}
    # Which property? the one whose label words the question uses most.
    best: tuple[int, int, str] | None = None
    for d in world.devices:
        for p in properties_for(_EntityView(d)):
            lw = [x for x in tokens_of(p["label"]) if x not in ("set",)]
            hits = sum(1 for x in lw if any(same_word(x, w) for w in w2))
            if hits and (best is None or (hits, -len(lw)) > (best[0], best[1])) and hits == len(lw):
                best = (hits, -len(lw), p["id"])
    if best:
        pid = best[2]
        label_words = set()
        for d in world.devices:
            for p in properties_for(_EntityView(d)):
                if p["id"] == pid:
                    label_words.update(tokens_of(p["label"]))
        devs = [d for d in world.devices if any(p["id"] == pid for p in properties_for(_EntityView(d)))]
        target = [w for w in w2 if w not in question and not any(same_word(w, x) for x in label_words)]
        if not target:
            return Intent("ask_value", devices=devs, prop=pid, all=True)
        found, why, via = pick(target, devs)
        if why == "unknown":
            return not_found(target, "a device", ("device", "room"), device_pool(devs))
        if why == "ambiguous":
            return Intent("ask_value", devices=found, prop=pid, all=True)
        return Intent("ask_value", devices=found, prop=pid, via=via)
    # No reading named: "what's the weather", "how's the dishwasher", "status of the vacuum"
    target = [w for w in w2 if w not in question]
    if not target:
        return None
    found, why, via = pick(target, world.devices)
    if why == "unknown":
        return not_found(target, "a device", ("device", "room"), device_pool(world.devices)) if words[0] != "how" else None
    if why == "ambiguous" and len(found) > 3:
        return Intent("ambiguous", devices=found, reply=which_one(found))
    return Intent("describe", devices=found, via=via)


def _command_verbs(world: World) -> dict[str, list[tuple[Kind, Command, list[str]]]]:
    """verb -> [(kind, command, qualifier words)] from the command labels of the kinds present."""
    out: dict[str, list[tuple[Kind, Command, list[str]]]] = {}
    seen = set()
    for d in world.devices:
        if d.kind in seen:
            continue
        seen.add(d.kind)
        for c in d.k.commands:
            label = re.sub(r"[()]", " ", c.label)
            lw = tokens_of(label)
            if lw:
                out.setdefault(lw[0], []).append((d.k, c, lw[1:]))
    return out


def _all_verbs() -> set[str]:
    """The first word of every command label of every kind (kinds.py)."""
    return {tokens_of(re.sub(r"[()]", " ", c.label))[0] for k in KINDS.values() for c in k.commands
            if tokens_of(c.label)}


def _value_of(words: list[str]) -> tuple[Any, str, set[int]]:
    """The value in "… to 21 degrees" / "… to 50 percent" / "… to heat" / "… at 30":
    (value, unit word, indexes used)."""
    for i, w in enumerate(words):
        if w in ("to", "at") and i + 1 < len(words):
            j = i + 1
            unit = ""
            if _is_num(words[j]):
                if j + 1 < len(words) and words[j + 1] in ("degrees", "degree", "percent", "c", "celsius"):
                    unit = words[j + 1]
                return float(words[j]), unit, {i, j} | ({j + 1} if unit else set())
            return " ".join(words[j:]), "", set(range(i, len(words)))
    # "dim the lights 30 percent", "set the thermostat 21"
    for i, w in enumerate(words):
        if _is_num(w) and i > 0:
            unit = words[i + 1] if i + 1 < len(words) and words[i + 1] in ("degrees", "percent") else ""
            return float(w), unit, {i} | ({i + 1} if unit else set())
    return None, "", set()


def _choose_value_command(cands: list[tuple[Command, int]], value: Any, unit: str, kind: Kind,
                          words: list[str]) -> tuple[Command | None, Any]:
    if value is None:
        plain = [c for c, q in cands if not c.takes_value]
        return (plain[0] if plain else None), None
    if isinstance(value, float):
        nums = [c for c, q in cands if c.value_type in ("number", "percent")]
        if unit in ("percent",):
            nums = [c for c in nums if c.value_type == "percent"] or nums
        elif unit in ("degrees", "degree", "c", "celsius"):
            nums = [c for c in nums if c.unit.startswith("°")] or nums
        named = [c for c, q in cands if q and c in nums]
        nums = named or sorted(nums, key=lambda c: c.value_type != "number")
        return (nums[0] if nums else None), value
    # a word: one of the kind's states ("heat")
    target = tokens_of(str(value))
    for c, q in cands:
        if c.value_type == "state":
            for sid, label in kind.states:
                if any(same_word(t, x) for t in target for x in tokens_of(label)):
                    return c, sid
    return None, value


def _command(words: list[str], world: World) -> Intent | None:
    words = list(words)
    # "turn the lights off" -> "turn off the lights"; "kitchen lights off" -> "turn off kitchen lights"
    if words[0] in ("turn", "switch", "power") and len(words) > 2 and words[-1] in ("on", "off") and words[1] not in ("on", "off"):
        words = [words[0], words[-1], *words[1:-1]]
    elif words[0] in ("shut", "power") and len(words) > 1 and words[1] in ("off", "down"):
        words = ["turn", "off", *words[2:]]
    elif words[-1] in ("on", "off") and words[0] not in ("turn", "switch", "power") and len(words) > 1:
        words = ["turn", words[-1], *words[:-1]]
    syn = VERB_SYNONYMS.get(words[0])
    if syn:
        words = syn.split() + words[1:]
    if words[0] == "turn" and len(words) > 1 and words[1] in ("on", "off"):
        pass
    verbs = _command_verbs(world)
    verb = words[0]
    entries = verbs.get(verb) or verbs.get(singular(verb))
    if not entries:
        # A verb some kind of device has ("turn on…") but nothing here does: say what wasn't found.
        if verb in _all_verbs() and len(words) > 1:
            rest = [w for w in words[1:] if w not in ("on", "off")]
            return Intent("unknown", reply=didnt_catch(rest))
        return None
    rest = words[1:]
    value, unit, vused = _value_of(rest)
    # qualifier words ("on", "away", "brightness") present in what was said
    present = [w for i, w in enumerate(rest) if i not in vused]
    kinds_ok = {k.id for k, _, _ in entries}
    devs = [d for d in world.devices if d.kind in kinds_ok]
    q_words = {x for _, _, q in entries for x in q}
    target = [w for w in present if w not in q_words or not any(w in tokens_of(d.name) for d in devs)]
    target = [w for w in target if w not in ("degrees", "degree", "percent", "brightness", "volume", "speed",
                                             "position", "temperature", "mode")]
    found, why, via = pick(target, devs)
    if why == "unknown":
        return not_found(target, "a device", ("device", "room"), device_pool(devs))
    if why == "ambiguous" and not target:
        found = found  # "turn on" alone: ask
    # Some words fit none of the devices picked ("the back yard lights": only "lights" did): a name may have
    # been misheard - noted for the pipeline's second look (which changes nothing unless the corrected
    # request then fits every word).
    said = [w for w in target if w not in STOPWORDS and w not in ALL_WORDS]
    miss = None
    if found and any(not any(match_device([w], d).recall > 0 for d in found) for w in said):
        miss = missing(("device", "room"), target, device_pool(devs))
    # The command for each device: the one whose qualifiers were all said, and that fits the value.
    chosen: list[tuple[Device, Command, Any]] = []
    for d in found:
        mine = [(c, q) for k, c, q in entries if k.id == d.kind]
        cands = []
        for c, q in mine:
            said = sum(1 for x in q if any(same_word(x, w) for w in present))
            if said == len(q):
                cands.append((c, said))
            elif value is not None and c.takes_value:
                cands.append((c, 0))
        plain = [c for c, q in mine if not c.takes_value]
        if not cands and value is None and len(plain) == 1:
            cands = [(plain[0], 0)]  # "start the vacuum" = its only "start…" command
        elif not cands and value is None and len(plain) > 1:
            return Intent("unknown", reply=f"Say which: {join_names([c.label.lower() for c in plain], 'or')}.")
        cands.sort(key=lambda cq: -cq[1])
        cmd, val = _choose_value_command(cands, value, unit, d.k, present)
        if cmd is None and value is None:
            # e.g. "turn on" for a kind whose turn-on needs the particle: nothing fits
            continue
        if cmd is not None:
            chosen.append((d, cmd, val))
    if not chosen:
        if found and value is not None:
            return Intent("unknown", reply=f"I can't set {the(found[0])} to {say_number(value) if isinstance(value, float) else value}.")
        return Intent("unknown", reply=didnt_catch(target) if not found else NOT_UNDERSTOOD)
    if why == "ambiguous":
        return Intent("ambiguous", devices=[d for d, _, _ in chosen], reply=which_one([d for d, _, _ in chosen]),
                      missing=miss)
    cmd = chosen[0][1]
    group = [(d, c, v) for d, c, v in chosen if c.id == cmd.id]
    if any(c.sensitive for _, c, _ in group) and not world.allow_sensitive:
        return Intent("refuse", devices=[d for d, _, _ in group], command=cmd, reply=SAFETY, missing=miss)
    val = group[0][2]
    if isinstance(val, float):
        lo, hi = cmd.min, cmd.max
        if (lo is not None and val < lo) or (hi is not None and val > hi):
            u = UNITS.get(cmd.unit, cmd.unit)
            return Intent("unknown", reply=f"{Cap(the(group[0][0]))} goes from {say_number(lo)} to {say_number(hi)} {u}.".replace("  ", " "))
    return Intent("command", devices=[d for d, _, _ in group], command=cmd, value=val, via=via, missing=miss)

# -- replies -----------------------------------------------------------------------------------------


def say_done(it: Intent) -> str:
    """"Okay, the Kitchen Lights are off." after a command was sent."""
    c, devs = it.command, it.devices
    names = join_names([d.label for d in devs[:3]]) + (f" and {len(devs) - 3} more" if len(devs) > 3 else "")
    target = names if len(devs) > 1 else the(devs[0])
    if it.value is not None and c is not None and c.takes_value:
        v = it.value
        if isinstance(v, float):
            v = f"{say_number(v)} {UNITS.get(c.unit, c.unit)}".strip()
        else:
            v = devs[0].k.state_label(str(v)).lower()
        return f"Okay, {target} {'are' if len(devs) > 1 else be(devs[0])} set to {v}."
    if c is not None and c.confirms_to:
        state = devs[0].k.state_label(c.confirms_to).lower()
        return f"Okay, {target} {'are' if len(devs) > 1 else be(devs[0])} {state}."
    return f"Okay, {c.past if c else 'done'}: {target}." if c else "Okay."


def say_state_answer(it: Intent) -> str:
    want = it.state
    devs = it.devices
    if not devs:
        return NOT_UNDERSTOOD
    k = devs[0].k
    want_label = k.state_label(want).lower()
    if len(devs) == 1 and not it.all:
        d = devs[0]
        if not d.available:
            return f"{Cap(the(d))} is offline."
        now = say_state(d)
        if d.state == want:
            return f"Yes, {the(d)} {be(d)} {now}."
        return f"No, {the(d)} {be(d)} {now}."
    yes = [d for d in devs if d.state == want and d.available]
    if not yes:
        return f"No, nothing is {want_label}." if len(devs) > 1 else f"No, {the(devs[0])} is {say_state(devs[0])}."
    if len(yes) == len(devs):
        return f"Yes, {'both' if len(devs) == 2 else 'all ' + str(len(devs))} are {want_label}."
    return f"Yes: {join_names([d.label for d in yes[:4]])} {'is' if len(yes) == 1 else 'are'} {want_label}."


def say_value_answer(it: Intent) -> str:
    out = []
    for d in it.devices:
        p = next((p for p in properties_for(_EntityView(d)) if p["id"] == it.prop), None)
        if p and p.get("value") is not None:
            out.append((d, p))
    if not out:
        return "I don't have that reading right now."
    if len(out) == 1:
        d, p = out[0]
        if it.via:
            return f"{Cap(it.via)}, the {p['label'].lower()} is {say_value(p)}."
        return f"{d.label}'s {p['label'].lower()} is {say_value(p)}."
    label = out[0][1]["label"]
    parts = [f"{d.label} {say_value(p)}" for d, p in out[:4]]
    return f"{label}: {join_names(parts)}."


def say_describe(it: Intent) -> str:
    parts = []
    for d in it.devices[:3]:
        s = summary(d)
        who = Cap(it.via) if it.via and len(it.devices) == 1 else d.label
        parts.append(f"{who}: {s}" if s else f"{who} is fine")
    return ". ".join(parts) + "."


def say_where(it: Intent) -> str:
    def one(p: dict[str, Any]) -> str | None:
        if not p.get("sharing"):
            return None
        if p.get("home"):
            return f"{p['name']} is home"
        if p.get("place"):
            return f"{p['name']} is at {p['place']}"
        dist = p.get("distanceM")
        if dist is None:
            return f"I don't know where {p['name']} is right now"
        km = dist / 1000
        far = f"{round(km)} kilometres" if km >= 2 else f"{round(km, 1):g} kilometres" if km >= 1 else f"{int(round(dist, -2)) or 100} metres"
        return f"{p['name']} is about {far} away"
    if not it.all:
        p = it.people[0]
        s = one(p)
        return (s + ".") if s else f"I can't see where {p['name']} is."
    said = [s for s in (one(p) for p in it.people) if s]
    if not said:
        return "I can't see where anyone is."
    return "; ".join(said) + "."


def say_today(today: dict[str, Any]) -> str:
    events = today.get("events") or []
    todo = today.get("todo") or []
    heads = today.get("headsUp") or []
    bits = []
    if events:
        ev = [f"{e['title']} {'all day' if e.get('allDay') else 'at ' + e['time']}" for e in events[:3]]
        more = f" and {len(events) - 3} more" if len(events) > 3 else ""
        bits.append(f"Today you have {join_names(ev)}{more}.")
    else:
        bits.append("Nothing on the calendar today.")
    if todo:
        names = [t["title"] for t in todo[:2]]
        bits.append(f"{len(todo)} thing{'s' if len(todo) != 1 else ''} to do: {join_names(names)}"
                    f"{' and more' if len(todo) > 2 else ''}.")
    if heads:
        bits.append(f"Heads up: {heads[0]['text']}.")
    return " ".join(bits)


def say_timers(timers: list[dict[str, Any]], now: float, count: bool = False) -> str:
    """Time left on each timer; with `count` ("how many timers…") it says how many first."""
    if not timers:
        return "You don't have any timers."
    parts = []
    for t in timers[:4]:
        if t.get("state") == "ringing":
            parts.append(f"your {timer_name(t)} is done")
        elif t.get("state") == "paused":
            parts.append(f"your {timer_name(t)} is paused with {say_duration(t.get('left') or 0)} left")
        else:
            parts.append(f"{say_duration(t['endsAt'] - now)} left on your {timer_name(t)}")
    s = join_names(parts)
    if count:
        n = len(timers)
        head = "You have one timer" if n == 1 else f"You have {n} timers"
        return f"{head}: {s}."
    return Cap(s) + "."
