"""Server health, offline: Unraid (fake GraphQL API), Linux hosts (fake Glances and node_exporter),
Proxmox (fake REST API) - readings, warning states, offline / back, sustained CPU, disk alerts,
parity checks, VM commands."""
import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

import httpx

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations import REGISTRY  # noqa: E402
from newman.integrations.base import CommandError, Context  # noqa: E402
from newman.integrations.linux_host import LinuxHostIntegration, parse_metrics, smart_reading, uptime_days  # noqa: E402
from newman.integrations.proxmox import ProxmoxIntegration  # noqa: E402
from newman.integrations.unraid import UnraidIntegration  # noqa: E402
from newman.kinds import KINDS, properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402

KB = 1024 ** 2  # 1 GiB in KB


def adisk(name, idx, status="DISK_OK", temp=35, size=4000, used=1000, spinning=True, kind="DATA", errors=0):
    return {"idx": idx, "name": name, "device": f"sd{chr(97 + idx)}", "size": size * KB, "status": status,
            "temp": temp, "numErrors": errors, "fsSize": None if kind == "PARITY" else size * KB,
            "fsFree": None if kind == "PARITY" else (size - used) * KB, "fsUsed": None if kind == "PARITY" else used * KB,
            "type": kind, "warning": 70, "critical": 90, "isSpinning": spinning}


UNRAID = {"data": {
    "info": {"os": {"hostname": "Tower", "distro": "Unraid OS", "release": "7.2.3", "uptime": 864000, "kernel": "6.12"},
             "versions": {"core": {"unraid": "7.2.3"}}},
    "metrics": {"cpu": {"percentTotal": 12.34}, "memory": {"total": "68719476736", "used": "17179869184",
                                                         "percentTotal": 25.0}},
    "array": {"state": "STARTED", "capacity": {"kilobytes": {"free": "6000000", "used": "2000000", "total": "8000000"}},
              "parityCheckStatus": {"status": "COMPLETED", "progress": 100, "errors": 0, "running": False,
                                    "paused": False, "correcting": False, "date": "2026-10-01T03:00:00Z", "duration": 50000},
              "parities": [adisk("parity", 0, kind="PARITY")],
              "disks": [adisk("disk1", 1), adisk("disk2", 2, spinning=False, temp=None), adisk("disk3", 3, status="DISK_NP")],
              "caches": [adisk("cache", 4, size=1000, used=500, kind="CACHE", temp=40)]}}}
DOCKER = {"data": {"docker": {"containers": [{"state": "RUNNING"}, {"state": "RUNNING"}, {"state": "EXITED"}]}}}


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Hub()
        self.events = []
        self.clock = 1000.0

        async def on_event(ent, event, data):
            self.events.append((ent.id, event, data.get("detail")))
        self.hub.subscribe_events(on_event)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def make(self, cls, config, handler, base_url):
        i = cls(cls.type, cls.name, config, Context(self.hub, Path(self.tmp.name), Path(self.tmp.name)))
        i._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url,
                                    headers=dict(i._http.headers))
        i.now = lambda: self.clock
        return i

    async def settle(self):
        for _ in range(3):
            await asyncio.sleep(0)

    def props(self, eid):
        return {p["id"]: p["value"] for p in properties_for(self.hub.get(eid))}


class FakeUnraid:
    def __init__(self):
        self.data = copy.deepcopy(UNRAID)
        self.down = False
        self.keys = []
        self.queries = []

    def __call__(self, req):
        if self.down:
            raise httpx.ConnectError("refused")
        self.keys.append(req.headers.get("x-api-key"))
        if req.headers.get("x-api-key") != "k3y":
            return httpx.Response(200, json={"errors": [{"message": "Invalid API key",
                                                        "extensions": {"code": "UNAUTHENTICATED"}}], "data": None})
        q = json.loads(req.content)["query"]
        self.queries.append(q)
        if "NewmanDocker" in q:
            return httpx.Response(200, json=DOCKER)
        if "NewmanDisks" in q:
            return httpx.Response(200, json={"data": {"disks": [{"device": "sdb", "smartStatus": "OK", "temperature": 33}]}})
        return httpx.Response(200, json=self.data)


