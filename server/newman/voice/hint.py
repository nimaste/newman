"""The Whisper hint: the names speech-to-text should expect in this home, written where the Whisper
container reads its initial prompt.

Whisper spells what it hears as the words it already expects, so a short list of the home's names
("Newman. Dante, Massimo, Backyard, Front Porch, Good Night, …") makes "Massimo" come out as Massimo rather
than "my see mo". Wyoming's `transcribe` request has no field the Whisper service uses for this (checked
against wyoming-faster-whisper 3.8.1: it reads only the language), so the hint goes in a file instead -
DATA_DIR/voice/whisper_prompt.txt - which the `newman-whisper` container reads at start through
WYO_WHISPER_INITIAL_PROMPT_FILE (README → Voice). The hub rewrites it whenever the names change (a few
seconds after the change, and only when the words differ); Whisper picks it up when it restarts, which the
hub does itself through the Docker connection once the list has settled and nobody is talking
(whisper_restart.py).

    build(groups, max_tokens=200, max_words=150) -> "Newman. A, B, C."   (pure)
    groups_for(index) -> the index's names in priority order               (pure)
    HintFile(index, path)                                                  writes it, debounced
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from .names import Name, NameIndex

log = logging.getLogger(__name__)

LEAD = "Newman."
MAX_TOKENS = 200   # Whisper keeps at most 223 prompt tokens (and drops the *start* past that)
MAX_WORDS = 150
MAX_NAME_CHARS = 40
DELAY_S = 15.0     # after a change, wait this long for more before writing


def estimate_tokens(text: str) -> int:
    """Whisper tokens a prompt would take, over- rather than under-estimated: Whisper's own tokenizer runs
    about 3 characters a token on lists of names; this counts 2.5 (and at least one a word)."""
    return max(math.ceil(len(text) / 2.5), len(text.split()))


def clean_name(text: str) -> str:
    """One name as it goes in the list: no commas or line breaks (they'd split it), single spaces; "" when
    it's no use to Whisper (no letters, or too long to be said as a name)."""
    t = " ".join(re.sub(r"[,;:\"\n\r\t]+", " ", str(text or "")).split())
    if not re.search(r"[A-Za-z]", t) or len(t) > MAX_NAME_CHARS:
        return ""
    return t


def build(groups: Iterable[Iterable[str]], max_tokens: int = MAX_TOKENS, max_words: int = MAX_WORDS) -> str:
    """"Newman. " then the names, group by group (most important first), each once (ignoring case), as many
    as fit `max_tokens` (estimate_tokens) and `max_words`; a name that doesn't fit is left out and the
    shorter ones after it still get their chance."""
    seen = {LEAD.rstrip(".").lower()}
    picked: list[str] = []
    for group in groups:
        for raw in group:
            name = clean_name(raw)
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            candidate = f"{LEAD} {', '.join(picked + [name])}."
            if estimate_tokens(candidate) > max_tokens or len(candidate.split()) > max_words:
                continue
            picked.append(name)
    return f"{LEAD} {', '.join(picked)}." if picked else LEAD


def groups_for(index: NameIndex) -> list[list[str]]:
    """The names to hint, most important first: the household, rooms, devices (most used first, then the
    ones that take commands), automations, then the most played artists (only when play counts are known)."""
    def by_use(names: list[Name]) -> list[str]:
        return [n.text for n in sorted(names, key=lambda n: (-n.weight, n.text.lower()))]
    return [[n.text for n in index.names("person")],
            [n.text for n in index.names("room")],
            by_use(index.names("device")),
            [n.text for n in index.names("automation")],
            by_use([n for n in index.names("artist") if n.weight > 0])]


class HintFile:
    """Keeps `path` holding the hint for `index`: written DELAY_S after the names change (more changes in that
    time wait with it), and whenever `run` looks again."""

    def __init__(self, index: NameIndex, path: Path, delay: float = DELAY_S,
                 groups: Callable[[NameIndex], list[list[str]]] = groups_for) -> None:
        self.index, self.path, self.delay, self.groups = index, path, delay, groups
        self.written_at: float | None = None
        self._task: asyncio.Task | None = None
        index.listeners.append(self.changed)

    def text(self) -> str:
        return build(self.groups(self.index))

    def info(self) -> dict[str, Any]:
        t = self.text()
        try:
            on_disk = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            on_disk = None
        return {"text": t, "tokens": estimate_tokens(t), "words": len(t.split()), "file": str(self.path),
                "written": on_disk == t, "writtenAt": self.written_at}

    def write(self) -> bool:
        """Writes the hint when it differs from the file's; True when it did."""
        t = self.text()
        try:
            if self.path.is_file() and self.path.read_text(encoding="utf-8").strip() == t:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".part")
            tmp.write_text(t + "\n", encoding="utf-8")
            tmp.chmod(0o644)
            tmp.replace(self.path)
        except OSError as e:
            log.warning("Voice: couldn't write the Whisper hint to %s: %s", self.path, e)
            return False
        self.written_at = time.time()
        log.info("Voice: Whisper hint updated (%d words)", len(t.split()))
        return True

    def changed(self, _key: str | None = None) -> None:
        """Names changed: write in a little while (a burst of changes - a connection starting - writes once)."""
        if self._task and not self._task.done():
            return
        try:
            self._task = asyncio.get_running_loop().create_task(self._later(), name="voice-hint")
        except RuntimeError:
            pass  # no loop (a script, a sync test): `run` or the next change writes it

    async def _later(self) -> None:
        await asyncio.sleep(self.delay)
        self.write()

    async def run(self, every: float = 600.0, first: float = 60.0,
                  warm: Callable[[], Awaitable[None]] | None = None) -> None:
        """Writes `first` seconds after start (the connections have registered their devices by then), then
        looks again every `every` seconds - names with no change event of their own (people, the music
        library) are caught there. `warm`: fills caches the index reads (the music library's names). With no
        file yet it's written straight away (the Whisper container won't start without it)."""
        if not self.path.is_file():
            self.write()
        await asyncio.sleep(first)
        while True:
            if warm is not None:
                try:
                    await warm()
                except Exception as e:  # noqa: BLE001 - the music server away: the names on hand will do
                    log.debug("Voice hint: %s", e)
            self.index.mark(None)
            self.write()
            await asyncio.sleep(every)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
