"""Categories (Devices, the phone's Home cards) and the overrides on a device: the category it's moved to and
the icon it's given (Devices > a device > Category / Icon). Built-in categories come from kinds.CATEGORIES;
a custom one ("Pool") is made by moving a device into a new category and is gone once nothing is in it.

Settings "categories": {"devices": {entity id: category id}, "icons": {entity id: icon name},
"custom": {category id: name}}. The device moves are mirrored into state.CATEGORY_OVERRIDES, so every
Entity.category - and everything grouped by it - follows them. Behaviour stays with the device's kind.
"""

from __future__ import annotations

import re
from typing import Any

from .kinds import CATEGORY, CATEGORIES, KINDS, icons
from .state import CATEGORY_OVERRIDES, Entity

SETTING = "categories"
CUSTOM_PREFIX = "cat."


def screen_of(category_id: str) -> str | None:
    """The purpose-built phone screen a category opens (kinds.Category.screen); custom ones: none."""
    c = CATEGORY.get(category_id)
    return c.screen if c else None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "category"


class Categories:
    def __init__(self, store: Any, hub: Any) -> None:
        self.store, self.hub = store, hub
        self._load()

    # -- saved ------------------------------------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        v = self.store.settings().get(SETTING)
        v = v if isinstance(v, dict) else {}
        return {"devices": dict(v.get("devices") or {}), "icons": dict(v.get("icons") or {}),
                "custom": dict(v.get("custom") or {})}

    def _saved(self) -> dict[str, Any]:
        """A copy of what's saved (read once, then kept current by _save)."""
        return {k: dict(d) for k, d in self._v.items()}

    def _save(self, v: dict[str, Any]) -> None:
        self.store.set_setting(SETTING, v)
        self._load(v)

    def _load(self, v: dict[str, Any] | None = None) -> None:
        self._v = v if v is not None else self._read()
        CATEGORY_OVERRIDES.clear()
        CATEGORY_OVERRIDES.update({e: c for e, c in self._v["devices"].items() if self.known(c, self._v)})

    # -- reading ----------------------------------------------------------------------------------------

    def known(self, category_id: Any, v: dict[str, Any] | None = None) -> bool:
        v = v if v is not None else self._v
        return isinstance(category_id, str) and (category_id in CATEGORY or category_id in v["custom"])

    def info(self, category_id: str) -> dict[str, Any]:
        if c := CATEGORY.get(category_id):
            return {"id": c.id, "name": c.name, "builtIn": True, "screen": c.screen}
        name = self._v["custom"].get(category_id) or category_id
        return {"id": category_id, "name": name, "builtIn": False, "screen": None}

    def list(self) -> list[dict[str, Any]]:
        """Every built-in category, then the custom ones, each with how many devices are in it now."""
        counts: dict[str, int] = {}
        for e in self.hub.all():
            counts[e.category] = counts.get(e.category, 0) + 1
        out = [{**self.info(c.id), "count": counts.get(c.id, 0)} for c in CATEGORIES]
        out += [{**self.info(cid), "count": counts.get(cid, 0)} for cid in self._v["custom"]]
        return out

    def order(self) -> list[str]:
        return [c.id for c in CATEGORIES] + list(self._v["custom"])

    def icon_of(self, entity_id: str) -> str | None:
        return self._v["icons"].get(entity_id)

    def moved(self, entity_id: str) -> bool:
        return entity_id in CATEGORY_OVERRIDES

    # -- changing ---------------------------------------------------------------------------------------

    def set_category(self, e: Entity, category_id: Any, new_name: Any = None) -> dict[str, Any]:
        """Moves a device into a category (None: back to its kind's). new_name: into a new custom category
        of that name (or the existing one with that name). ValueError: refused, in words."""
        v = self._saved()
        if new_name is not None:
            name = str(new_name).strip()[:40]
            if not name:
                raise ValueError("Give the new category a name")
            taken = {c.name.lower(): c.id for c in CATEGORIES} | {n.lower(): i for i, n in v["custom"].items()}
            category_id = taken.get(name.lower())
            if category_id is None:
                base = CUSTOM_PREFIX + slug(name)
                category_id, n = base, 2
                while category_id in v["custom"] or category_id in CATEGORY:
                    category_id, n = f"{base}_{n}", n + 1
                v["custom"][category_id] = name
        elif category_id is not None and not self.known(category_id, v):
            raise ValueError("Unknown category")
        if category_id is None or category_id == KINDS[e.kind].category:
            v["devices"].pop(e.id, None)
        else:
            v["devices"][e.id] = category_id
        self._prune(v)
        self._save(v)
        return self.info(e.category)

    def rename(self, category_id: str, name: Any) -> dict[str, Any]:
        v = self._saved()
        if category_id not in v["custom"]:
            raise ValueError("Only a category made in Newman can be renamed")
        name = str(name or "").strip()[:40]
        if not name:
            raise ValueError("Give the category a name")
        if name.lower() in ({c.name.lower() for c in CATEGORIES}
                            | {n.lower() for i, n in v["custom"].items() if i != category_id}):
            raise ValueError(f"There's already a category called {name}")
        v["custom"][category_id] = name
        self._save(v)
        return self.info(category_id)

    def set_icon(self, entity_id: str, icon: Any) -> str | None:
        v = self._saved()
        if icon is None:
            v["icons"].pop(entity_id, None)
        elif isinstance(icon, str) and icon in icons():
            v["icons"][entity_id] = icon
        else:
            raise ValueError("Unknown icon")
        self._save(v)
        return v["icons"].get(entity_id)

    def _prune(self, v: dict[str, Any]) -> None:
        """A custom category nothing is in any more is gone."""
        used = set(v["devices"].values())
        for cid in [c for c in v["custom"] if c not in used]:
            del v["custom"][cid]

    def replace_ids(self, mapping: dict[str, str]) -> int:
        """A device swapped for another (replace.py): its category and icon go with it."""
        v, n = self._saved(), 0
        for key in ("devices", "icons"):
            for old, new in mapping.items():
                if old in v[key]:
                    val = v[key].pop(old)
                    v[key].setdefault(new, val)
                    n += 1
        if n:
            self._prune(v)
            self._save(v)
        return n

    def forget(self, entity_id: str) -> None:
        v = self._saved()
        if entity_id in v["devices"] or entity_id in v["icons"]:
            v["devices"].pop(entity_id, None)
            v["icons"].pop(entity_id, None)
            self._prune(v)
            self._save(v)
