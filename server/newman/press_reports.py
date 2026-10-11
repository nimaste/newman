"""Phones' accounts of notification-button presses (POST /api/public/press-report).

A press that times out on the phone may never reach the hub, so the hub's own log can't explain
it. After every press the phone app sends what it saw: each try's address, how the app was running,
how long it took, the error (with its underlying error, stream code and network path), whether a
client certificate was asked for and there to give, and the connection's timings
(URLSessionTaskMetrics). A failed press is logged as one WARNING line per try; a press that worked
as one INFO line. The last few are kept in memory for GET /api/diagnostics/press (admins).
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

KEPT = 50
MAX_ATTEMPTS = 10
#: A report may come a while after the push it is about (queued on the phone until it reaches the
#: hub), so its action token is accepted this long after it expired.
GRACE_S = 7 * 86400

_STR = 300
_METRIC_STEPS = ("dns", "connect", "secure", "request", "response")
_METRIC_NAMES = {"secure": "tls"}


def _s(v: Any, n: int = _STR) -> str | None:
    return None if v is None else str(v)[:n]


def _num(v: Any) -> float | int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def _bool(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def _clean_error(e: Any) -> dict[str, Any] | None:
    if not isinstance(e, dict):
        return None
    return {"domain": _s(e.get("domain"), 100), "code": _num(e.get("code")), "description": _s(e.get("description")),
            "underlying": _s(e.get("underlying")), "streamCode": _num(e.get("streamCode")),
            "streamDomain": _num(e.get("streamDomain")), "failingHost": _s(e.get("failingHost"), 200),
            "path": _s(e.get("path"))}


def _clean_metrics(m: Any) -> dict[str, Any] | None:
    if not isinstance(m, dict):
        return None
    out: dict[str, Any] = {"transactions": _num(m.get("transactions")), "fetchStartAt": _num(m.get("fetchStartAt"))}
    for step in _METRIC_STEPS:
        for end in ("Start", "End"):
            out[step + end] = _num(m.get(step + end))
    for k in ("remoteAddress", "localAddress", "networkProtocol"):
        out[k] = _s(m.get(k), 100)
    out["remotePort"] = _num(m.get("remotePort"))
    for k in ("reusedConnection", "proxyConnection", "cellular", "expensive", "constrained"):
        out[k] = _bool(m.get(k))
    return out


def _clean_attempt(a: Any) -> dict[str, Any] | None:
    if not isinstance(a, dict):
        return None
    other = a.get("otherChallenges")
    return {"at": _num(a.get("at")), "url": _s(a.get("url")), "appState": _s(a.get("appState"), 20),
            "backgroundLeft": _num(a.get("backgroundLeft")), "timeoutSeconds": _num(a.get("timeoutSeconds")),
            "ms": _num(a.get("ms")), "status": _num(a.get("status")), "error": _clean_error(a.get("error")),
            "certChallenges": _num(a.get("certChallenges")) or 0, "certAvailable": _bool(a.get("certAvailable")),
            "otherChallenges": [str(x)[:60] for x in other[:5]] if isinstance(other, list) else None,
            "metrics": _clean_metrics(a.get("metrics"))}


def clean(data: dict[str, Any], *, phone: str | None, remote: str | None, now: float | None = None) -> dict[str, Any]:
    """What is kept of a report: the phone's fields, bounded - never its token."""
    attempts = data.get("attempts") if isinstance(data.get("attempts"), list) else []
    return {
        "id": _s(data.get("id"), 64) or "", "receivedAt": now or time.time(), "from": remote,
        "deviceId": _s(data.get("deviceId"), 100) or "", "phone": phone,
        "button": _s(data.get("button"), 120) or "", "subject": _s(data.get("subject"), 120) or "",
        "ok": bool(data.get("ok")), "outcome": _s(data.get("outcome")) or "", "summary": _s(data.get("summary")) or "",
        "appVersion": _s(data.get("appVersion"), 40), "launchState": _s(data.get("launchState"), 20),
        "sinceLaunchSeconds": _num(data.get("sinceLaunchSeconds")),
        "backgroundExpired": bool(data.get("backgroundExpired")), "actionURL": _s(data.get("actionURL")),
        "at": _num(data.get("at")),
        "attempts": [c for c in (_clean_attempt(a) for a in attempts[:MAX_ATTEMPTS]) if c],
    }


