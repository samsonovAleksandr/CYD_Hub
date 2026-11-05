"""Клиппер / ОктоПринт через Moonraker API (обычно на том же сервере, localhost:7125).

Показывает прогресс печати, ETA, температуры стола и сопла; позволяет поставить на
паузу, возобновить и отменить печать с экрана платы.
"""
import asyncio
import os
import time

import httpx

from .state import config

MOONRAKER_DEFAULT = "http://localhost:7125"

state = {"t": 0.0, "err": "", "enabled": False, "url": MOONRAKER_DEFAULT,
         "info": {}, "job": {}, "temps": {}}
kick = asyncio.Event()


def settings():
    return config.data.get("printer") or {}


def is_on():
    c = settings()
    return bool(c.get("enabled") and c.get("url"))


def _headers():
    return {"Accept": "application/json"}


async def _get(client, path, params=None):
    r = await client.get(f"{settings()['url']}{path}", params=params, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


async def refresh(client=None):
    """Собрать информацию о принтере и текущей печати."""
    c = settings()
    if not is_on():
        state.update(t=0.0, err="", enabled=False, info={}, job={}, temps={})
        return
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        info = await _get(client, "/api/server/info")
        state["info"] = info.get("info", {}) or {}

        job = await _get(client, "/api/job/status")
        ps = job.get("print_stats", {}) or {}
        state["job"] = {
            "filename": ps.get("filename", ""),
            "state": ps.get("state", "idle"),        # printing / paused / complete / error / idle
            "progress": round((ps.get("progress") or 0) * 100, 1),
            "print_time": ps.get("print_time", 0),
            "print_time_remaining": ps.get("print_time_remaining", 0),
            "filament_used": ps.get("filament_used", 0),
        }

        objs = await _get(client, "/api/printer/objects/query",
                          params={"objects": "heater_bed,extruder,temperature_sensor,display_status"})
        o = objs.get("status", {}).get("objects", {})
        bed = o.get("heater_bed", {}) or {}
        ext = o.get("extruder", {}) or {}
        state["temps"] = {
            "bed": {"actual": round(bed.get("temperature", 0) or 0, 1),
                    "target": round(bed.get("target", 0) or 0, 1)},
            "extruder": {"actual": round(ext.get("temperature", 0) or 0, 1),
                         "target": round(ext.get("target", 0) or 0, 1)},
        }

        state.update(t=time.time(), err="", enabled=True)
    except httpx.HTTPError as e:
        state["err"] = f"Принтер недоступен: {type(e).__name__}"
        state["enabled"] = True
    except (KeyError, TypeError, ValueError) as e:
        state["err"] = f"Принтер: неверный ответ ({e})"
        state["enabled"] = True
    finally:
        if own:
            await client.aclose()


async def test(url=None):
    """Проверка соединения (используется из /admin)."""
    url = (url or settings().get("url") or "").strip() or MOONRAKER_DEFAULT
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            info = await _get(client, f"{url.rstrip('/')}/api/server/info")
            job = await _get(client, f"{url.rstrip('/')}/api/job/status")
        name = (info.get("info", {}) or {}).get("software_version", "Moonraker")
        state_text = (job.get("print_stats", {}) or {}).get("state", "idle")
        return {"ok": True, "name": name, "state": state_text}
    except httpx.HTTPError as e:
        return {"ok": False, "err": f"Принтер недоступен: {type(e).__name__}"}
    except (KeyError, TypeError, ValueError) as e:
        return {"ok": False, "err": f"Неверный ответ: {e}"}


async def command(cmd):
    """pause / resume / cancel."""
    if not is_on():
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{settings()['url']}/api/printer/print/{cmd}",
                                  headers=_headers(), timeout=10)
        if r.status_code not in (200, 204):
            state["err"] = f"Команда {cmd}: HTTP {r.status_code}"
    except httpx.HTTPError as e:
        state["err"] = f"Команда {cmd}: {type(e).__name__}"
    kick.set()


async def loop():
    while True:
        try:
            if is_on() and (not state["t"] or time.time() - state["t"] >= int(settings().get("refresh_s", 5))):
                await refresh()
            elif not is_on() and state["enabled"]:
                state.update(t=0.0, err="", enabled=False, info={}, job={}, temps={})
        except Exception:
            state["err"] = "Принтер: ошибка обновления"
        kick.clear()
        try:
            await asyncio.wait_for(kick.wait(), 2)
        except asyncio.TimeoutError:
            pass


def public():
    c = settings()
    out = dict(state)
    out["enabled"] = is_on()
    out["url"] = c.get("url", MOONRAKER_DEFAULT)
    out["refresh_s"] = int(c.get("refresh_s", 5))
    return out