class UnraidTests(Base):
    def unraid(self, **cfg):
        self.fake = FakeUnraid()
        return self.make(UnraidIntegration, {"address": "http://tower", "api_key": "k3y", **cfg}, self.fake, "http://tower")

    async def test_registered_and_kinds(self):
        for t in ("unraid", "linux_host", "proxmox"):
            self.assertIn(t, REGISTRY)
            self.assertEqual(REGISTRY[t].category, "servers")
        self.assertTrue(KINDS["vm"].command("shutdown").sensitive)
        self.assertTrue(KINDS["vm"].command("stop").sensitive)
        self.assertFalse(KINDS["vm"].command("start").sensitive)
        for k in ("server", "disk", "vm"):
            self.assertEqual(KINDS[k].category, "servers")

    async def test_reads_server_array_and_disks(self):
        u = self.unraid()
        await u.poll()
        await self.settle()
        s = self.hub.get("unraid.server")
        self.assertEqual((s.kind, s.name, s.state), ("server", "Tower", "online"))
        p = self.props("unraid.server")
        self.assertEqual((p["cpu"], p["memoryPercent"], p["uptimeDays"], p["version"]), (12.3, 25.0, 10.0, "7.2.3"))
        self.assertEqual((p["arrayState"], p["parityCheck"], p["parityErrors"], p["parityResult"]),
                         ("started", "idle", 0, "completed"))
        self.assertEqual((p["containersRunning"], p["arrayUsedPercent"]), (2, 25.0))
        self.assertEqual(self.fake.keys[0], "k3y")
        d1 = self.hub.get("unraid.disk_disk1")
        self.assertEqual((d1.kind, d1.name, d1.state), ("disk", "Tower Disk1", "ok"))
        self.assertEqual(self.props("unraid.disk_disk1")["usedPercent"], 25.0)
        self.assertEqual(self.hub.get("unraid.disk_disk2").state, "standby")
        self.assertIsNone(self.hub.get("unraid.disk_disk3"))  # empty slot
        self.assertEqual(self.hub.get("unraid.disk_parity").attrs.get("usedPercent"), None)
        self.assertEqual(u.detail, "4 disks · array started")
        self.assertFalse(any("NewmanDisks" in q for q in self.fake.queries))  # SMART off: never wakes disks
        self.assertEqual(self.events, [])

    async def test_smart_opt_in(self):
        u = self.unraid(smart=True)
        await u.poll()
        self.assertEqual(self.hub.get("unraid.disk_disk1").attrs["smart"], "passed")

    async def test_bad_key(self):
        u = self.unraid(api_key="nope")
        u.config["api_key"] = "nope"
        u._http.headers["x-api-key"] = "nope"
        with self.assertRaisesRegex(RuntimeError, "API key"):
            await u.poll()

    async def test_failures_parity_and_offline(self):
        u = self.unraid()
        await u.poll()
        arr = self.fake.data["data"]["array"]
        arr["disks"][0].update(status="DISK_DSBL")
        arr["caches"][0].update(fsFree=50 * KB, fsUsed=950 * KB, temp=60)
        arr["parityCheckStatus"].update(status="RUNNING", running=True, progress=42.5)
        await u.poll()
        await self.settle()
        self.assertEqual(self.hub.get("unraid.disk_disk1").state, "failed")
        self.assertEqual(self.hub.get("unraid.disk_cache").state, "warning")
        s = self.hub.get("unraid.server")
        self.assertEqual(s.state, "warning")
        self.assertIn("Disk1 failed", s.attrs["problem"])
        self.assertEqual(s.attrs["parityProgress"], 42.5)
        self.assertIn("parity check 42.5%", u.detail)
        ev = {(e, n) for e, n, _ in self.events}
        self.assertTrue({("unraid.disk_disk1", "failed"), ("unraid.disk_cache", "almost_full"),
                         ("unraid.disk_cache", "too_hot"), ("unraid.server", "parity_started")} <= ev)
        self.events.clear()
        await u.poll()  # still the same: no repeats
        await self.settle()
        self.assertEqual(self.events, [])
        arr["parityCheckStatus"].update(status="COMPLETED", running=False, errors=3)
        arr["state"] = "STOPPED"
        await u.poll()
        await self.settle()
        self.assertIn(("unraid.server", "parity_finished", "completed, 3 errors"), self.events)
        self.assertIn(("unraid.server", "array_stopped", "stopped"), self.events)
        # Unreachable: one miss is a blip, the second is offline; then back.
        self.events.clear()
        self.fake.down = True
        for _ in range(2):
            try:
                await u.poll()
            except httpx.HTTPError as e:
                await u.unreachable(e)
        await self.settle()
        self.assertEqual(self.hub.get("unraid.server").state, "offline")
        self.assertFalse(self.hub.get("unraid.disk_disk1").available)
        self.assertEqual([e[1] for e in self.events], ["offline"])
        self.fake.down = False
        await u.poll()
        await self.settle()
        self.assertEqual(self.events[-1][:2], ("unraid.server", "online"))
        self.assertTrue(self.hub.get("unraid.disk_disk1").available)

    async def test_sustained_cpu(self):
        u = self.unraid(sustained_min=10)
        m = self.fake.data["data"]["metrics"]
        m["cpu"]["percentTotal"] = 97
        await u.poll()
        self.clock += 300
        await u.poll()
        await self.settle()
        self.assertEqual(self.hub.get("unraid.server").state, "online")  # high, but not for long enough
        self.clock += 301
        await u.poll()
        await self.settle()
        self.assertEqual(self.hub.get("unraid.server").state, "warning")
        self.assertEqual(self.events, [("unraid.server", "high_cpu", "97%")])
        self.clock += 600
        await u.poll()
        await self.settle()
        self.assertEqual(len(self.events), 1)
        m["cpu"]["percentTotal"] = 10
        await u.poll()
        self.assertEqual(self.hub.get("unraid.server").state, "online")


