"""Public space data. Observer coordinates are used locally, never sent to APIs."""
import asyncio
import math
import time
from datetime import datetime

import httpx

from .state import config

iss = {"t": 0, "err": ""}
weather = {"t": 0, "err": ""}
events = [asyncio.Event(), asyncio.Event()]


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Non-finite measurement")
    return value


async def fetch_iss(client):
    response = await client.get("https://api.wheretheiss.at/v1/satellites/25544",
                                params={"timestamp": int(time.time())})
    response.raise_for_status()
    data = response.json()
    lat, lon = finite(data["latitude"]), finite(data["longitude"])
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Invalid satellite position")
    iss.update(t=finite(data["timestamp"]), err="", lat=lat, lon=lon,
               altitude=finite(data["altitude"]), speed=finite(data["velocity"]))


def kp_rows(data):
    if data and isinstance(data[0], list):
        data = [dict(zip(data[0], row)) for row in data[1:]]
    points = []
    for row in data:
        try:
            value = finite(row.get("Kp", row.get("kp")))
            stamp = datetime.fromisoformat(row["time_tag"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                from datetime import timezone
                stamp = stamp.replace(tzinfo=timezone.utc)
            if 0 <= value <= 9:
                points.append([stamp.timestamp(), value])
        except (ValueError, TypeError, KeyError):
            continue
    if not points:
        raise ValueError("No Kp measurements")
    return sorted(points)


async def fetch_weather(client):
    response = await client.get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json")
    response.raise_for_status()
    points = kp_rows(response.json())
    recent = [row for row in points if row[0] >= points[-1][0] - 86400]
    weather.update(t=points[-1][0], kp=points[-1][1], points=recent,
                   peak=max(row[1] for row in recent), err="")


def refresh():
    for event in events:
        event.set()


async def worker(client, store, fetch, flag, interval, multiplier, event):
    while True:
        event.clear()
        settings = config.data["screen"]
        if settings[flag]:
            try:
                await fetch(client)
            except Exception:
                store["err"] = "API недоступен"
        else:
            store.clear()
            store.update(t=0, err="")
        delay = settings[interval] * multiplier
        if store["err"]:
            delay = max(60, delay)
        try:
            await asyncio.wait_for(event.wait(), delay)
        except asyncio.TimeoutError:
            pass


async def loop():
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "CYDHub/1.0 (desk display)"}) as client:
        await asyncio.gather(
            worker(client, iss, fetch_iss, "show_iss", "iss_interval_s", 1, events[0]),
            worker(client, weather, fetch_weather, "show_spaceweather", "spaceweather_interval_min", 60, events[1]))


def public():
    satellite = dict(iss)
    if "lat" in satellite:
        observer = config.data["dashboard"]
        a, b = math.radians(float(observer["lat"])), math.radians(satellite["lat"])
        delta = math.radians(satellite["lon"] - float(observer["lon"]))
        angle = math.acos(max(-1, min(1, math.sin(a) * math.sin(b) + math.cos(a) * math.cos(b) * math.cos(delta))))
        satellite["distance"] = round(6371 * angle)
    return {"iss": satellite, "weather": weather}
