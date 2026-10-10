"""Weather (Open-Meteo + Environment Canada alerts) and calendar links, offline."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations.base import SECRET_MASK, Context, mask_config, merge_config  # noqa: E402
from newman.integrations.calendar_ics import CalendarIntegration, parse  # noqa: E402
from newman.integrations.weather import WeatherIntegration  # noqa: E402
from newman.kinds import properties_for  # noqa: E402
from newman.state import Hub  # noqa: E402


def ics(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def vevent(uid: str, summary: str, start: str, end: str, extra: str = "") -> str:
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{summary}\r\nDTSTART{start}\r\nDTEND{end}\r\n{extra}"
            "END:VEVENT\r\n")


class Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class CalendarTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_expands_repeats_and_all_day(self):
        now = datetime.now().replace(second=0, microsecond=0)
        start = now + timedelta(hours=1)  # five daily repeats, all inside the next week
        text = ics(
            vevent("a", "Soccer practice", f":{start:%Y%m%dT%H%M%S}", f":{start + timedelta(hours=1):%Y%m%dT%H%M%S}",
                   "RRULE:FREQ=DAILY;COUNT=5\r\n"),
            vevent("b", "Recycling day", f";VALUE=DATE:{now:%Y%m%d}", f";VALUE=DATE:{now + timedelta(days=1):%Y%m%d}"),
            vevent("c", "Called off", f":{now + timedelta(hours=3):%Y%m%dT%H%M%S}",
                   f":{now + timedelta(hours=4):%Y%m%dT%H%M%S}", "STATUS:CANCELLED\r\n"))
        events = parse(text, now - timedelta(days=1), now + timedelta(days=7))
        self.assertEqual(sum(1 for e in events if e["title"] == "Soccer practice"), 5)
        self.assertTrue(next(e for e in events if e["title"] == "Recycling day")["allDay"])
        self.assertFalse(any(e["title"] == "Called off" for e in events))

    async def test_busy_now_and_event_starts(self):
        tmp = tempfile.TemporaryDirectory()
        hub = Hub()
        cal = CalendarIntegration("calendar", "Calendars", {"calendars": [{"name": "Family", "url": "https://x/y.ics",
                                                                           "colour": "#7FB8FF"}]},
                                  Context(hub, Path(tmp.name), Path(tmp.name)))
        c = cal.config["calendars"][0]
        hub.upsert(cal._eid(c), kind="calendar", name="Family", integration="calendar")
        now = datetime.now()
        cal._events["Family"] = [
            {"title": "Dentist", "start": (now - timedelta(minutes=10)).isoformat(),
             "end": (now + timedelta(minutes=50)).isoformat(), "allDay": False, "location": ""},
            {"title": "Pickup", "start": (now + timedelta(seconds=1)).isoformat(),
             "end": (now + timedelta(hours=1)).isoformat(), "allDay": False, "location": "School"}]
        fired = []

        async def on_event(ent, event, data):
            fired.append((ent.id, event, data["title"]))
        hub.subscribe_events(on_event)
        cal._last_tick = (now - timedelta(seconds=30)).timestamp()
        await cal._tick([c])
        ent = hub.get("calendar.family")
        self.assertEqual((ent.state, ent.attrs["now"], ent.attrs["colour"]), ("busy", "Dentist", "#7FB8FF"))
        await asyncio.sleep(1.1)
        await cal._tick([c])
        await asyncio.sleep(0.05)
        self.assertEqual(fired, [("calendar.family", "start", "Pickup")])
        tmp.cleanup()

    def test_calendar_links_are_hidden_like_passwords(self):
        cfg = {"calendars": [{"name": "Family", "url": "https://secret/1.ics"}, {"name": "Work", "url": "https://secret/2.ics"}]}
        masked = mask_config(CalendarIntegration, cfg)
        self.assertEqual([c["url"] for c in masked["calendars"]], [SECRET_MASK, SECRET_MASK])
        # Sent back reordered and unchanged: each keeps its own link.
        back = {"calendars": list(reversed(masked["calendars"])) + [{"name": "Kids", "url": "https://new/3.ics"}]}
        merged = merge_config(CalendarIntegration, cfg, back)
        self.assertEqual({c["name"]: c["url"] for c in merged["calendars"]},
                         {"Family": "https://secret/1.ics", "Work": "https://secret/2.ics", "Kids": "https://new/3.ics"})


class WeatherTests(unittest.IsolatedAsyncioTestCase):
    async def test_forecast_readings_and_new_alerts(self):
        tmp = tempfile.TemporaryDirectory()
        hub = Hub()
        logged = []
        w = WeatherIntegration("weather", "Weather", {"place": "Toronto", "latitude": 43.65, "longitude": -79.38},
                               Context(hub, Path(tmp.name), Path(tmp.name), hub_log=lambda *a, **k: logged.append(a)))
        hub.upsert(w.eid, kind="weather", name="Home", integration="weather")
        days = [(datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]
        forecast = {"current": {"temperature_2m": 19.2, "apparent_temperature": 19.3, "relative_humidity_2m": 80,
                                "weather_code": 3, "wind_speed_10m": 11.8, "is_day": 0},
                    "daily": {"time": days, "weather_code": [61, 0, 71], "temperature_2m_max": [21, 23, 2],
                              "temperature_2m_min": [12, 11, -3], "precipitation_probability_max": [70, 0, 40],
                              "sunrise": [d + "T07:10" for d in days], "sunset": [d + "T18:55" for d in days]},
                    "hourly": {"time": [days[0] + "T20:00"], "temperature_2m": [18.5], "weather_code": [80],
                               "precipitation_probability": [55]}}
        alert = {"alert_code": "WRW", "alert_type": "warning", "alert_name_en": "rainfall warning",
                 "risk_colour_en": "orange", "alert_text_en": "Heavy rain.", "feature_name_en": "City of Toronto",
                 "publication_datetime": "2026-09-30T21:00:00Z", "expiration_datetime": "2026-10-01T21:00:00Z",
                 "status_en": "issued"}
        replies = {"forecast": forecast, "alerts": {"features": []}}

        async def fake_get(url, params=None):
            return Resp(replies["alerts"] if "weather.gc.ca" in url else replies["forecast"])
        w._http.get = fake_get
        fired = []

        async def on_event(ent, event, data):
            fired.append((event, data["title"]))
        hub.subscribe_events(on_event)
        await w._update_weather(43.65, -79.38)
        ent = hub.get(w.eid)
        props = {p["id"]: p for p in properties_for(ent)}
        self.assertEqual((ent.state, props["temperature"]["value"], props["humidity"]["unit"]), ("cloudy", 19.2, "%"))
        self.assertEqual([(d["day"], d["condition"]) for d in ent.attrs["forecast"]][1:],
                         [(datetime.now().__add__(timedelta(days=1)).strftime("%a"), "clear"),
                          ((datetime.now() + timedelta(days=2)).strftime("%a"), "snow")])
        self.assertEqual((props["high"]["value"], props["precipChance"]["value"]), (21, 70))
        await w._update_alerts(43.65, -79.38)  # first look: nothing announced
        replies["alerts"] = {"features": [{"properties": alert}]}
        await w._update_alerts(43.65, -79.38)  # a new warning appears
        await w._update_alerts(43.65, -79.38)  # still there: not announced again
        await asyncio.sleep(0.05)
        self.assertEqual(fired, [("alert", "Rainfall warning"), ("warning", "Rainfall warning")])
        self.assertEqual(ent.attrs["alerts"] if False else hub.get(w.eid).attrs["alerts"][0]["colour"], "orange")
        self.assertEqual(len(logged), 1)
        await w._http.aclose() if hasattr(w._http, "aclose") else None
        tmp.cleanup()