GLANCES = {
    "system": {"os_name": "Linux", "hostname": "pihole", "platform": "64bit", "os_version": "6.1.0-rpi7",
               "linux_distro": "Debian GNU/Linux 12 (bookworm)"},
    "cpu": {"total": 7.5}, "load": {"min1": 0.42, "min5": 0.3, "min15": 0.2, "cpucore": 4},
    "mem": {"percent": 41.2, "total": 4e9, "used": 1.6e9},
    "sensors": [{"label": "cpu_thermal 0", "value": 52.1, "unit": "C", "type": "temperature_core"},
                {"label": "fan", "value": 1200, "unit": "R", "type": "fan_speed"}],
    "fs": [{"device_name": "/dev/mmcblk0p2", "fs_type": "ext4", "mnt_point": "/", "size": 64e9, "used": 60e9,
            "free": 4e9, "percent": 93.8},
           {"device_name": "/dev/loop0", "fs_type": "squashfs", "mnt_point": "/snap/core/1", "size": 1e8, "used": 1e8,
            "free": 0, "percent": 100},
           {"device_name": "/dev/sda1", "fs_type": "ext4", "mnt_point": "/mnt/data", "size": 1e12, "used": 1e11,
            "free": 9e11, "percent": 10.0}],
    "uptime": "13 days, 2:24:00",
    "smart": {"sda": {"DeviceName": "WD Red", "9": {"name": "Power_On_Hours", "raw": 12000, "value": 90, "threshold": 0},
                      "194": {"name": "Temperature_Celsius", "raw": "38 (Min/Max 20/45)", "value": 62, "threshold": 0},
                      "5": {"name": "Reallocated_Sector_Ct", "raw": 8, "value": 100, "threshold": 10}}},
}

NODE_METRICS = """# HELP node_load1 1m load average.
node_load1 0.5
node_memory_MemTotal_bytes 8e+09
node_memory_MemAvailable_bytes 6e+09
node_boot_time_seconds 1000000
node_time_seconds 1086400
node_cpu_seconds_total{cpu="0",mode="idle"} %s
node_cpu_seconds_total{cpu="0",mode="user"} %s
node_hwmon_temp_celsius{chip="platform_coretemp_0",sensor="temp1"} 48
node_uname_info{domainname="(none)",machine="x86_64",nodename="nuc",release="6.8.0-45-generic",sysname="Linux"} 1
node_os_info{id="ubuntu",pretty_name="Ubuntu 24.04.1 LTS"} 1
node_filesystem_size_bytes{device="/dev/nvme0n1p2",fstype="ext4",mountpoint="/"} 1e+11
node_filesystem_avail_bytes{device="/dev/nvme0n1p2",fstype="ext4",mountpoint="/"} 4e+10
node_filesystem_free_bytes{device="/dev/nvme0n1p2",fstype="ext4",mountpoint="/"} 5e+10
node_filesystem_size_bytes{device="tmpfs",fstype="tmpfs",mountpoint="/run"} 1e+09
apt_upgrades_pending{arch="amd64",origin="Ubuntu"} 3
"""


