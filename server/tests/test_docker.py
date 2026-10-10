"""Docker, offline: containers, the event stream (crashes vs. stops on purpose, health), stats, commands."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

import httpx

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations.base import CommandError, Context  # noqa: E402
from newman.integrations.docker import GONE_GRACE_S, YOUNG_S, DockerIntegration, health_of, usage  # noqa: E402
from newman.kinds import KINDS, properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402

CONTAINERS = [
    {"Id": "aaa111" * 4, "Names": ["/plex"], "Image": "lscr.io/linuxserver/plex", "State": "running", "Status": "Up 2 days"},
    {"Id": "bbb222" * 4, "Names": ["/immich_server"], "Image": "immich", "State": "running",
     "Status": "Up 9 days (healthy)"},
    {"Id": "ccc333" * 4, "Names": ["/updater"], "Image": "updater", "State": "exited", "Status": "Exited (0) 2 days ago"},
]
STATS = {"cpu_stats": {"cpu_usage": {"total_usage": 2_000_000}, "system_cpu_usage": 20_000_000, "online_cpus": 4},
         "precpu_stats": {"cpu_usage": {"total_usage": 1_000_000}, "system_cpu_usage": 10_000_000},
         "memory_stats": {"usage": 600 * 1048576, "limit": 2000 * 1048576, "stats": {"inactive_file": 100 * 1048576}}}


def ev(action, name, **attrs):
    return {"Type": "container", "Action": action,
            "Actor": {"ID": next(c["Id"] for c in CONTAINERS if c["Names"][0] == "/" + name), "Attributes": {"name": name, **attrs}}}


class FakeDocker:
    def __init__(self):
        self.posts = []
        self.events = []
        self.read_only = False
        self.containers = None  # what Docker has now (None: CONTAINERS)
        self.down = False       # Docker can't be reached
        self.auto_remove = set()  # container ids started with --rm

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=req)
        p = req.url.path
        if req.method == "POST":
            if self.read_only:
                return httpx.Response(403, text="Forbidden")
            self.posts.append(p)
            return httpx.Response(204)
        if p == "/containers/json":
            return httpx.Response(200, json=CONTAINERS if self.containers is None else self.containers)
        if p == "/events":
            return httpx.Response(200, text="".join(json.dumps(e) + "\n" for e in self.events))
        if p.startswith("/containers/") and p.endswith("/json"):
            cid = p.split("/")[2]
            return httpx.Response(200, json={"Id": cid, "HostConfig": {"AutoRemove": cid in self.auto_remove}})
        if p.endswith("/stats"):
            return httpx.Response(200, json=STATS)
        return httpx.Response(404, json={"message": "no such container"})


class DockerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.fake = FakeDocker()
        self.d = self._make({"address": "http://proxy:2375"})
        self.events = []

        async def on_event(ent, event, data):
            self.events.append((ent.id, event, data.get("detail")))
        self.hub.subscribe_events(on_event)

    def _make(self, config):
        d = DockerIntegration("docker", "Docker", config, Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        d._http = httpx.AsyncClient(transport=httpx.MockTransport(self.fake), base_url="http://proxy:2375")
        return d

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_readings(self):
        self.assertEqual([health_of(s) for s in ("Up 2 days", "Up 9 days (healthy)", "Up 1 s (health: starting)")],
                         [None, "healthy", "starting"])
        self.assertEqual(usage(STATS), {"cpu": 40.0, "memory": 500, "memoryPercent": 25.0})

    async def test_lists_containers_and_filters(self):
        await self.d._list()
        plex = self.hub.get("docker.plex")
        self.assertEqual((plex.kind, plex.state, plex.attrs["image"]), ("container", "running", "lscr.io/linuxserver/plex"))
        self.assertEqual(self.hub.get("docker.immich_server").attrs["health"], "healthy")
        self.assertEqual(self.hub.get("docker.updater").state, "stopped")
        self.assertEqual(self.d.detail, "3 containers · 2 running")
        self.assertEqual((await self.d.dynamic_options())["containers"][0], {"value": "immich_server", "label": "immich_server"})
        only = self._make({"address": "http://proxy:2375", "containers": ["plex"]})
        hub_before = {e.id for e in self.hub.all()}
        self.hub.remove_integration("docker")
        await only._list()
        self.assertEqual({e.id for e in self.hub.all()}, {"docker.plex"})
        self.assertTrue(hub_before)
        self.assertEqual((await self.d.dynamic_options())["hide_containers"][1], {"value": "plex", "label": "plex"})

    async def test_leave_out_list_and_new_containers(self):
        d = self._make({"address": "http://proxy:2375", "hide_containers": ["updater"]})
        await d._list()
        self.assertEqual({e.id for e in self.hub.all()}, {"docker.plex", "docker.immich_server"})
        # A container created later appears by itself.
        CONTAINERS.append({"Id": "ddd444" * 4, "Names": ["/newone"], "Image": "x", "State": "running", "Status": "Up 1 s"})
        try:
            await d.handle(ev("start", "newone"))
            self.assertEqual(self.hub.get("docker.newone").state, "running")
            await d.handle(ev("start", "updater"))
            self.assertIsNone(self.hub.get("docker.updater"))
        finally:
            CONTAINERS.pop()

    async def test_older_include_list_becomes_a_leave_out_list(self):
        mig = DockerIntegration.migrate_config
        self.assertEqual(mig({"address": "a", "containers": []}), {"address": "a", "hide_containers": []})
        self.assertEqual(mig({"address": "a", "containers": None}), {"address": "a"})
        self.assertEqual(mig({"address": "a", "containers": ["plex"]}), {"address": "a", "containers": ["plex"]})
        d = self._make({"address": "http://proxy:2375", "containers": ["plex", "immich_server"]})
        saved = []
        d.save_config = saved.append
        # Until Docker answers, the include list still decides; an older client's list is kept as sent.
        self.assertEqual((d._wanted("plex"), d._wanted("updater")), (True, False))
        self.assertEqual(d.translate_config({"containers": ["plex"]}), {"containers": ["plex"]})
        await d._list()
        self.assertEqual(d.config, {"address": "http://proxy:2375", "hide_containers": ["updater"]})
        self.assertEqual(saved, [d.config])
        self.assertEqual({e.id for e in self.hub.all()}, {"docker.plex", "docker.immich_server"})
        # Now Docker's list is known: an older client's include list converts straight away.
        self.assertEqual(d.translate_config({"containers": ["plex"]}),
                         {"hide_containers": ["immich_server", "updater"], "containers": None})
        self.assertEqual(d.translate_config({"containers": []}), {"hide_containers": [], "containers": None})
        self.assertEqual(d.translate_config({"containers": ["plex"], "hide_containers": []}),
                         {"hide_containers": [], "containers": None})
        self.assertEqual(d.translate_config({"hide_containers": ["plex"]}), {"hide_containers": ["plex"], "containers": None})

    async def test_crash_versus_stopped_on_purpose(self):
        await self.d._list()
        self.fake.events = [ev("kill", "plex", signal="15"), ev("die", "plex", exitCode="143"), ev("stop", "plex"),
                            ev("start", "plex"),
                            ev("die", "plex", exitCode="139"),                       # nobody asked: a crash
                            ev("die", "updater", exitCode="0"),                      # finished its job: fine
                            ev("health_status: unhealthy", "immich_server"),
                            ev("health_status: unhealthy", "immich_server")]          # still unhealthy: once
        with self.assertRaises(RuntimeError):  # the fake stream ends
            await self.d._events()
        await asyncio.sleep(0)
        self.assertEqual(self.events, [("docker.plex", "crashed", "exit code 139"),
                                       ("docker.immich_server", "unhealthy", None)])
        self.assertEqual(self.hub.get("docker.plex").state, "stopped")
        self.assertEqual(self.hub.get("docker.immich_server").attrs["health"], "unhealthy")

    async def test_stats_and_properties(self):
        await self.d._list()
        task = asyncio.create_task(self.d._stats_loop())
        for _ in range(100):
            if self.hub.get("docker.plex").attrs.get("cpu") is not None:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        p = {x["id"]: x for x in properties_for(self.hub.get("docker.plex"))}
        self.assertEqual((p["cpu"]["value"], p["memory"]["value"], p["memory"]["unit"]), (40.0, 500, "MB"))
        self.assertNotIn("image", p)  # descriptive, not something to automate on
        self.assertNotIn("cpu", self.hub.get("docker.updater").attrs)  # stopped: not read

    async def test_commands(self):
        await self.d._list()
        await self.d.command("docker.plex", "restart")
        self.assertEqual(self.fake.posts, [f"/containers/{CONTAINERS[0]['Id']}/restart"])
        await self.d.handle(ev("die", "plex", exitCode="137"))  # the restart's own stop isn't a crash
        await asyncio.sleep(0)
        self.assertEqual(self.events, [])
        self.fake.read_only = True
        with self.assertRaisesRegex(CommandError, "read-only"):
            await self.d.command("docker.plex", "stop")


def only(*names):
    return [c for c in CONTAINERS if c["Names"][0][1:] in names]


class GoneContainerTests(unittest.IsolatedAsyncioTestCase):
    """Containers Docker no longer has go from Newman; throwaway ones never show up."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.fake = FakeDocker()
        self.t = 1_000_000_000.0
        self.removed = []

        async def added(ent):
            pass

        async def removed(ids):
            self.removed.extend(ids)
        self.hub.subscribe_registry(added, removed)
        self.used = {}  # entity id -> what uses it
        self.d = self._make({"address": "http://proxy:2375"})

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def _make(self, config):
        d = DockerIntegration("docker", "Docker", config, Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        d._http = httpx.AsyncClient(transport=httpx.MockTransport(self.fake), base_url="http://proxy:2375")
        d._now = lambda: self.t
        d.ctx.in_use = lambda eid: self.used.get(eid, [])
        return d

    async def _settle(self):
        for _ in range(3):
            await asyncio.sleep(0)

    def ids(self):
        return {e.id for e in self.hub.all()}

    async def test_stopped_stays_deleted_goes_after_grace(self):
        await self.d._list()
        self.fake.containers = only("plex", "updater")  # immich_server deleted; updater only stopped
        await self.d._list()
        self.assertFalse(self.hub.get("docker.immich_server").available)  # missing: unavailable for now
        self.t += GONE_GRACE_S - 1
        await self.d._list()
        self.assertIn("docker.immich_server", self.ids())
        self.t += 2
        await self.d._list()
        await self._settle()
        self.assertEqual(self.ids(), {"docker.plex", "docker.updater"})
        self.assertEqual(self.removed, ["docker.immich_server"])
        upd = self.hub.get("docker.updater")
        self.assertEqual((upd.state, upd.available), ("stopped", True))
        self.assertEqual(self.d.detail, "2 containers · 1 running")

    async def test_used_device_is_kept_as_removed(self):
        await self.d._list()
        self.used["docker.immich_server"] = ["automation", "dashboard"]
        self.fake.containers = only("plex", "updater")
        await self.d._list()
        self.t += GONE_GRACE_S
        with self.assertLogs("newman.integrations.docker", "INFO") as logs:
            await self.d._list()
            await self.d._list()
        self.assertEqual(sum("kept as Removed" in m for m in logs.output), 1)  # said once
        e = self.hub.get("docker.immich_server")
        self.assertEqual((e.available, KINDS["container"].label_for(e.state, e.attrs)), (False, "Removed"))
        # Recreated under the same name: back as itself.
        self.fake.containers = None
        await self.d._list()
        e = self.hub.get("docker.immich_server")
        self.assertEqual((e.available, KINDS["container"].label_for(e.state, e.attrs)), (True, "Running"))
        # Gone again and nothing uses it any more: removed once the grace is up.
        self.used.clear()
        self.fake.containers = only("plex", "updater")
        await self.d._list()
        self.t += GONE_GRACE_S
        await self.d._list()
        self.assertNotIn("docker.immich_server", self.ids())

    async def test_nothing_removed_while_docker_is_unreachable(self):
        await self.d._list()
        self.fake.down = True
        self.t += GONE_GRACE_S * 10
        with self.assertRaises(httpx.ConnectError):
            await self.d._list()
        self.assertEqual(len(self.ids()), 3)
        # Back, without immich_server: its grace starts now, not when Docker went away.
        self.fake.down, self.fake.containers = False, only("plex", "updater")
        await self.d._list()
        self.assertIn("docker.immich_server", self.ids())
        self.t += GONE_GRACE_S
        await self.d._list()
        self.assertNotIn("docker.immich_server", self.ids())

    async def test_short_lived_container_never_appears(self):
        young = {"Id": "eee555" * 4, "Names": ["/amazing_franklin"], "Image": "alpine", "State": "running",
                 "Status": "Up 1 second", "Created": self.t - 5}
        self.fake.containers = [*CONTAINERS, young]
        await self.d._list()
        await self.d.handle({"Type": "container", "Action": "start",
                             "Actor": {"ID": young["Id"], "Attributes": {"name": "amazing_franklin"}}})
        self.assertNotIn("docker.amazing_franklin", self.ids())
        self.assertEqual(self.d._young_until, self.t - 5 + YOUNG_S)
        self.assertEqual(self.d.detail, "3 containers · 2 running")
        self.t += 30
        self.fake.containers = None  # done and gone (--rm) within the two minutes
        await self.d._list()
        self.assertNotIn("docker.amazing_franklin", self.ids())
        self.assertIsNone(self.d._young_until)
        # One that keeps running shows up once it's two minutes old.
        self.fake.containers = [*CONTAINERS, young]
        self.t = young["Created"] + YOUNG_S
        await self.d._list()
        self.assertEqual(self.hub.get("docker.amazing_franklin").state, "running")

    async def test_throwaway_containers_never_appear(self):
        old = self.t - YOUNG_S * 10  # long-running builds and test runs, well past the two minutes
        build = {"Id": "fff666" * 4, "Names": ["/newman-android-build"], "Image": "gradle", "State": "running",
                 "Status": "Up 9 minutes", "Created": old}
        tests = {"Id": "ggg777" * 4, "Names": ["/jolly_allen"], "Image": "node", "State": "running",
                 "Status": "Up 4 minutes", "Created": old, "Labels": {"newman.ignore": "true"}}
        self.fake.auto_remove.add(build["Id"])
        self.fake.containers = [*CONTAINERS, build, tests]
        await self.d._list()
        await self.d._list()
        self.assertEqual(self.ids(), {"docker.plex", "docker.immich_server", "docker.updater"})
        # A container already shown that is later recreated with --rm stays a device.
        self.fake.auto_remove.add(CONTAINERS[0]["Id"])
        await self.d._list()
        self.assertIn("docker.plex", self.ids())

    async def test_startup_removes_what_is_already_gone(self):
        await self.d._list()
        self.used["docker.updater"] = ["phone layout"]
        # Docker listing nothing on startup may be Docker starting: nothing goes at once.
        self.fake.containers = []
        await self._make({"address": "http://proxy:2375"})._list()
        self.assertEqual(len(self.ids()), 3)
        # Starting with Docker answering: gone and unused -> removed straight away; used -> kept.
        self.fake.containers = only("plex")
        await self._make({"address": "http://proxy:2375"})._list()
        await self._settle()
        self.assertEqual(self.ids(), {"docker.plex", "docker.updater"})
        e = self.hub.get("docker.updater")
        self.assertEqual(KINDS["container"].label_for(e.state, e.attrs), "Removed")
        self.assertEqual(self.removed, ["docker.immich_server"])


class UsesTests(unittest.TestCase):
    def test_what_uses_a_device(self):
        from types import SimpleNamespace as NS
        from newman import replace

        class Store:
            PHONE_DEFAULT = "default"

            def dashboards(self):
                return [{"id": "d1", "userId": "u1", "tabs": [{"tiles": [{"entity": "docker.plex"}]}]}]

            def phone_layout_times(self):
                return {"u2": 1}

            def phone_layout(self, uid):
                return {"layout": {"pages": [{"tiles": [{"entity": "docker.sonarr"}]}]}} if uid == "u2" else None

        h = NS(engine=NS(autos={"a1": {"trigger": {"items": [{"entity": "docker.plex"}]}}}), store=Store(),
               widget_push=NS(showing=lambda eid: eid == "docker.radarr"))
        self.assertEqual(replace.uses(h, "docker.plex"), ["automation", "dashboard"])
        self.assertEqual(replace.uses(h, "docker.sonarr"), ["phone layout"])
        self.assertEqual(replace.uses(h, "docker.radarr"), ["widget"])
        self.assertEqual(replace.uses(h, "docker.gone"), [])
