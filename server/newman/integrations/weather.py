"""Weather for a place: conditions and forecast from Open-Meteo (free, no account or key) plus,
in Canada, Environment Canada's official alerts (warnings / watches / advisories) for exactly
that spot. One weather device per connection; add another connection for a second place."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

import httpx

from .base import Field, Integration

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
EC_ALERTS_URL = "https://api.weather.gc.ca/collections/weather-alerts/items"
WEATHER_EVERY_S = 15 * 60
DAILY = ["weather_code", "temperature_2m_max", "temperature_2m_min", "apparent_temperature_max",
         "apparent_temperature_min", "precipitation_probability_max", "precipitation_sum", "rain_sum", "showers_sum",
         "snowfall_sum", "precipitation_hours", "wind_speed_10m_max", "wind_gusts_10m_max",
         "wind_direction_10m_dominant", "uv_index_max", "sunrise", "sunset"]
HOURLY = ["temperature_2m", "apparent_temperature", "weather_code", "precipitation_probability", "precipitation",
          "rain", "showers", "snowfall", "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
          "relative_humidity_2m", "cloud_cover", "uv_index"]
ALERTS_EVERY_S = 5 * 60

# WMO weather codes (what Open-Meteo reports) -> the weather kind's states.
WMO = {0: "clear", 1: "mostly-clear", 2: "partly-cloudy", 3: "cloudy", 45: "fog", 48: "fog",
       51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "freezing-rain", 57: "freezing-rain",
       61: "rain", 63: "rain", 65: "rain", 66: "freezing-rain", 67: "freezing-rain",
       71: "snow", 73: "snow", 75: "snow", 77: "snow", 80: "showers", 81: "showers", 82: "showers",
       85: "snow-showers", 86: "snow-showers", 95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm"}


def condition(code: Any) -> str:
    try:
        return WMO.get(int(code), "cloudy")
    except (TypeError, ValueError):
        return "cloudy"


class WeatherIntegration(Integration):
    type = "weather"
    name = "Weather"
    category = "climate"
    provides = ("weather",)  # kinds of device it can bring in (shown on its catalog card)
    description = ("Conditions now and the week ahead for your home, from Open-Meteo (free, no account). "
                   "In Canada it also watches Environment Canada's official warnings for your exact location.")
    fields = [
        Field("place", "Town or city", required=True, placeholder="Toronto",
              help="Newman looks it up once; fine-tune the exact spot under More settings."),
        Field("name", "Name", default="Home", help="What the weather device is called."),
        Field("alerts", "Environment Canada warnings", "boolean", default=True,
              help="Warnings, watches and advisories for this spot (Canada only)."),
        Field("latitude", "Latitude", "number", advanced=True),
        Field("longitude", "Longitude", "number", advanced=True),
    ]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._http = httpx.AsyncClient(timeout=20, headers={"User-Agent": "Newman home hub"})
        self._alert_ids: set[str] | None = None  # None = first look: don't announce what's already out

    @property
    def eid(self) -> str:
        return self.entity_id("weather")

    async def _locate(self) -> tuple[float, float, str]:
        lat, lon = self.config.get("latitude"), self.config.get("longitude")
        if lat not in (None, "") and lon not in (None, ""):
            return float(lat), float(lon), str(self.config.get("place") or "")
        r = await self._http.get(GEOCODE_URL, params={"name": self.config["place"], "count": 1, "language": "en"})
        r.raise_for_status()
        hits = r.json().get("results") or []
        if not hits:
            raise RuntimeError(f"Couldn't find “{self.config['place']}” - try a nearby city")
        h = hits[0]
        where = ", ".join(x for x in (h.get("name"), h.get("admin1"), h.get("country_code")) if x)
        cfg = dict(self.config, latitude=round(h["latitude"], 4), longitude=round(h["longitude"], 4))
        self.config = cfg
        if self.save_config:
            self.save_config(cfg)  # look it up once; the exact spot can then be fine-tuned
        return float(h["latitude"]), float(h["longitude"]), where

    async def run(self) -> None:
        lat, lon, where = await self._locate()
        self.hub.upsert(self.eid, kind="weather", name=str(self.config.get("name") or "Home"), integration=self.id)
        next_weather = next_alerts = 0.0
        while True:
            now = time.time()
            try:
                if now >= next_weather:
                    await self._update_weather(lat, lon)
                    next_weather = now + WEATHER_EVERY_S
                if self.config.get("alerts", True) and now >= next_alerts:
                    await self._update_alerts(lat, lon)
                    next_alerts = now + ALERTS_EVERY_S
                self.set_status("connected", f"{where or f'{lat}, {lon}'} · Open-Meteo"
                                + (" + Environment Canada" if self.config.get("alerts", True) else ""))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.set_status("error", f"Weather unavailable: {e}")
                await self.hub.update(self.eid, available=False)
                next_weather = min(next_weather, now + 120)
            await asyncio.sleep(30)

    async def _update_weather(self, lat: float, lon: float) -> None:
        r = await self._http.get(FORECAST_URL, params={
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,"
                       "wind_gusts_10m,is_day",
            "daily": ",".join(DAILY), "hourly": ",".join(HOURLY)})
        r.raise_for_status()
        d = r.json()
        cur, day, hr = d.get("current") or {}, d.get("daily") or {}, d.get("hourly") or {}

        def col(src: dict, key: str, i: int) -> Any:
            vals = src.get(key) or []
            return vals[i] if i < len(vals) else None

        forecast = [{"date": t, "day": datetime.fromisoformat(t).strftime("%a"),
                     "condition": condition(col(day, "weather_code", i)),
                     "high": col(day, "temperature_2m_max", i), "low": col(day, "temperature_2m_min", i),
                     "feelsHigh": col(day, "apparent_temperature_max", i), "feelsLow": col(day, "apparent_temperature_min", i),
                     "precipChance": col(day, "precipitation_probability_max", i),
                     "precip": col(day, "precipitation_sum", i), "rain": round((col(day, "rain_sum", i) or 0)
                                                                           + (col(day, "showers_sum", i) or 0), 1),
                     "snow": col(day, "snowfall_sum", i), "precipHours": col(day, "precipitation_hours", i),
                     "wind": col(day, "wind_speed_10m_max", i), "gusts": col(day, "wind_gusts_10m_max", i),
                     "windDirection": col(day, "wind_direction_10m_dominant", i), "uv": col(day, "uv_index_max", i),
                     "sunrise": col(day, "sunrise", i), "sunset": col(day, "sunset", i)}
                    for i, t in enumerate(day.get("time") or [])]
        # Every hour of the week (for a day's detail view); "hourly" = the next 24 from now.
        hours = [{"time": t, "temperature": col(hr, "temperature_2m", i), "feelsLike": col(hr, "apparent_temperature", i),
                  "condition": condition(col(hr, "weather_code", i)), "precipChance": col(hr, "precipitation_probability", i),
                  "precip": col(hr, "precipitation", i), "rain": round((col(hr, "rain", i) or 0) + (col(hr, "showers", i) or 0), 1),
                  "snow": col(hr, "snowfall", i), "wind": col(hr, "wind_speed_10m", i), "gusts": col(hr, "wind_gusts_10m", i),
                  "windDirection": col(hr, "wind_direction_10m", i), "humidity": col(hr, "relative_humidity_2m", i),
                  "cloud": col(hr, "cloud_cover", i), "uv": col(hr, "uv_index", i)}
                 for i, t in enumerate(hr.get("time") or [])]
        now_hour = datetime.now().strftime("%Y-%m-%dT%H:00")
        hourly = [h for h in hours if h["time"] >= now_hour][:24]
        today = forecast[0] if forecast else {}
        await self.hub.update(self.eid, available=True, state=condition(cur.get("weather_code")), attrs={
            "temperature": cur.get("temperature_2m"), "feelsLike": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"), "wind": cur.get("wind_speed_10m"),
            "gusts": cur.get("wind_gusts_10m"), "isDay": bool(cur.get("is_day")),
            "high": today.get("high"), "low": today.get("low"), "precipChance": today.get("precipChance"),
            "rainToday": today.get("rain"), "snowToday": today.get("snow"), "uv": today.get("uv"),
            "sunrise": today.get("sunrise"), "sunset": today.get("sunset"),
            "forecast": forecast, "hourly": hourly, "hours": hours, "updated": time.time(), "source": "Open-Meteo"})

    async def _update_alerts(self, lat: float, lon: float) -> None:
        # A tiny box around the spot: Environment Canada returns the alert areas it falls in.
        box = f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}"
        r = await self._http.get(EC_ALERTS_URL, params={"f": "json", "limit": 50, "bbox": box})
        r.raise_for_status()
        alerts, seen = [], set()
        for f in r.json().get("features") or []:
            p = f.get("properties") or {}
            if p.get("status_en") == "cancelled":
                continue
            key = f"{p.get('alert_code')}|{p.get('publication_datetime')}"
            if key in seen:
                continue
            seen.add(key)
            alerts.append({"id": key, "name": (p.get("alert_name_en") or "").capitalize(),
                           "type": p.get("alert_type"), "colour": p.get("risk_colour_en"),
                           "text": p.get("alert_text_en") or "", "area": p.get("feature_name_en"),
                           "issued": p.get("publication_datetime"), "expires": p.get("expiration_datetime")})
        await self.hub.update(self.eid, attrs={"alerts": alerts})
        new = [a for a in alerts if self._alert_ids is not None and a["id"] not in self._alert_ids]
        self._alert_ids = seen
        for a in new:
            data = {"title": a["name"], "text": a["text"][:500]}
            await self.hub.emit(self.eid, "alert", data)
            if a["type"] == "warning":
                await self.hub.emit(self.eid, "warning", data)
            self.ctx.hub_log("weather", a["name"], a["area"] or "", self.eid, {"type": a["type"], "colour": a["colour"]})