class LinuxTests(Base):
    async def test_glances(self):
        def handler(req):
            if not req.url.path.startswith("/api/3/"):  # an older Glances: v3 only
                return httpx.Response(404)
            key = req.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=GLANCES[key]) if key in GLANCES else httpx.Response(404)
        g = self.make(LinuxHostIntegration, {"address": "http://pi:61208"}, handler, "http://pi:61208")
        await g.poll()
        await self.settle()
        p = self.props("linux_host.host")
        self.assertEqual(self.hub.get("linux_host.host").name, "pihole")
        self.assertEqual((p["cpu"], p["load"], p["memoryPercent"], p["temperature"], p["uptimeDays"]),
                         (7.5, 0.42, 41.2, 52.1, 13.1))
        self.assertEqual(p["temps.cpu_thermal 0"], 52.1)
        self.assertEqual(p["os"], "Debian GNU/Linux 12 (bookworm)")
        root = self.hub.get("linux_host.fs_root")
        self.assertEqual((root.state, root.attrs["free"], root.name), ("warning", 4.0, "pihole /"))
        self.assertEqual(self.hub.get("linux_host.fs_mnt_data").state, "ok")
        self.assertFalse(any("snap" in e.id for e in self.hub.all()))
        sda = self.hub.get("linux_host.smart_sda")
        self.assertEqual((sda.state, sda.attrs["powerOnHours"], sda.attrs["temperature"], sda.attrs["smart"]),
                         ("warning", 12000, 38.0, "warning"))
        self.assertEqual(self.hub.get("linux_host.host").state, "warning")  # its disks need a look
        self.assertEqual(g.detail, "pihole · 2 filesystems")
        self.assertEqual(self.events, [])  # already full when Newman started: not news

    def test_helpers(self):
        self.assertEqual([uptime_days(x) for x in ("2:24:00", "1 day, 0:00:00", 86400, "junk")], [0.1, 1.0, 1.0, None])
        self.assertEqual(smart_reading({"x": {"name": "Reallocated_Sector_Ct", "raw": 0, "value": 5, "threshold": 10}})["smart"],
                         "failed")
        self.assertEqual(parse_metrics('a{b="c"} 1.5\n# x\nd 2'), [("a", {"b": "c"}, 1.5), ("d", {}, 2.0)])

    async def test_node_exporter(self):
        sample = {"idle": 1000, "user": 1000}

        def handler(req):
            return httpx.Response(200, text=NODE_METRICS % (sample["idle"], sample["user"]))
        n = self.make(LinuxHostIntegration, {"address": "http://nuc:9100", "method": "node_exporter"}, handler,
                      "http://nuc:9100")
        await n.poll()
        sample.update(idle=1060, user=1040)  # 100 s of CPU, 60 idle -> 40 %
        await n.poll()
        p = self.props("linux_host.host")
        self.assertEqual(self.hub.get("linux_host.host").name, "nuc")
        self.assertEqual((p["cpu"], p["load"], p["memoryPercent"], p["temperature"], p["uptimeDays"]),
                         (40.0, 0.5, 25.0, 48.0, 1.0))
        self.assertEqual((p["updateAvailable"], p["updates"], p["os"]), (True, 3, "Ubuntu 24.04.1 LTS"))
        root = self.props("linux_host.fs_root")
        self.assertEqual((root["usedPercent"], root["free"], root["size"]), (50.0, 40.0, 100.0))
        self.assertIsNone(self.hub.get("linux_host.fs_run"))


class FakeProxmox:
    def __init__(self):
        self.posts = []
        self.auth = []
        self.pve2_status = "online"
        self.read_only = False

    def __call__(self, req):
        self.auth.append(req.headers.get("authorization"))
        p = req.url.path.replace("/api2/json", "")
        if req.method == "POST":
            if self.read_only:
                return httpx.Response(403, json={"data": None})
            self.posts.append(p)
            return httpx.Response(200, json={"data": "UPID:pve1:..."})
        data = {
            "/nodes": [{"node": "pve1", "status": "online", "cpu": 0.05, "maxcpu": 8, "mem": 4e9, "maxmem": 16e9, "uptime": 172800},
                       {"node": "pve2", "status": self.pve2_status}],
            "/nodes/pve1/status": {"cpu": 0.1234, "loadavg": ["0.80", "0.60", "0.50"],
                                   "memory": {"total": 16e9, "used": 8e9, "free": 8e9}, "uptime": 172800,
                                   "pveversion": "pve-manager/8.2.4/faa83925c9641325", "kversion": "Linux 6.8.8-2-pve"},
            "/nodes/pve2/status": {"cpu": 0.01, "loadavg": ["0.1"], "memory": {"total": 8e9, "used": 1e9}, "uptime": 100},
            "/nodes/pve1/storage": [{"storage": "local-lvm", "type": "lvmthin", "total": 1e12, "used": 9.5e11,
                                     "avail": 5e10, "active": 1, "enabled": 1},
                                    {"storage": "nfs-off", "type": "nfs", "total": 0, "used": 0, "active": 0, "enabled": 0}],
            "/nodes/pve1/disks/list": [{"devpath": "/dev/nvme0n1", "model": "Samsung 980", "size": 1e12, "health": "PASSED",
                                        "wearout": 97, "type": "nvme"},
                                       {"devpath": "/dev/sda", "model": "Old HDD", "size": 2e12, "health": "FAILED",
                                        "wearout": "N/A", "type": "hdd"}],
            "/nodes/pve1/apt/update": [{"Package": "pve-manager"}, {"Package": "openssl"}],
            "/cluster/resources": [
                {"id": "qemu/100", "vmid": 100, "name": "homeassistant", "node": "pve1", "type": "qemu", "status": "running",
                 "cpu": 0.031, "maxcpu": 2, "mem": 2147483648, "maxmem": 4294967296, "uptime": 86400, "template": 0},
                {"id": "lxc/101", "vmid": 101, "name": "pihole", "node": "pve1", "type": "lxc", "status": "stopped",
                 "cpu": 0, "mem": 0, "maxmem": 536870912, "uptime": 0},
                {"id": "qemu/9000", "vmid": 9000, "name": "tmpl", "node": "pve1", "type": "qemu", "status": "stopped",
                 "template": 1}],
        }
        if p.startswith("/nodes/pve2/") and p != "/nodes/pve2/status":
            return httpx.Response(403, json={"data": None})
        return httpx.Response(200, json={"data": data[p]}) if p in data else httpx.Response(404, json={"data": None})


