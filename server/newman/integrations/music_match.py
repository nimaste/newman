"""Matching a spoken music request against the library - speech-to-text mishears song names ("Better
Roses" for "Bed of Roses"). The comparing itself (word by word, letter by letter and by how the words sound)
is the hub's shared name matching (voice/names.py); this adds what's particular to music. Pure functions;
the music connection (navidrome.py) gathers the candidates and decides what to do with the result:

    rank(said, candidates, artist="") -> [(score 0..1, candidate)], best first
    band(ranked) -> "high" (play it) | "medium" (ask "Did you mean …?") | "low" (couldn't find it)

A candidate is {"kind": "song" | "album" | "artist" | "playlist", "name", "artist", "item"}.
"""
from __future__ import annotations

from typing import Any

from ..voice import names as N
from ..voice.names import CLEAR_LEAD, HIGH, MEDIUM, SAME_SOUND, WEAK, content, norm, score, sound, sound_word  # noqa: F401

KIND_ORDER = {"artist": 3, "playlist": 2, "album": 1, "song": 0}  # an exact tie goes to the bigger thing


def rank(said: str, candidates: list[dict[str, Any]], artist: str = "") -> list[tuple[float, dict[str, Any]]]:
    """Best first. With an artist named ("X by Y"), a candidate's artist must fit too (and a candidate
    that IS an artist is only matched on its name)."""
    out = []
    seen = set()
    for c in candidates:
        key = (c["kind"], str((c.get("item") or {}).get("id") or c.get("name")))
        if key in seen:
            continue
        seen.add(key)
        s = score(said, str(c.get("name") or ""))
        if artist and c["kind"] != "artist":
            a = score(artist, str(c.get("artist") or ""))
            if a < 0.6:
                continue
            s = 0.75 * s + 0.25 * a
        elif artist:
            continue
        out.append((s, c))
    out.sort(key=lambda x: (round(x[0], 3), KIND_ORDER.get(x[1]["kind"], 0)), reverse=True)
    return out


def band(ranked: list[tuple[float, dict[str, Any]]]) -> str:
    if not ranked:
        return "low"
    top = ranked[0][0]
    # The runner-up only makes it doubtful when it's a different thing by name (an album and its title
    # song are the same answer).
    rival = next((s for s, c in ranked[1:] if norm(str(c.get("name"))) != norm(str(ranked[0][1].get("name")))), 0.0)
    return N.band(top, rival)
