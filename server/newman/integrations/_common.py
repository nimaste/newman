"""Small helpers several integrations share: plain-English counts for status lines, stable ids,
and keeping fire-and-forget tasks alive."""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Any, Coroutine, Iterable

from ..kinds import KINDS, join_names


def plural(n: int, word: str) -> str:
    if n == 1:
        return f"1 {word}"
    head, _, last = word.rpartition(" ")
    last = last + ("es" if last.endswith(("s", "sh", "ch", "x")) else "s")
    return f"{n} {(head + ' ' if head else '')}{last}"


def device_summary(kinds: Iterable[str]) -> str:
    """"2 switches and 1 sensor" - counted by each kind's own noun, so nothing here knows any kind."""
    counts: Counter[str] = Counter()
    for k in kinds:
        counts[KINDS[k].noun if k in KINDS else "device"] += 1
    return join_names([plural(n, noun) for noun, n in counts.most_common()]) or "no devices yet"


def clean_id(value: Any) -> str:
    """A MAC / serial / unique id as a safe, stable piece of an entity id ("A8:03:2A" -> "a8032a")."""
    return re.sub(r"[^a-z0-9_]", "", str(value or "").lower().replace("-", "_"))


class TaskKeeper:
    """Schedules coroutines from library callbacks (which are often plain functions) and keeps a
    reference until they finish, so they aren't garbage-collected half way."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        """Tests: wait for everything scheduled so far."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
