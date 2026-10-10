"""Connecting apps to this hub before anyone signs in.

- GET /api/hello (no sign-in): who this hub is - its id, name, version and addresses. Nothing secret.
- mDNS: the hub announces itself as `_newman._tcp.local.` so the apps list it on the home network.
- The connect link / QR code: newman://connect?v=1&id=..&name=..&local=..&outside=.. (no secrets).

The hub's id is made once and kept in the settings ("hubId"). Its name is the "homeName" setting
(else "Newman"); its outside address the "outsideUrl" setting (else PUBLIC_URL).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import secrets
import socket
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit, urlunsplit

if TYPE_CHECKING:
    from zeroconf.asyncio import AsyncZeroconf

log = logging.getLogger(__name__)

SERVICE_TYPE = "_newman._tcp.local."
DEFAULT_NAME = "Newman"
NAME_MAX = 48
# Network interfaces that are never the home network: Docker's and VPNs'.
VIRTUAL_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "vnet", "tailscale", "tun", "tap", "wg", "zt",
                    "cni", "flannel", "cali", "shim-", "vhost", "kube", "podman", "lxc")


def hub_id(store: Any) -> str:
    """This hub's stable id (made the first time it's asked for)."""
    hid = str(store.settings().get("hubId") or "")
    if not hid:
        hid = secrets.token_hex(8)
        store.set_setting("hubId", hid)
    return hid


def home_name(settings: dict[str, Any]) -> str:
    return str(settings.get("homeName") or "").strip() or DEFAULT_NAME


def clean_name(raw: Any) -> str:
    """A home name as typed: one line, at most 48 characters ("" = the default)."""
    name = " ".join(str(raw or "").split())
    if len(name) > NAME_MAX:
        raise ValueError(f"Keep the name to {NAME_MAX} characters")
    return name


def normalize_outside(raw: Any) -> str:
    """The outside address as typed -> "https://home.example.com/" ("" = none). ValueError when it isn't one."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if "://" not in s:
        s = "https://" + s
    try:
        u = urlsplit(s)
        port = u.port  # raises on a bad port
    except ValueError:
        raise ValueError("That isn't a web address")
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or " " in s:
        raise ValueError("Use a web address like https://home.example.com")
    if u.query or u.fragment:
        raise ValueError("Leave out anything after ? or #")
    host = u.hostname.lower()
    if "." not in host and ":" not in host:
        raise ValueError("Use the full name, like home.example.com")
    if u.scheme == "http" and not _private_host(host):
        raise ValueError("Use https:// for an address outside your home network")
    path = u.path if u.path.endswith("/") else u.path + "/"
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    return urlunsplit((u.scheme, netloc, path, "", ""))


def _private_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return host.endswith((".local", ".lan", ".home.arpa", ".internal"))


def outside_url(settings: dict[str, Any], public_url: str = "") -> str | None:
    """The saved outside address; without one, PUBLIC_URL; None when neither is set."""
    if "outsideUrl" in settings:
        return str(settings.get("outsideUrl") or "") or None
    try:
        return normalize_outside(public_url) or None
    except ValueError:
        return None


def lan_ipv4s(adapters: list[Any] | None = None, only: str = "") -> list[str]:
    """The hub's home-network IPv4 addresses: private ones on real interfaces (not Docker / VPNs).
    [only]: a comma-separated list of interface names to use instead (MDNS_INTERFACE)."""
    if adapters is None:
        try:
            import ifaddr
            adapters = ifaddr.get_adapters()
        except Exception:
            return []
    wanted = {x.strip() for x in only.split(",") if x.strip()}
    found: list[str] = []
    for a in adapters:
        name = str(getattr(a, "nice_name", "") or getattr(a, "name", ""))
        if wanted:
            if name not in wanted:
                continue
        elif name.startswith(VIRTUAL_PREFIXES):
            continue
        for ip in getattr(a, "ips", []):
            addr = ip.ip if isinstance(ip.ip, str) else None
            if not addr:
                continue
            try:
                v = ipaddress.IPv4Address(addr)
            except ValueError:
                continue
            if (v.is_private and not v.is_loopback and not v.is_link_local) or wanted:
                if addr not in found:
                    found.append(addr)
    return found


