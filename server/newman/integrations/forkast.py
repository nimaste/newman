"""Forkast: your own recipe collection, read out and shown on a wall tablet while you cook (recipes.py).

Sign-in happens once: the email and password go to Forkast's POST /api/auth/login, which answers with a
sign-in token (a JWT - Forkast can be set to issue ones that never expire, SESSION_DURATION=never); only
that token is kept (integrations/<id>/token.json, readable by Newman alone) and the password is dropped
from the settings. When Forkast stops accepting the token (it answers 401: the account's password or the
server's secret changed) the connection asks for the password once more.

Newman only reads: the recipe list (GET /api/recipes, a page at a time - kept for matching spoken names
and refreshed every few minutes or on demand), one recipe with its ingredients and steps
(GET /api/recipes/<id>) and its picture. One "recipe book" device (kinds.py) shows how many recipes there are.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import aiohttp

from .base import CommandError, Field, Integration

log = logging.getLogger(__name__)

REFRESH_S = 10 * 60        # the recipe list, for matching names
DETAIL_TTL_S = 5 * 60      # a recipe read again after this long (unless the list says it changed)
MAX_PAGES = 40             # 50 a page: 2000 recipes
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class ForkastError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class NeedsSignIn(CommandError):
    """Forkast answered 401: the kept token no longer works."""


def clean_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url):
        url = "http://" + url
    return url


class ForkastClient:
    """Forkast's JSON API with a bearer token."""

    def __init__(self, url: str, token: str = "", timeout: float = 15) -> None:
        self.url, self.token, self.timeout = clean_url(url), token, timeout
        self._session: aiohttp.ClientSession | None = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    async def _json(r: aiohttp.ClientResponse) -> Any:
        try:
            return await r.json(content_type=None)
        except ValueError:
            return None

    async def login(self, email: str, password: str) -> str:
        s = await self.session()
        async with s.post(f"{self.url}/api/auth/login", json={"email": email, "password": password}) as r:
            data = await self._json(r)
            if r.status >= 400:
                msg = (data or {}).get("error") if isinstance(data, dict) else None
                raise ForkastError(r.status, str(msg or f"HTTP {r.status}"))
        token = (data or {}).get("token") if isinstance(data, dict) else None
        if not token:
            raise ForkastError(0, "That doesn't look like a Forkast server")
        return str(token)

    async def get(self, path: str, **params: Any) -> Any:
        s = await self.session()
        async with s.get(f"{self.url}{path}", params={k: str(v) for k, v in params.items() if v is not None},
                         headers={"Authorization": f"Bearer {self.token}"}) as r:
            data = await self._json(r)
            if r.status >= 400:
                msg = (data or {}).get("error") if isinstance(data, dict) else None
                raise ForkastError(r.status, str(msg or f"HTTP {r.status}"))
        if data is None:
            raise ForkastError(0, "That doesn't look like a Forkast server")
        return data

    async def fetch(self, url: str) -> tuple[bytes, str]:
        """A picture: Forkast's own (/uploads/…) or one on the web."""
        s = await self.session()
        full = url if re.match(r"^https?://", url) else f"{self.url}/{url.lstrip('/')}"
        async with s.get(full) as r:
            if r.status >= 400:
                raise ForkastError(r.status, f"HTTP {r.status}")
            ctype = r.headers.get("Content-Type", "")
            if not ctype.startswith("image/"):
                raise ForkastError(415, "Not a picture")
            body = await r.content.read(MAX_IMAGE_BYTES + 1)
            if len(body) > MAX_IMAGE_BYTES:
                raise ForkastError(413, "Picture too big")
            return body, ctype


def summary_json(r: dict[str, Any]) -> dict[str, Any]:
    """What the hub keeps of a recipe in the list."""
    count = r.get("_count") if isinstance(r.get("_count"), dict) else {}
    return {"id": str(r.get("id") or ""), "title": str(r.get("title") or "Untitled"),
            "hasImage": bool(r.get("imageUrl")), "imageUrl": r.get("imageUrl") or None,
            "baseServings": r.get("baseServings") or None, "prepMinutes": r.get("prepTimeMinutes"),
            "cookMinutes": r.get("cookTimeMinutes"), "steps": count.get("instructions"),
            "updatedAt": str(r.get("updatedAt") or "")}


