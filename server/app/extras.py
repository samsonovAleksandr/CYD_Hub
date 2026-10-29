"""Вкладка «Сводка»: погода (MET Norway, без ключа) и курсы валют ЦБ РФ.
Open-Meteo из домашней сети недоступен, поэтому погода — с api.met.no."""
import asyncio
import time
from datetime import datetime

import httpx

from .state import config

weather = {"t": 0, "err": ""}
rates = {"t": 0, "err": "", "items": []}

# api.met.no требует понятный User-Agent
UA = {"User-Agent": "cydhub/1.0 (home desk display)"}

# symbol_code MET Norway -> (текст, значок для экрана); порядок важен: гроза раньше дождя
SYMBOLS = [
    ("thunder", "гроза", "storm"), ("sleet", "мокрый снег", "snow"), ("snow", "снег", "snow"),
    ("heavyrain", "ливень", "rain"), ("lightrain", "небольшой дождь", "rain"), ("drizzle", "морось", "rain"),
    ("rain", "дождь", "rain"), ("fog", "туман", "fog"), ("clearsky", "ясно", "sun"), ("fair", "почти ясно", "sun"),
    ("partlycloudy", "переменная облачность", "cloud-sun"), ("cloudy", "пасмурно", "cloud"),
]


def symbol(code):
    for key, text, icon in SYMBOLS:
        if key in (code or ""):
            return text, icon
    return "", "cloud"


def local(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()


async def fetch_weather(c):
    d = config.data["dashboard"]
    r = await c.get("https://api.met.no/weatherapi/locationforecast/2.0/compact",
                    params={"lat": round(d["lat"], 4), "lon": round(d["lon"], 4)}, headers=UA)
    r.raise_for_status()
    series = r.json()["properties"]["timeseries"]
    now = series[0]
    det = now["data"]["instant"]["details"]
    nxt = now["data"].get("next_1_hours") or now["data"].get("next_6_hours") or {}
    text, icon = symbol(nxt.get("summary", {}).get("symbol_code"))
    today = local(now["time"]).date()
    temps = [p["data"]["instant"]["details"]["air_temperature"] for p in series if local(p["time"]).date() == today]
    hours = []
    for p in series[2:14:2]:                      # каждые 2 часа на 12 часов вперёд
        n1 = p["data"].get("next_1_hours") or p["data"].get("next_6_hours") or {}
        hours.append({"h": local(p["time"]).strftime("%H"), "temp": round(p["data"]["instant"]["details"]["air_temperature"]),
                      "icon": symbol(n1.get("summary", {}).get("symbol_code"))[1]})
    t, wind = det["air_temperature"], det["wind_speed"]
    # «ощущается»: при морозе с ветром — ветро-холодовой индекс
    feels = 13.12 + 0.6215 * t - 11.37 * (wind * 3.6) ** 0.16 + 0.3965 * t * (wind * 3.6) ** 0.16 if t < 10 and wind > 1.3 else t
    weather.update(t=time.time(), err="", city=d["city"], temp=round(t), feels=round(feels), text=text, icon=icon,
                   wind=round(wind), hum=round(det.get("relative_humidity", 0)),
                   tmax=round(max(temps or [t])), tmin=round(min(temps or [t])), hours=hours)


async def fetch_rates(c):
    r = await c.get("https://www.cbr-xml-daily.ru/daily_json.js")
    r.raise_for_status()
    j = r.json()
    want = [x.strip().upper() for x in config.data["dashboard"]["rates"].split(",") if x.strip()]
    items = []
    for code in want[:4]:
        v = j["Valute"].get(code)
        if v:
            val, prev = v["Value"] / v["Nominal"], v["Previous"] / v["Nominal"]
            items.append({"code": code, "value": round(val, 2), "delta": round(val - prev, 2)})
    rates.update(t=time.time(), err="", items=items, date=j["Date"][:10])


async def loop():
    last_w = last_r = 0
    async with httpx.AsyncClient(timeout=15) as c:
        while True:
            now = time.time()
            if now - last_w > 900:          # погода — раз в 15 мин
                last_w = now
                try:
                    await fetch_weather(c)
                except Exception as e:
                    weather["err"] = (str(e) or type(e).__name__)[:80]
            if now - last_r > 3600:         # курсы — раз в час
                last_r = now
                try:
                    await fetch_rates(c)
                except Exception as e:
                    rates["err"] = (str(e) or type(e).__name__)[:80]
            await asyncio.sleep(30)


def refresh():
    """После смены города/валют в настройках — обновить сразу."""
    weather["t"] = 0
    asyncio.create_task(_refresh_now())


async def _refresh_now():
    async with httpx.AsyncClient(timeout=15) as c:
        for f, store in ((fetch_weather, weather), (fetch_rates, rates)):
            try:
                await f(c)
            except Exception as e:
                store["err"] = (str(e) or type(e).__name__)[:80]
