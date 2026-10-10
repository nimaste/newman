"""Live end-to-end scenarios against a running hub and the REAL devices.

    python3 live_scenarios.py [http://10.1.1.2:8093]

Uses test phones (their notifications land in /api/test/outbox) for two
people, so no mobile app is needed. It really unlocks and locks the doors -
only run it when someone is home. Every scenario ends with the doors locked.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8093").rstrip("/")
RESULTS = []


def call(method, path, body=None, expect=200):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    if expect is not None and status != expect:
        raise AssertionError(f"{method} {path} -> {status}: {raw[:300]!r}")
    ctype_json = raw[:1] in (b"{", b"[")
    return (json.loads(raw) if ctype_json else raw), status


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("  PASS " if ok else "  FAIL ") + name + (f" - {detail}" if detail else ""))


def wait_state(eid, state, timeout=90, required=True):
    # Ring can take up to ~45 s to carry out a command (seen live), so be patient.
    t0 = time.time()
    while time.time() - t0 < timeout:
        ent = next(e for e in call("GET", "/api/entities")[0] if e["id"] == eid)
        if ent["state"] == state:
            return time.time() - t0
        time.sleep(1)
    if required:
        raise AssertionError(f"{eid} never became {state}")
    return None


def outbox(device, kind=None, since=0):
    msgs = call("GET", f"/api/test/outbox?deviceId={device}")[0]
    return [m for m in msgs if m["ts"] > since and (kind is None or m["kind"] == kind)]


def wait_outbox(device, kind, since, timeout=20, pred=lambda m: True):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hits = [m for m in outbox(device, kind, since) if pred(m)]
        if hits:
            return hits[0]
        time.sleep(0.5)
    return None


def tap(msg, button, device):
    return call("POST", "/api/public/actions", {"button": button, "subject": msg["action_subject"], "deviceId": device,
                                                 "exp": msg["action_exp"], "token": msg["action_token"]}, expect=None)


def main():
    print(f"Newman live scenarios against {BASE}\n")
    call("DELETE", "/api/test/outbox")
    locks = {e["name"]: e["id"] for e in call("GET", "/api/entities?kind=lock")[0]}
    side, laundry = locks["Side Door"], locks["Laundry"]
    print(f"Locks: {locks}")

    # -- setup: people, test phones, automations from the recipes -------------------
    people = {p["name"]: p["id"] for p in call("GET", "/api/people")[0]}
    for name in ("Chris", "Marlina"):
        if name not in people:
            people[name] = call("POST", "/api/people", {"name": name})[0]["id"]
    phones = {}
    for name in ("Chris", "Marlina"):
        phones[name] = call("POST", "/api/test/phones", {"personId": people[name], "name": f"{name} (test phone)"})[0]["deviceId"]
    for a in call("GET", "/api/automations")[0]:
        call("DELETE", f"/api/automations/{a['id']}")
    tmpl = {t["id"]: t["automation"] for t in call("GET", "/api/automations/templates")[0]}
    nightly = call("POST", "/api/automations", {**tmpl["nightly-lock-check"],
                                                "recipients": [people["Chris"], people["Marlina"]]})[0]
    activity = call("POST", "/api/automations", {**tmpl["lock-activity"], "recipients": [people["Chris"]]})[0]
    doorbell = call("POST", "/api/automations", {**tmpl["doorbell-alert"], "recipients": [people["Chris"], people["Marlina"]]})[0]
    print("Nightly:", call("GET", f"/api/automations/{nightly['id']}")[0]["summary"], "\n")

    for name in (side, laundry):
        ent = next(e for e in call("GET", "/api/entities")[0] if e["id"] == name)
        if ent["state"] != "LOCKED":
            call("POST", f"/api/entities/{name}/command", {"command": "lock"})
            wait_state(name, "LOCKED")

    # -- A: everything locked -> nothing sent ------------------------------------------
    print("Scenario A - 9 PM check with everything locked")
    t = time.time()
    run = call("POST", f"/api/automations/{nightly['id']}/run", {})[0]
    check("run is skipped", run["status"] == "skipped", run["log"][-1]["text"] if run["log"] else "")
    check("no notification sent", not outbox(phones["Chris"], "automation", t))

    # -- B: Laundry unlocked -> Marlina locks it from the notification ------------------
    print("\nScenario B - Laundry left unlocked, Marlina taps Lock Laundry")
    t = time.time()
    call("POST", f"/api/entities/{laundry}/command", {"command": "unlock"})
    secs = wait_state(laundry, "UNLOCKED")
    check("real lock unlocked", True, f"{secs:.1f}s")
    m = wait_outbox(phones["Chris"], "automation", t, pred=lambda m: m.get("automation_id") == activity["id"])
    check("lock-activity alert to Chris", bool(m), m and f"{m['title']} / {m['body']}")
    t = time.time()
    call("POST", f"/api/automations/{nightly['id']}/run", {})
    mm = wait_outbox(phones["Marlina"], "automation", t, pred=lambda m: m.get("automation_id") == nightly["id"])
    mc = wait_outbox(phones["Chris"], "automation", t, pred=lambda m: m.get("automation_id") == nightly["id"])
    labels = [b["label"] for b in json.loads(mm["buttons"])] if mm else []
    check("reminder sent to both", bool(mm and mc), mm and f"{mm['title']} / {mm['body']}")
    check("only Laundry's buttons shown", labels == ["Lock Laundry", "Snooze 1 hour", "Dismiss"], str(labels))
    res, st = tap(mm, f"lock:{side}", phones["Marlina"])
    check("button not in the notification is refused", st == 409, str(res))
    res, st = tap({**mm, "action_token": mm["action_token"][:-2] + "xx"}, f"lock:{laundry}", phones["Marlina"])
    check("tampered token is refused", st == 401)
    res, st = tap(mm, f"lock:{laundry}", phones["Chris"])
    check("Marlina's token can't be used from Chris's phone", st == 401)
    t = time.time()
    res, st = tap(mm, f"lock:{laundry}", phones["Marlina"])
    check("Marlina taps Lock Laundry", st == 200, str(res))
    secs = wait_state(laundry, "LOCKED")
    check("real lock locked", True, f"{secs:.1f}s after the tap")
    fc = wait_outbox(phones["Chris"], "followup", t)
    fm = wait_outbox(phones["Marlina"], "followup", t)
    check("follow-up to both", bool(fc and fm), fc and fc["body"])
    check("follow-up names Marlina", bool(fc) and fc["body"] == "Marlina locked Laundry.")
    ma = wait_outbox(phones["Chris"], "automation", t, pred=lambda m: m.get("automation_id") == activity["id"])
    check("lock-activity says who locked it", bool(ma) and "by Marlina" in ma["body"], ma and f"{ma['title']} / {ma['body']}")
    runs = call("GET", f"/api/runs?automationId={nightly['id']}")[0]
    check("run resolved", runs[0]["status"] == "resolved", " | ".join(l["text"] for l in runs[0]["log"]))

    # -- C: both unlocked -> Chris taps Lock all (needs Side Door to accept remote unlock) --
    print("\nScenario C - both doors unlocked, Chris taps Lock all")
    call("POST", f"/api/entities/{side}/command", {"command": "unlock"})
    side_ok = wait_state(side, "UNLOCKED", timeout=40, required=False)
    call("POST", f"/api/entities/{laundry}/command", {"command": "unlock"})
    wait_state(laundry, "UNLOCKED")
    t = time.time()
    call("POST", f"/api/automations/{nightly['id']}/run", {})
    mc = wait_outbox(phones["Chris"], "automation", t, pred=lambda m: m.get("automation_id") == nightly["id"])
    if side_ok is None:
        print("  NOTE Side Door refused the remote unlock again - running the Dismiss path with Laundry only")
        check("reminder names only Laundry", bool(mc) and mc["body"] == "Laundry is still unlocked.", mc and mc["body"])
        res, st = tap(mc, "dismiss", phones["Chris"])
        check("Chris taps Dismiss", st == 200)
        runs = call("GET", f"/api/runs?automationId={nightly['id']}")[0]
        check("run dismissed", runs[0]["status"] == "dismissed")
        t = time.time()
        call("POST", f"/api/entities/{laundry}/command", {"command": "lock"})
        wait_state(laundry, "LOCKED")
        time.sleep(3)
        check("no follow-up after a dismissed reminder", not outbox(phones["Marlina"], "followup", t))
    else:
        labels = [b["label"] for b in json.loads(mc["buttons"])] if mc else []
        check("reminder lists both doors", bool(mc) and mc["body"] == "Laundry and Side Door are still unlocked.", mc and mc["body"])
        check("buttons", labels == ["Lock Laundry", "Lock Side Door", "Lock all", "Snooze 1 hour", "Dismiss"], str(labels))
        t = time.time()
        res, st = tap(mc, "lock_all", phones["Chris"])
        check("Chris taps Lock all", st == 200, str(res))
        a, b = wait_state(side, "LOCKED"), wait_state(laundry, "LOCKED")
        check("both real locks locked", True, f"{max(a, b):.1f}s")
        fm = wait_outbox(phones["Marlina"], "followup", t)
        check("follow-up text", bool(fm) and fm["body"] == "Chris locked Laundry and Side Door.", fm and fm["body"])
    for eid in (side, laundry):
        if next(e for e in call("GET", "/api/entities")[0] if e["id"] == eid)["state"] != "LOCKED":
            call("POST", f"/api/entities/{eid}/command", {"command": "lock"})
            wait_state(eid, "LOCKED")

    # -- D: doorbell (manual test run grabs a live snapshot) ------------------------------------
    print("\nScenario D - doorbell alert test run with a live snapshot")
    t = time.time()
    call("POST", f"/api/automations/{doorbell['id']}/run", {"force": True})
    md = wait_outbox(phones["Marlina"], "automation", t, pred=lambda m: m.get("automation_id") == doorbell["id"])
    check("doorbell notification", bool(md), md and f"{md['title']} / {md['body']} / time_sensitive={md['time_sensitive']}")
    if md and md.get("snapshot_url"):
        path = md["snapshot_url"].split("8093", 1)[-1] if "8093" in md["snapshot_url"] else md["snapshot_url"]
        img, st = call("GET", path, expect=None)
        check("snapshot fetchable with the phone's token", st == 200 and isinstance(img, bytes) and img[:2] == b"\xff\xd8",
              f"{len(img) if isinstance(img, bytes) else 0} bytes")
        _, st = call("GET", path.replace("device=", "device=x"), expect=None)
        check("snapshot refused for another device", st == 401)
    else:
        check("snapshot attached", False, "no snapshot_url")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


def relock_all():
    """Whatever happened, leave the house locked."""
    for _ in range(3):
        locks = call("GET", "/api/entities?kind=lock")[0]
        todo = [e for e in locks if e["state"] != "LOCKED"]
        if not todo:
            print("\nAll doors locked.")
            return
        for e in todo:
            call("POST", f"/api/entities/{e['id']}/command", {"command": "lock"}, expect=None)
        for e in todo:
            wait_state(e["id"], "LOCKED", required=False)
    print("\nWARNING: could not confirm every door is locked:",
          {e["name"]: e["state"] for e in call("GET", "/api/entities?kind=lock")[0]})


if __name__ == "__main__":
    try:
        code = main()
    finally:
        relock_all()
    sys.exit(code)