class ForkastIntegration(Integration):
    type = "forkast"
    name = "Forkast"
    category = "other"
    provides = ("recipe_book",)
    description = ("Your recipes from Forkast, on a wall tablet while you cook: “Hey Newman, pull up the pancakes”, "
                   "then step by step, the ingredients, scaled amounts and timers - by voice or touch. The password "
                   "is used once to sign in; Newman keeps only Forkast's sign-in token.")
    fields = [
        Field("url", "Server address", required=True, placeholder="http://10.1.1.2:9700",
              help="Where Newman reaches Forkast - its address on your network is best."),
        Field("username", "Email", required=True, help="The email you sign in to Forkast with."),
        Field("password", "Password", "password",
              help="Used once to sign in, then forgotten. Only needed again if Forkast stops accepting Newman."),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._dir = Path(self.ctx.data_dir) / "integrations" / self.id
        self.client: ForkastClient | None = None
        self.list: list[dict[str, Any]] = []
        self.listed_at = 0.0
        self._details: dict[str, tuple[float, str, dict[str, Any]]] = {}  # id -> (when, updatedAt, recipe)
        self._images: dict[str, tuple[bytes, str]] = {}
        self._refresh_lock = asyncio.Lock()
        self._wake = asyncio.Event()

    # -- sign-in ------------------------------------------------------------------------------

    def _token_file(self) -> Path:
        return self._dir / "token.json"

    def saved_token(self) -> dict[str, str]:
        try:
            d = json.loads(self._token_file().read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_token(self, data: dict[str, str]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._token_file().with_suffix(".part")
        tmp.write_text(json.dumps(data))
        tmp.chmod(0o600)
        tmp.replace(self._token_file())

    def _forget_token(self) -> None:
        self._token_file().unlink(missing_ok=True)

    ASK_AGAIN = "Forkast no longer accepts Newman's sign-in - enter the password in Settings and save (used once)"

    async def sign_in(self) -> bool:
        url = clean_url(str(self.config.get("url") or ""))
        email = str(self.config.get("username") or "").strip()
        password = str(self.config.get("password") or "")
        saved = self.saved_token()
        client = ForkastClient(url)
        try:
            if password:
                client.token = await client.login(email, password)
            elif saved.get("token") and saved.get("email", email) == email:
                client.token = saved["token"]
            else:
                await client.close()
                self.set_status("pairing", "Enter your Forkast password in Settings and save (used once, then forgotten)")
                return False
            await self._load_list(client)
        except ForkastError as e:
            await client.close()
            if e.status in (400, 401, 403):
                if password:
                    msg = "Forkast didn't accept that email and password"
                else:
                    self._forget_token()
                    msg = self.ASK_AGAIN
                self.set_status("pairing", msg)
            else:
                self.set_status("error", f"Forkast said: {e}")
            return False
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            await client.close()
            self.set_status("error", f"Can't reach Forkast at {url} ({e.__class__.__name__})")
            return False
        if password:
            # Signed in: keep the token; the password is never needed again.
            self._save_token({"email": email, "token": client.token})
            cfg = {k: v for k, v in self.config.items() if k != "password"}
            self.config = cfg
            if self.save_config:
                self.save_config(cfg)
        self.client = client
        return True

    async def run(self) -> None:
        attempt = 0
        while True:
            if await self.sign_in():
                break
            if self.status == "pairing":
                await self._offline()
                await asyncio.Event().wait()  # until the settings change (the manager restarts us)
            await self.backoff_sleep(attempt)
            attempt += 1
        self.set_status("connected", self._detail())
        await self._publish()
        try:
            while True:
                try:
                    await asyncio.wait_for(self._wake.wait(), REFRESH_S)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                try:
                    await self.refresh()
                except NeedsSignIn:
                    await self._offline()
                    await asyncio.Event().wait()
                except CommandError:
                    pass
        finally:
            if self.client:
                await self.client.close()

    def _detail(self) -> str:
        n = len(self.list)
        return f"{n} recipe{'s' if n != 1 else ''}"

    def connected(self) -> bool:
        return self.client is not None and self.status == "connected"

    def _need_client(self) -> ForkastClient:
        if self.status == "pairing":
            raise NeedsSignIn("Forkast needs signing in again")
        if not self.client:
            raise CommandError("Forkast isn't connected")
        return self.client

    async def _call(self, path: str, **params: Any) -> Any:
        """A read, with Forkast's 401 turned into "sign in again"."""
        c = self._need_client()
        try:
            return await c.get(path, **params)
        except ForkastError as e:
            if e.status == 401:
                self._forget_token()
                self.set_status("pairing", self.ASK_AGAIN)
                raise NeedsSignIn("Forkast needs signing in again") from e
            if e.status == 404:
                raise CommandError("That recipe isn't in Forkast any more") from e
            self.set_status("error", f"Forkast said: {e}")
            raise CommandError(f"Forkast said: {e}") from e
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            self.set_status("error", f"Can't reach Forkast ({e.__class__.__name__})")
            raise CommandError("I can't reach Forkast right now") from e

    # -- the recipes ------------------------------------------------------------------------------

    async def _load_list(self, client: ForkastClient) -> None:
        out: list[dict[str, Any]] = []
        page = 1
        while page <= MAX_PAGES:
            data = await client.get("/api/recipes", page=page, limit=50)
            got = data.get("recipes") if isinstance(data, dict) else None
            if not isinstance(got, list):
                raise ForkastError(0, "That doesn't look like a Forkast server")
            out += [summary_json(r) for r in got if isinstance(r, dict) and r.get("id")]
            if page >= int(data.get("totalPages") or 1) or not got:
                break
            page += 1
        out.sort(key=lambda r: r["title"].lower())
        self.list, self.listed_at = out, time.time()

    async def refresh(self) -> list[dict[str, Any]]:
        """The recipe list read again (every few minutes, and when a spoken name isn't found)."""
        c = self._need_client()
        async with self._refresh_lock:
            try:
                await self._load_list(c)
            except ForkastError as e:
                if e.status == 401:
                    self._forget_token()
                    self.set_status("pairing", self.ASK_AGAIN)
                    raise NeedsSignIn("Forkast needs signing in again") from e
                self.set_status("error", f"Forkast said: {e}")
                raise CommandError(f"Forkast said: {e}") from e
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                self.set_status("error", f"Can't reach Forkast ({e.__class__.__name__})")
                raise CommandError("I can't reach Forkast right now") from e
        self.set_status("connected", self._detail())
        await self._publish()
        return self.list

    def summaries(self) -> list[dict[str, Any]]:
        return list(self.list)

    async def recipe(self, rid: str) -> dict[str, Any]:
        """One recipe as Forkast has it (ingredients, instructions…) - read again when it changed."""
        listed = next((r for r in self.list if r["id"] == rid), None)
        kept = self._details.get(rid)
        if kept and time.time() - kept[0] < DETAIL_TTL_S and (not listed or listed["updatedAt"] == kept[1]):
            return kept[2]
        data = await self._call(f"/api/recipes/{rid}")
        if not isinstance(data, dict) or not data.get("id"):
            raise CommandError("Forkast sent something odd for that recipe")
        self._details[rid] = (time.time(), str(data.get("updatedAt") or ""), data)
        if len(self._details) > 50:
            self._details.pop(next(iter(self._details)))
        if self.status != "connected" and self.client:
            self.set_status("connected", self._detail())
        return data

    async def image(self, rid: str) -> tuple[bytes, str] | None:
        listed = next((r for r in self.list if r["id"] == rid), None)
        url = (listed or {}).get("imageUrl")
        if not url:
            return None
        key = f"{rid}|{url}"
        if key in self._images:
            return self._images[key]
        try:
            got = await self._need_client().fetch(url)
        except (ForkastError, aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return None
        if len(self._images) > 30:
            self._images.pop(next(iter(self._images)))
        self._images[key] = got
        return got

    # -- the recipe book device -----------------------------------------------------------------

    async def _publish(self) -> None:
        eid = self.entity_id("recipes")
        self.hub.upsert(eid, kind="recipe_book", name=self.display_name, integration=self.id)
        await self.hub.update(eid, state="ready", attrs={"recipes": len(self.list)}, available=True)

    async def _offline(self) -> None:
        eid = self.entity_id("recipes")
        if self.hub.get(eid):
            await self.hub.update(eid, available=False)