class ProxmoxTests(Base):
    def pve(self):
        self.fake = FakeProxmox()
        return self.make(ProxmoxIntegration, {"address": "https://pve:8006", "token_id": "newman@pve!mon",
                                              "token_secret": "s3cr3t"}, self.fake, "https://pve:8006/api2/json")

    async def test_nodes_storage_disks_guests(self):
        x = self.pve()
        await x.poll()
        await self.settle()
        self.assertEqual(self.fake.auth[0], "PVEAPIToken=newman@pve!mon=s3cr3t")
        p = self.props("proxmox.node_pve1")
        self.assertEqual((p["cpu"], p["load"], p["memoryPercent"], p["uptimeDays"], p["updates"], p["updateAvailable"]),
                         (12.3, 0.8, 50.0, 2.0, 2, True))
        self.assertTrue(p["version"].startswith("pve-manager/8.2.4"))
        self.assertEqual(self.hub.get("proxmox.storage_pve1_local_lvm").state, "warning")  # 95 % full
        self.assertIsNone(self.hub.get("proxmox.storage_pve1_nfs_off"))
        self.assertEqual(self.hub.get("proxmox.disk_pve1_sda").state, "failed")
        nv = self.props("proxmox.disk_pve1_nvme0n1")
        self.assertEqual((nv["smart"], nv["lifeLeft"]), ("passed", 97.0))
        self.assertEqual(self.hub.get("proxmox.node_pve1").state, "warning")
        self.assertEqual(self.hub.get("proxmox.node_pve2").state, "online")  # can't read its disks: fine
        ha = self.hub.get("proxmox.vm_100")
        self.assertEqual((ha.kind, ha.name, ha.state), ("vm", "homeassistant", "running"))
        self.assertEqual(self.props("proxmox.vm_100")["memoryPercent"], 50.0)
        self.assertEqual(ha.attrs["vmType"], "VM")
        self.assertEqual(self.hub.get("proxmox.vm_101").state, "stopped")
        self.assertIsNone(self.hub.get("proxmox.vm_9000"))
        self.assertEqual(x.detail, "2 nodes · 2 online · 2 guests")
        self.assertEqual(self.events, [])

    async def test_node_goes_offline_and_commands(self):
        x = self.pve()
        await x.poll()
        self.fake.pve2_status = "offline"
        await x.poll()
        await self.settle()
        self.assertEqual(self.hub.get("proxmox.node_pve2").state, "offline")
        self.assertIn(("proxmox.node_pve2", "offline", "the cluster reports it offline"), self.events)
        await x.command("proxmox.vm_101", "start")
        await x.command("proxmox.vm_100", "shutdown")
        self.assertEqual(self.fake.posts, ["/nodes/pve1/lxc/101/status/start", "/nodes/pve1/qemu/100/status/shutdown"])
        with self.assertRaises(CommandError):
            await x.command("proxmox.vm_100", "explode")
        with self.assertRaises(CommandError):
            await x.command("proxmox.node_pve1", "start")
        self.fake.read_only = True
        with self.assertRaisesRegex(CommandError, "PVEVMUser"):
            await x.command("proxmox.vm_100", "reboot")


if __name__ == "__main__":
    unittest.main()