def _clock(ts: Any) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else "?"


def _span(m: dict[str, Any], step: str) -> str:
    a, b = m.get(step + "Start"), m.get(step + "End")
    if a is None:
        return "-"
    return f"{a}-{'?' if b is None else b}"


def metrics_line(m: dict[str, Any] | None) -> str:
    if not m:
        return "no metrics"
    s = " ".join(f"{_METRIC_NAMES.get(step, step)} {_span(m, step)}" for step in _METRIC_STEPS)
    who = []
    if m.get("remoteAddress"):
        who.append(f"{m['remoteAddress']}:{m['remotePort']}" if m.get("remotePort") is not None else m["remoteAddress"])
    if m.get("localAddress"):
        who.append(f"from {m['localAddress']}")
    if m.get("networkProtocol"):
        who.append(m["networkProtocol"])
    if m.get("reusedConnection") is not None:
        who.append("reused" if m["reusedConnection"] else "new")
    if m.get("proxyConnection"):
        who.append("proxy")
    if m.get("cellular"):
        who.append("cellular")
    if m.get("transactions") not in (None, 1):
        who.append(f"{m['transactions']} transactions")
    return s + (" | " + " ".join(who) if who else "")


def error_line(e: dict[str, Any] | None) -> str:
    if not e:
        return "no answer"
    s = f"{e.get('domain')} {e.get('code')} {e.get('description')}"
    extra = []
    if e.get("underlying"):
        extra.append(f"underlying {e['underlying']}")
    if e.get("streamCode") is not None:
        extra.append(f"stream {e.get('streamDomain')}/{e['streamCode']}")
    if e.get("failingHost"):
        extra.append(f"host {e['failingHost']}")
    if e.get("path"):
        extra.append(f"path {e['path']}")
    return s + (f" [{'; '.join(extra)}]" if extra else "")


def lines(rep: dict[str, Any]) -> list[str]:
    """The log lines for a kept report: one per try for a failed press, one for a press that worked."""
    who = rep["deviceId"] + (f" ({rep['phone']})" if rep.get("phone") else "")
    head = f"Press report from {who}: button {rep['button']!r} on {rep['subject'] or '?'}"
    app = f"app {rep.get('appVersion') or '?'}, {rep.get('launchState') or '?'} when the button arrived"
    if rep.get("sinceLaunchSeconds") is not None:
        app += f", running {rep['sinceLaunchSeconds']} s"
    if rep.get("backgroundExpired"):
        app += ", iOS ended its background time"
    if rep["ok"] or not rep["attempts"]:
        return [f"{head} at {_clock(rep.get('at'))}: {rep['summary'] or rep['outcome']} ({app})"]
    out = []
    n = len(rep["attempts"])
    for i, a in enumerate(rep["attempts"]):
        left = f", {a['backgroundLeft']} s background left" if a.get("backgroundLeft") is not None else ""
        cert = f"cert challenges {a['certChallenges']}"
        if a.get("certAvailable") is not None:
            cert += " (certificate sent)" if a["certAvailable"] else " (no certificate stored)"
        if a.get("otherChallenges"):
            cert += f"; other challenges {','.join(a['otherChallenges'])}"
        answer = f"HTTP {a['status']}" if a.get("status") is not None else error_line(a.get("error"))
        out.append(f"{head} {rep['outcome']} - try {i + 1}/{n} {a.get('url')} at {_clock(a.get('at'))} "
                   f"({a.get('appState')}{left}, {a.get('timeoutSeconds')} s allowed): {a.get('ms')} ms, {answer}; "
                   f"{cert}; {metrics_line(a.get('metrics'))} ({app})")
    return out


class PressReports:
    """The last KEPT reports, newest last; a report delivered twice (two addresses) is kept once."""

    def __init__(self, kept: int = KEPT) -> None:
        self.items: deque[dict[str, Any]] = deque(maxlen=kept)

    def seen(self, rid: str) -> bool:
        return bool(rid) and any(r["id"] == rid for r in self.items)

    def add(self, rep: dict[str, Any]) -> None:
        self.items.append(rep)

    def to_json(self) -> list[dict[str, Any]]:
        return list(reversed(self.items))
