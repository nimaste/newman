"""Every name Newman knows, and matching what speech-to-text heard against them.

Speech-to-text mishears names ("good nite" for Good Night, "ma seemo" for Massimo, "Better Roses" for Bed of
Roses), so a phrase is compared to a name three ways: word by word, letter by letter and by how it sounds (a
small consonant-sound key, Double-Metaphone style). Pure functions, plus the index:

    score(said, name) -> 0..1                    how well what was heard fits a name
    NameIndex                                    the hub's names by slot (device, room, person, automation,
                                                 recipe, page, artist, album, playlist, connection), each
                                                 source rebuilt only when it changed (voice/api.py wires it
                                                 to the hub's change events)
    correct(words, allowed, everything, skip)    the best name for a stretch of the words heard -> Fix | None

The music connection ranks its library with the same scoring (integrations/music_match.py); the voice
pipeline uses `correct` when a named target isn't found as said (pipeline.py), and the Whisper hint is built
from the index (voice/hint.py).
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

HIGH = 0.86      # at least this sure (and clearly ahead of the next one): use it
MEDIUM = 0.64    # at least this sure: ask first ("Did you mean …?")
CLEAR_LEAD = 0.05
EXACT = 0.97     # said as it is named (only the little words differ)
SAME_SOUND = 0.88  # a phrase that sounds the same, every consonant sound in order, but is spelt differently
# Little words that often vanish or appear in what's heard ("bed of roses" -> "better roses").
WEAK = {"of", "the", "a", "an", "and", "to", "in", "on", "my", "for", "at", "is", "it", "me", "you", "your", "i"}


def norm(text: str) -> str:
    """"Jump (2015 Remaster)" -> "jump"; "AC/DC" -> "ac dc"; "The Beatles" -> "beatles"."""
    t = (text or "").lower().replace("&", " and ").replace("’", "'")
    t = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", " ", t)               # (Remastered), [Live]
    t = re.sub(r"\s+-\s+(\d{4} )?(remaster|live|mono|stereo|single|radio).*$", " ", t)
    t = re.sub(r"[^a-z0-9' ]+", " ", t).replace("'", "")
    words = [w for w in t.split() if w]
    if words[:1] == ["the"] and len(words) > 1:
        words = words[1:]
    return " ".join(words)


def content(words: list[str]) -> list[str]:
    """The words that carry the name (all of them when every word is a little one)."""
    out = [w for w in words if w not in WEAK]
    return out or words


# -- how it sounds -------------------------------------------------------------------------------------

_DIGRAPHS = (("tch", "X"), ("sch", "SK"), ("ph", "F"), ("gh", ""), ("ck", "K"), ("sh", "X"), ("ch", "X"),
             ("th", "0"), ("wr", "R"), ("kn", "N"), ("wh", "W"), ("qu", "KW"), ("dg", "J"), ("mb", "M"))
_CLASS = {"b": "P", "p": "P", "f": "F", "v": "F", "d": "T", "t": "T", "g": "K", "k": "K", "q": "K", "j": "J",
          "s": "S", "z": "S", "x": "KS", "m": "N", "n": "N", "l": "L", "r": "R"}


@lru_cache(maxsize=8192)
def sound_word(w: str) -> str:
    """A word's consonant sounds: "better" -> "PTR", "bed" -> "PT", "roses" -> "RSS"→"RS", "phone" ->
    "FN". Vowels go (but a leading one is kept as "A"); like sounds share a letter (b/p, d/t, f/v, s/z,
    m/n, g/k/c-hard); doubled sounds count once."""
    w = re.sub(r"[^a-z]", "", w.lower())
    if not w:
        return ""
    out = "A" if w[0] in "aeiouy" else ""
    i = 0
    while i < len(w):
        for dg, code in _DIGRAPHS:
            if w.startswith(dg, i):
                out += code
                i += len(dg)
                break
        else:
            c = w[i]
            if c == "c":
                out += "S" if w[i + 1:i + 2] in ("e", "i", "y") else "K"
            elif c in _CLASS:
                out += _CLASS[c]
            # vowels, h, w, y: nothing
            i += 1
    return re.sub(r"(.)\1+", r"\1", out)


def sound(text: str) -> str:
    """The whole phrase's sound key, words run together (so "bed of" and "better" can line up), the little
    words left out ("of" is the first thing to get lost)."""
    return re.sub(r"(.)\1+", r"\1", "".join(sound_word(w) for w in content(text.split())))


# -- comparing ------------------------------------------------------------------------------------------

def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


@lru_cache(maxsize=16384)
def _word_sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    r = _ratio(a, b)
    sa, sb = sound_word(a), sound_word(b)
    if sa and sa == sb and min(len(a), len(b)) >= 3:
        r = max(r, 0.85)
    return r


def _token_sim(aw: list[str], bw: list[str]) -> float:
    """Each spoken word against its best name word, and each name word against its best spoken word."""
    if not aw or not bw:
        return 0.0
    there = sum(max(_word_sim(x, y) for y in bw) for x in aw) / len(aw)
    back = sum(max(_word_sim(y, x) for x in aw) for y in bw) / len(bw)
    return 2 * there * back / (there + back) if there + back else 0.0


@lru_cache(maxsize=8192)
def _prep(text: str) -> tuple[str, tuple[str, ...], tuple[str, ...], str, tuple[Counter, Counter, Counter]]:
    """(normalised, words, content words, sound key, letter counts of those three) - names are compared again
    and again, so kept."""
    n = norm(text)
    words = tuple(n.split())
    c = tuple(content(list(words)))
    s = sound(n)
    return n, words, c, s, (Counter(n), Counter(" ".join(c)), Counter(s))


def _quick(x: Counter, y: Counter) -> float:
    """difflib's quick_ratio from letter counts: an upper bound of the real ratio."""
    lx, ly = sum(x.values()), sum(y.values())
    if not lx or not ly:
        return 0.0
    if len(x) > len(y):
        x, y = y, x
    return 2 * sum(min(n, y.get(ch, 0)) for ch, n in x.items()) / (lx + ly)