def local_urls(ips: list[str], port: int) -> list[str]:
    return [f"http://{ip}:{port}/" for ip in ips]


def hello_json(hid: str, name: str, version: str, outside: str | None, locals_: list[str]) -> dict[str, Any]:
    return {"app": "newman", "id": hid, "name": name, "version": version, "outsideUrl": outside or None,
            "localUrls": locals_}


def connect_link(hid: str, name: str, local: str | None, outside: str | None) -> str:
    """newman://connect?v=1&id=..&name=..&local=..&outside=.. - every value URL-encoded, no secrets."""
    q = lambda v: quote(v or "", safe="")  # noqa: E731
    return f"newman://connect?v=1&id={q(hid)}&name={q(name)}&local={q(local)}&outside={q(outside)}"


def connect_info(hid: str, name: str, version: str, outside: str | None, locals_: list[str]) -> dict[str, Any]:
    return {**hello_json(hid, name, version, outside, locals_),
            "link": connect_link(hid, name, locals_[0] if locals_ else None, outside)}


def mdns_txt(hid: str, name: str, outside: str | None) -> dict[str, str]:
    """The TXT record of the _newman._tcp announcement."""
    return {"id": hid, "name": name[:NAME_MAX], "v": "1", "ext": outside or "", "path": "/"}


def instance_name(name: str) -> str:
    """The mDNS instance label: the home name without dots (they'd split the label), at most 63 bytes."""
    label = " ".join(name.replace(".", " ").split()) or DEFAULT_NAME
    while len(label.encode()) > 63:
        label = label[:-1]
    return label


class Announcer:
    """Announces this hub over mDNS on its HTTP port (NEWMAN_MDNS=0 turns it off)."""

    def __init__(self, port: int, enabled: bool = True, interface: str = "") -> None:
        self.port = port
        self.enabled = enabled
        self.interface = interface
        self._azc: "AsyncZeroconf | None" = None
        self._info: Any = None
        self._want: tuple[str, str, str | None] | None = None
        self._lock = asyncio.Lock()

    def _service(self, hid: str, name: str, outside: str | None) -> Any:
        from zeroconf.asyncio import AsyncServiceInfo
        ips = lan_ipv4s(only=self.interface)
        return AsyncServiceInfo(
            SERVICE_TYPE, f"{instance_name(name)}.{SERVICE_TYPE}", port=self.port,
            properties=mdns_txt(hid, name, outside), addresses=[socket.inet_aton(ip) for ip in ips],
            server=f"newman-{hid[:8]}.local.")

    async def start(self, azc: "AsyncZeroconf", hid: str, name: str, outside: str | None) -> None:
        self._azc = azc
        await self.update(hid, name, outside)

    async def update(self, hid: str, name: str, outside: str | None) -> None:
        """(Re-)announce with this name and outside address; nothing happens when they're unchanged."""
        if not self.enabled or self._azc is None:
            return
        async with self._lock:
            if self._want == (hid, name, outside):
                return
            try:
                if self._info is not None:
                    await self._azc.async_unregister_service(self._info)
                    self._info = None
                info = self._service(hid, name, outside)
                if not info.addresses:
                    log.info("Not announcing Newman over mDNS: no home-network address found")
                    return
                await self._azc.async_register_service(info, allow_name_change=True)
                self._info, self._want = info, (hid, name, outside)
                log.info("Announcing %r over mDNS (%s)", name, ", ".join(info.parsed_addresses()))
            except Exception:
                log.exception("mDNS announcement failed - apps can still connect by address")

    async def stop(self) -> None:
        if self._azc is not None and self._info is not None:
            try:
                await self._azc.async_unregister_service(self._info)
            except Exception:
                pass
        self._info = None
