"""Tasks: GCloud Calendar's to-dos (iCalendar VTODO), read and written through a Calendar server (CalDAV)
connection. They replace Newman's own Upkeep items. The format is GCloud's (shared with it - see the
README's Today section):

    UID, SUMMARY, DESCRIPTION, DUE (a date, or a date and time), DTSTART = DUE, STATUS, COMPLETED
    RRULE                    FREQ=DAILY|WEEKLY|MONTHLY|YEARLY;INTERVAL=n - it comes back
    X-GCLOUD-REPEAT-FROM     DUE | DONE (absent = DUE)
    X-GCLOUD-OWNER           a GCloud username (absent = everyone)
    X-GCLOUD-LEAD-DAYS       shows as coming up this many days ahead (absent = 7)
    X-GCLOUD-EVENING-BEFORE  TRUE: a heads-up the evening before it's due
    X-GCLOUD-ASK-WHO         TRUE: the wall asks who did it (only without an owner)
    X-GCLOUD-DONE            one per completion, newest first: <UTC yyyymmddThhmmssZ>;<who>;<previous DUE>

Done is a PUT of the task with STATUS:COMPLETED, COMPLETED, PERCENT-COMPLETE:100 and X-GCLOUD-DONE-BY:<who>;
GCloud adds the X-GCLOUD-DONE line, rolls a repeating one forward and drops X-GCLOUD-DONE-BY. Undo puts the
previous DUE back from the newest X-GCLOUD-DONE line, removes that line and reopens it.

Done and Undo edit the stored text line by line (not through a parser and back), so everything else in it
- GCloud's own lines exactly as written, reminders, other apps' properties - stays as it was.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import icalendar

from .integrations.calendar_ics import _local

DEFAULT_LEAD = 7
KEEP_COMPLETED_DAYS = 30   # finished one-off tasks are kept this long (Undo, the facts panel)
UNDO_WINDOW = timedelta(hours=12)  # Undo only takes back a history line this new (a server that isn't
                                   # GCloud adds none: an old line must not be taken back instead)
MAX_TITLE = 500
UNITS = {"days": "DAILY", "weeks": "WEEKLY", "months": "MONTHLY"}
FREQ_WORDS = {"DAILY": ("day", "days"), "WEEKLY": ("week", "weeks"), "MONTHLY": ("month", "months"),
              "YEARLY": ("year", "years")}


# --- reading ------------------------------------------------------------------------------------------

def _flag(v: Any) -> bool:
    return str(v or "").strip().upper() == "TRUE"


def _values(comp: Any, name: str) -> list[str]:
    """Every value of a property that may repeat (X-GCLOUD-DONE), in the order written."""
    v = comp.get(name)
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, list) else [v])]


def done_line(text: str) -> dict[str, str] | None:
    """One X-GCLOUD-DONE value → {"at": ISO UTC, "who", "prevDue"}; None when it isn't one."""
    parts = text.replace("\\;", ";").split(";")
    if len(parts) < 3:
        return None
    try:
        at = datetime.strptime(parts[0].strip(), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return {"at": at.isoformat(), "who": ";".join(parts[1:-1]).strip(), "prevDue": parts[-1].strip()}


def every_text(rrule: Any) -> str:
    """"6 months", "2 weeks", "1 day" - from an RRULE (vRecur or its text); "" when it doesn't repeat."""
    if rrule is None:
        return ""
    if isinstance(rrule, str):
        if not rrule:
            return ""
        try:
            rrule = icalendar.vRecur.from_ical(rrule)
        except (ValueError, TypeError):
            return ""
    freq = (rrule.get("FREQ") or [""])[0]
    n = int((rrule.get("INTERVAL") or [1])[0] or 1)
    one, many = FREQ_WORDS.get(str(freq).upper(), ("", ""))
    return f"{n} {one if n == 1 else many}" if one else ""


def parse_tasks(text: str, href: str, etag: str = "", today: date | None = None) -> list[dict[str, Any]]:
    """One calendar object → its task (the VTODO without RECURRENCE-ID), or [] when it has none. One-off
    tasks finished more than KEEP_COMPLETED_DAYS ago and cancelled ones are left out."""
    cal = icalendar.Calendar.from_ical(text)
    today = today or date.today()
    out = []
    for t in cal.walk("VTODO"):
        if t.get("RECURRENCE-ID") is not None:
            continue  # an app's copy of one time (Apple Reminders); GCloud keeps the task itself
        status = str(t.get("STATUS") or "NEEDS-ACTION").upper()
        if status == "CANCELLED":
            continue
        due_raw = t.get("DUE") or t.get("DTSTART")
        due, due_at = None, None
        if due_raw is not None:
            v, all_day = _local(due_raw.dt)
            due = (v if isinstance(v, date) and not isinstance(v, datetime) else v.date()).isoformat()
            due_at = None if all_day else v.isoformat()
        completed = None
        if t.get("COMPLETED") is not None:
            c = t.get("COMPLETED").dt
            completed = (c if isinstance(c, datetime) else datetime(c.year, c.month, c.day)).isoformat()
        rrule = t.get("RRULE")
        if status == "COMPLETED" and rrule is None:
            when = date.fromisoformat(completed[:10]) if completed else None
            if when and (today - when).days > KEEP_COMPLETED_DAYS:
                continue
        try:
            lead = max(0, int(str(t.get("X-GCLOUD-LEAD-DAYS") or DEFAULT_LEAD).strip()))
        except ValueError:
            lead = DEFAULT_LEAD
        owner = str(t.get("X-GCLOUD-OWNER") or "").strip()
        history = sorted((d for d in (done_line(x) for x in _values(t, "X-GCLOUD-DONE")) if d),
                         key=lambda d: d["at"], reverse=True)
        out.append({
            "uid": str(t.get("UID") or ""), "href": href, "etag": etag,
            "title": str(t.get("SUMMARY") or "A task")[:MAX_TITLE], "notes": str(t.get("DESCRIPTION") or ""),
            "due": due, "dueAt": due_at, "status": status, "completed": completed,
            "rrule": rrule.to_ical().decode() if rrule is not None else "", "every": every_text(rrule),
            "repeatFrom": "done" if str(t.get("X-GCLOUD-REPEAT-FROM") or "").strip().upper() == "DONE" else "due",
            "owner": owner, "lead": lead, "eveningBefore": _flag(t.get("X-GCLOUD-EVENING-BEFORE")),
            "askWho": _flag(t.get("X-GCLOUD-ASK-WHO")) and not owner, "history": history,
        })
    return out


# --- writing: line by line ----------------------------------------------------------------------------

def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _fold(lines: list[str]) -> str:
    out = []
    for line in lines:
        b = line.encode()
        if len(b) <= 75:
            out.append(line)
            continue
        chunks, cur, limit = [], "", 75
        for ch in line:
            if len((cur + ch).encode()) > limit:
                chunks.append(cur)
                cur, limit = "", 74
            cur += ch
        chunks.append(cur)
        out.append("\r\n ".join(chunks))
    return "\r\n".join(out) + "\r\n"


def _name(line: str) -> str:
    cut = min([i for i in (line.find(":"), line.find(";")) if i >= 0] or [len(line)])
    return line[:cut].upper()


def _params(line: str) -> str:
    """";TZID=America/Toronto" or ";VALUE=DATE" - what's between the name and the value."""
    colon = line.find(":")
    head = line[:colon] if colon >= 0 else line
    semi = head.find(";")
    return head[semi:] if semi >= 0 else ""


def _master(lines: list[str]) -> tuple[int, int]:
    """(first, last) line index of the task itself: the VTODO without RECURRENCE-ID (its own lines only)."""
    i = 0
    while i < len(lines):
        if lines[i].upper() == "BEGIN:VTODO":
            j, depth = i + 1, 0
            while j < len(lines):
                u = lines[j].upper()
                if u.startswith("BEGIN:"):
                    depth += 1
                elif u.startswith("END:"):
                    if depth == 0:
                        break
                    depth -= 1
                j += 1
            if not any(_name(lines[k]) == "RECURRENCE-ID" for k in range(i + 1, j)):
                return i, j
            i = j
        i += 1
    raise ValueError("That task isn't on the calendar any more")


def _own_lines(lines: list[str], lo: int, hi: int) -> list[int]:
    """Indexes of the task's own property lines (not those of a VALARM inside it)."""
    out, depth = [], 0
    for k in range(lo + 1, hi):
        u = lines[k].upper()
        if u.startswith("BEGIN:"):
            depth += 1
        elif u.startswith("END:"):
            depth -= 1
        elif depth == 0:
            out.append(k)
    return out


def _utc(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _edit(text: str, drop: set[str], add: list[str], change: dict[str, str] | None = None,
          drop_line: str | None = None) -> str:
    """The task with its `drop` properties removed, `change` ones' lines replaced (name → whole line, kept
    in place; added when it had none), the line `drop_line` (once) removed, and `add` lines put at its end."""
    lines = _unfold(text)
    lo, hi = _master(lines)
    change = dict(change or {})
    first_dropped = False
    own = set(_own_lines(lines, lo, hi))
    out = lines[:lo + 1]
    for k in range(lo + 1, hi):
        line = lines[k]
        if k in own:
            n = _name(line)
            if n in drop:
                continue
            if drop_line is not None and line == drop_line and not first_dropped:
                first_dropped = True
                continue
            if n in change:
                out.append(change.pop(n))
                continue
        out.append(line)
    out.extend(change.values())
    out.extend(add)
    out.extend(lines[hi:])
    return _fold(out)


def mark_done(text: str, who: str, now: datetime | None = None) -> str:
    """The PUT body for Done: completed now, by `who` (GCloud adds the history line and, for a repeating
    task, rolls it forward)."""
    now = now or datetime.now(timezone.utc)
    stamp = _utc(now)
    add = [f"COMPLETED:{stamp}", "PERCENT-COMPLETE:100"]
    if who:
        add.append("X-GCLOUD-DONE-BY:" + _escape(who))
    return _edit(text, {"COMPLETED", "PERCENT-COMPLETE", "X-GCLOUD-DONE-BY", "LAST-MODIFIED", "DTSTAMP"},
                 add + [f"LAST-MODIFIED:{stamp}", f"DTSTAMP:{stamp}"], {"STATUS": "STATUS:COMPLETED"})


def _when_line(name: str, old_line: str | None, value: str) -> str:
    """DUE / DTSTART set back to `value` (as the X-GCLOUD-DONE line wrote it), keeping a time zone the
    task's line had for a time of day."""
    if len(value) == 8:
        return f"{name};VALUE=DATE:{value}"
    if value.endswith("Z"):
        return f"{name}:{value}"
    params = [p for p in _params(old_line or "").split(";") if p.upper().startswith("TZID=")]
    return f"{name}{';' + params[0] if params else ''}:{value}"


def undo_done(text: str, now: datetime | None = None) -> str:
    """The PUT body for Undo: the previous DUE (and DTSTART) back from the newest X-GCLOUD-DONE line, that
    line removed, open again."""
    now = now or datetime.now(timezone.utc)
    stamp = _utc(now)
    lines = _unfold(text)
    lo, hi = _master(lines)
    own = {_name(lines[k]): lines[k] for k in reversed(_own_lines(lines, lo, hi))}  # first of each name
    dones = [lines[k] for k in _own_lines(lines, lo, hi) if _name(lines[k]) == "X-GCLOUD-DONE"]
    # Newest first by the format; by its time, should a client have written them in another order.
    newest = max(dones, key=lambda x: (done_line(x.split(":", 1)[-1]) or {"at": ""})["at"], default=None)
    d = done_line(newest.split(":", 1)[-1]) if newest else None
    if not d or datetime.fromisoformat(d["at"]) < now - UNDO_WINDOW:
        newest = None  # no line for this Done: only open it again
    change: dict[str, str] = {"STATUS": "STATUS:NEEDS-ACTION"}
    if newest and d:
        prev = d["prevDue"]
        if prev and prev[:8].isdigit():
            change["DUE"] = _when_line("DUE", own.get("DUE"), prev)
            if "DTSTART" in own:
                change["DTSTART"] = _when_line("DTSTART", own.get("DTSTART"), prev)
    return _edit(text, {"COMPLETED", "PERCENT-COMPLETE", "X-GCLOUD-DONE-BY", "LAST-MODIFIED", "DTSTAMP"},
                 [f"LAST-MODIFIED:{stamp}", f"DTSTAMP:{stamp}"], change,
                 drop_line=newest)


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def task_fields(data: dict[str, Any]) -> dict[str, Any]:
    """What a phone's "+ Add" sends → checked: {title, due (date), every (0 = once), unit, owner (username)}."""
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("Say what needs doing first.")
    try:
        due = date.fromisoformat(str(data.get("due") or "")[:10])
    except ValueError as e:
        raise ValueError("Pick when it's due.") from e
    try:
        every = int(data.get("every") or 0)
    except (TypeError, ValueError) as e:
        raise ValueError("How often must be a whole number") from e
    unit = str(data.get("unit") or "days")
    if unit not in UNITS:
        raise ValueError("How often is in days, weeks or months")
    if not 0 <= every <= 999:
        raise ValueError("How often is 1 to 999")
    return {"title": title[:MAX_TITLE], "due": due, "every": every, "unit": unit,
            "owner": str(data.get("owner") or "").strip()}


def new_task(f: dict[str, Any], now: datetime | None = None, uid: str | None = None) -> tuple[str, str]:
    """(uid, VCALENDAR text) for a new task in GCloud's format."""
    now = now or datetime.now(timezone.utc)
    uid = uid or f"{uuid.uuid4()}@newman"
    stamp, day = _utc(now), f["due"].strftime("%Y%m%d")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Newman//Tasks//EN", "BEGIN:VTODO",
             f"UID:{uid}", f"DTSTAMP:{stamp}", f"CREATED:{stamp}", f"LAST-MODIFIED:{stamp}",
             "SUMMARY:" + _escape(f["title"]), f"DUE;VALUE=DATE:{day}", f"DTSTART;VALUE=DATE:{day}",
             "STATUS:NEEDS-ACTION"]
    if f["every"]:
        lines.append(f"RRULE:FREQ={UNITS[f['unit']]};INTERVAL={f['every']}")
    if f["owner"]:
        lines.append("X-GCLOUD-OWNER:" + _escape(f["owner"]))
    lines += ["END:VTODO", "END:VCALENDAR"]
    return uid, _fold(lines)


# --- who's who ------------------------------------------------------------------------------------------

def household(store: Any) -> list[dict[str, Any]]:
    """Newman's people, each with the user names of the Newman accounts linked to them (lower case)."""
    users = store.users()
    return [{"id": p["id"], "name": p["name"],
             "usernames": [str(u["username"]).lower() for u in users if u.get("person_id") == p["id"]]}
            for p in store.people()]


def owner_person(owner: str, people: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The Newman person a GCloud user name belongs to: by a linked account's user name, else by name
    (ignoring case)."""
    o = owner.strip().lower()
    if not o:
        return None
    return next((p for p in people if o in p.get("usernames", [])), None) or \
        next((p for p in people if p["name"].strip().lower() == o), None)


def owner_username(person_id: str, people: list[dict[str, Any]]) -> str:
    """The GCloud user name a task added for a person gets: their Newman account's user name, else their
    name in lower case."""
    p = next((x for x in people if x["id"] == person_id), None)
    if not p:
        return ""
    return (p.get("usernames") or [p["name"].strip().lower()])[0]


def task_id(cal_key: str, uid: str) -> str:
    return f"{cal_key}:task:{uid}"


def split_id(item_id: str) -> tuple[str, str]:
    cal, sep, uid = item_id.partition(":task:")
    if not sep or not cal or not uid:
        raise ValueError("That isn't a task")
    return cal, uid


def tomorrow_start(now: datetime | None = None) -> float:
    n = now or datetime.now()
    return datetime.combine(n.date() + timedelta(days=1), datetime.min.time()).timestamp()