def score(said: str, name: str, floor: float = 0.0) -> float:
    """0..1: how well what was heard fits a name. `floor`: below this, 0 will do (it saves the full
    comparison when a name can't get there - matching a phrase against every name in the house)."""
    a, aw, ac, sa, (ka, kac, ksa) = _prep(said)
    b, bw, bc, sb, (kb, kbc, ksb) = _prep(name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if ac == bc:
        return EXACT  # only the little words differ ("night at the opera" / "A Night at the Opera")
    if a.replace(" ", "") == b.replace(" ", ""):
        return 0.95  # split or joined differently ("back yard" / "Backyard")
    if floor > 0 and sa != sb and \
            0.3 * max(_quick(ka, kb), _quick(kac, kbc)) + 0.3 + 0.4 * _quick(ksa, ksb) < floor:
        return 0.0  # can't get there whatever the words do
    chars = max(_ratio(a, b), _ratio(" ".join(ac), " ".join(bc)))
    sounds = _ratio(sa, sb)
    if sa == sb and len(sa) >= 4:
        sounds = 1.0
    if min(len(sa), len(sb)) < 3:
        sounds *= 0.6  # one short word: sounds alike too easily
    if floor > 0 and sa != sb and 0.3 * chars + 0.3 + 0.4 * sounds < floor:
        return 0.0
    words = _token_sim(list(ac), list(bc))
    s = 0.3 * chars + 0.3 * words + 0.4 * sounds
    if sa == sb and len(sa) >= 4 and words >= 0.5:
        s = max(s, SAME_SOUND)  # every sound in the same order: what was said, misspelt ("better roses")
    return s


def band(top: float, rival: float = 0.0) -> str:
    """"high" (use it) | "medium" (ask first) | "low" (not found) for a best score and the next-best score of
    a different name."""
    if top >= HIGH and top - rival >= CLEAR_LEAD:
        return "high"
    if top >= MEDIUM:
        return "medium"
    return "low"


# -- the index ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Name:
    """One name Newman knows. slot: what it names - "device", "room", "person", "automation", "recipe",
    "page", "artist", "album", "playlist", "connection". ref: which one (an entity id, a person id…; a room's
    is its lower-case name). weight: how much it's used (the Whisper hint puts the most used first)."""
    text: str
    slot: str
    ref: str
    weight: float = 0.0


class NameIndex:
    """The hub's names, by source. A source is a function returning Names; it is rebuilt only when marked
    changed (`mark`, from the hub's change events) or, for sources given a `fingerprint` (a cheap value that
    changes when the names do), when that changes. Nothing here does network I/O: sources read what the hub
    already holds."""

    def __init__(self) -> None:
        self._build: dict[str, Callable[[], Iterable[Name]]] = {}
        self._print: dict[str, Callable[[], Any]] = {}
        self._entries: dict[str, list[Name]] = {}
        self._seen: dict[str, Any] = {}
        self._dirty: set[str] = set()
        self.builds: Counter[str] = Counter()  # rebuilds per source (tests, the hint's "changed?")
        self.listeners: list[Callable[[str], None]] = []  # told when a source is marked changed

    def source(self, key: str, build: Callable[[], Iterable[Name]], fingerprint: Callable[[], Any] | None = None) -> None:
        self._build[key] = build
        if fingerprint is not None:
            self._print[key] = fingerprint
        self._dirty.add(key)

    def mark(self, key: str | None = None) -> None:
        """Source `key` (None: every source) changed: rebuilt on the next lookup."""
        keys = list(self._build) if key is None else [key]
        self._dirty.update(k for k in keys if k in self._build)
        for k in keys:
            for listener in list(self.listeners):
                try:
                    listener(k)
                except Exception:  # noqa: BLE001 - a listener must never break a change event
                    log.exception("Name index listener failed")

    def _fresh(self, key: str) -> list[Name]:
        fp = None
        if key in self._print:
            try:
                fp = self._print[key]()
            except Exception:  # noqa: BLE001 - keep the last good names
                log.exception("Name index: %s fingerprint failed", key)
                fp = self._seen.get(key)
        if key in self._dirty or (key in self._print and fp != self._seen.get(key)) or key not in self._entries:
            try:
                entries = [n for n in self._build[key]() if n.text and n.text.strip()]
            except Exception:  # noqa: BLE001 - keep the last good names
                log.exception("Name index: %s failed", key)
                entries = self._entries.get(key, [])
            self._entries[key], self._seen[key] = entries, fp
            self._dirty.discard(key)
            self.builds[key] += 1
        return self._entries[key]

    def names(self, *slots: str) -> list[Name]:
        """Every name (or those of `slots`), each source up to date."""
        out: list[Name] = []
        for key in list(self._build):
            out += [n for n in self._fresh(key) if not slots or n.slot in slots]
        return out


# -- correcting what was heard ---------------------------------------------------------------------------

@dataclass
class Fix:
    """words[start:end] heard is most likely `name` (score 0..1; band "high" | "medium")."""
    start: int
    end: int
    name: Name
    score: float
    band: str


MAX_SPAN = 4  # words a name is heard as ("ma see mo")


def _spans(words: list[str], skip: set[str]) -> Iterable[tuple[int, int]]:
    for i in range(len(words)):
        if words[i] in skip:
            continue  # a stretch never starts with a kind word or a little word ("lights", "the")
        for j in range(i + 1, min(len(words), i + MAX_SPAN) + 1):
            yield i, j


def correct(words: list[str], allowed: list[Name], everything: list[Name] | None = None,
            skip: set[str] | None = None) -> Fix | None:
    """The name of `allowed` that a stretch of `words` most likely is, if fairly sure.

    `everything`: every name the hub knows - a stretch said exactly as some *other* name (one the request
    can't mean: "run the dishwasher" with a Dishwasher device) is left alone, never bent into an allowed one.
    `skip`: words that are not part of a name on their own (kind words, little words)."""
    skip = skip or set()
    if not words or not allowed:
        return None
    allowed_keys = {(n.slot, n.ref) for n in allowed}
    best: tuple[float, int, int, Name, float] | None = None  # (score, start, end, name, rival)
    for i, j in _spans(words, skip):
        if all(w in skip for w in words[i:j]):
            continue
        said = " ".join(words[i:j])
        if len(said.replace(" ", "")) < 3:
            continue
        ranked = sorted(((score(said, n.text, floor=MEDIUM - CLEAR_LEAD), n) for n in allowed), key=lambda x: -x[0])
        if not ranked or ranked[0][0] <= 0:
            continue
        top, name = ranked[0]
        rival = next((s for s, n in ranked[1:] if n.ref != name.ref or n.slot != name.slot), 0.0)
        # The longer stretch wins a tie: "back yard lights" over "back yard" for "Backyard Lights".
        if best is None or (round(top, 3), j - i) > (round(best[0], 3), best[2] - best[1]):
            best = (top, i, j, name, rival)
    if best is None:
        return None
    top, i, j, name, rival = best
    how = band(top, rival)
    if how == "low":
        return None
    said = " ".join(words[i:j])
    for n in everything or ():
        if (n.slot, n.ref) not in allowed_keys and score(said, n.text, floor=EXACT) >= EXACT:
            return None  # they said a real name - just not one this request can use
    return Fix(i, j, name, top, how)


__all__ = ["HIGH", "MEDIUM", "CLEAR_LEAD", "EXACT", "WEAK", "norm", "content", "sound_word", "sound", "score",
           "band", "Name", "NameIndex", "Fix", "correct"]
