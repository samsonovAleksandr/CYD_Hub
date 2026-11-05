"""Weeek: мои задачи и встречи через Public API.

Токен создаётся в Weeek → «Настройки пространства» → API. Встречи в Weeek —
это задачи с type = meet или call, поэтому отдельный календарный API не нужен.
"""
import asyncio
import os
import time
from datetime import date, datetime, time as dtime, timedelta

import httpx

from .state import config

API = os.environ.get("WEEEEK_API", "https://api.weeek.net/public/v1").rstrip("/")
MAX_TASKS, MAX_MEETINGS = 40, 10

# что видит страница /screen; t — когда данные последний раз обновились
state = {"t": 0.0, "err": "", "enabled": False, "tasks": [], "meetings": [], "badge": 0}
kick = asyncio.Event()
_projects = {}


def settings():
    return config.data.get("weeek") or {}


def is_on():
    c = settings()
    return bool(c.get("enabled") and c.get("token"))


def _ts(value):
    """ISO 8601 → секунды. Пустое или мусор → None."""
    if not value or not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.astimezone()
    return d.timestamp()


def _day(value):
    """Дата из Y-m-d или dd.mm.yyyy (Weeek встречает оба формата)."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _midnight(d):
    return datetime.combine(d, dtime.min).timestamp()


def _at(d, hm):
    """Дата + «ЧЧ:ММ» → секунды (старое поле time)."""
    if not d or not hm or not isinstance(hm, str):
        return None
    try:
        h, m = hm.strip()[:5].split(":")
        return datetime.combine(d, dtime(int(h), int(m))).timestamp()
    except (ValueError, TypeError):
        return None


def _moment(task, *date_keys):
    """Первый подходящий момент времени: ISO-поле, затем дата (+ время)."""
    for key in date_keys:
        ts = _ts(task.get(key))
        if ts is not None:
            # ISO-дата без времени приходит как полночь: считаем её датой
            return ts, len(task.get(key, "").strip()) > 10
    for key in date_keys:
        d = _day(task.get(key))
        if d:
            ts = _at(d, task.get("time") or task.get("timeStart"))
            return (ts if ts is not None else _midnight(d)), ts is not None
    return None, False


def _project_name(task):
    locs = task.get("locations") or []
    pid = task.get("projectId")
    if pid is None and locs and isinstance(locs[0], dict):
        pid = locs[0].get("projectId")
    return _projects.get(pid, "")


def parse(raw, projects=None, days=7, now=None):
    """Сырые задачи Weeek → плоские строки для экрана. Чистая функция, без сети."""
    global _projects
    if projects is not None:
        _projects = projects
    now = time.time() if now is None else now
    today = date.fromtimestamp(now)
    horizon = today + timedelta(days=days)
    tasks, meetings = [], []

    for t in raw or []:
        kind = t.get("type") or "action"
        title = (t.get("title") or t.get("name") or "").strip()
        if not title or t.get("isDeleted"):
            continue
        prio = t.get("priority")
        prio = int(prio) if isinstance(prio, (int, float)) else 0
        row = {"id": t.get("id"), "title": title, "project": _project_name(t), "prio": prio,
               "ts": None, "has_time": False}

        if kind in ("meet", "call"):
            ts, has_time = _moment(t, "startDateTime", "dueDateTime", "startDate", "dueDate", "date")
            if ts is None:
                continue
            if ts < now - 3600 or date.fromtimestamp(ts) > horizon:
                continue
            row.update(ts=ts, has_time=has_time)
            meetings.append(row)
            continue

        ts, has_time = _moment(t, "dueDateTime", "dueDate", "date")
        day = date.fromtimestamp(ts) if ts is not None else None
        if day is not None and day > horizon:
            continue
        overdue = bool(t.get("overdue")) or (day is not None and day < today)
        if day is None:
            bucket = "none"
        elif overdue:
            bucket = "overdue"
        elif day == today:
            bucket = "today"
        else:
            bucket = "later"
        row.update(ts=ts, has_time=has_time, day=day.isoformat() if day else None, bucket=bucket)
        tasks.append(row)

    order = {"overdue": 0, "today": 1, "none": 2, "later": 3}
    tasks.sort(key=lambda r: (order[r["bucket"]], r["ts"] or 0, -r["prio"]))
    meetings.sort(key=lambda r: r["ts"] or 0)
    tasks, meetings = tasks[:MAX_TASKS], meetings[:MAX_MEETINGS]
    return {"tasks": tasks, "meetings": meetings,
            "badge": sum(1 for r in tasks if r["bucket"] in ("overdue", "today"))}


def public():
    c = settings()
    out = dict(state)
    out["enabled"] = is_on()
    out["days"] = int(c.get("days", 7))
    return out


def _error(r):
    if r.status_code == 401:
        return "токен Weeek не принят"
    if r.status_code == 403:
        return "нет доступа к задачам Weeek"
    return f"Weeek: HTTP {r.status_code}"


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


async def refresh(client=None):
    """Забрать /user/me, /tm/projects и /tm/tasks. Ошибка не стирает старые данные."""
    c = settings()
    if not is_on():
        state.update(t=0.0, err="", enabled=False, tasks=[], meetings=[], badge=0)
        return
    token = c["token"]
    days = int(c.get("days", 7))
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        r = await client.get(f"{API}/user/me", headers=_headers(token))
        if r.status_code != 200:
            state["err"] = _error(r)
            state["enabled"] = True
            return
        user = (r.json() or {}).get("user") or {}
        me = user.get("id")

        r = await client.get(f"{API}/tm/projects", headers=_headers(token))
        projects = {}
        if r.status_code == 200:
            projects = {p.get("id"): (p.get("name") or p.get("title") or "") for p in (r.json() or {}).get("projects") or []}

        params = {"completed": "0", "perPage": 100, "sortBy": "date"}
        if me:
            params["userId"] = me
        r = await client.get(f"{API}/tm/tasks", params=params, headers=_headers(token))
        if r.status_code != 200:
            state["err"] = _error(r)
            state["enabled"] = True
            return
        raw = (r.json() or {}).get("tasks") or []

        state.update(parse(raw, projects, days), t=time.time(), err="", enabled=True)
    except httpx.HTTPError as e:
        state["err"] = f"Weeek недоступен: {type(e).__name__}"
        state["enabled"] = True
    finally:
        if own:
            await client.aclose()


async def complete(task_id):
    """Отметить задачу выполненной и убрать её с экрана, не дожидаясь обновления."""
    if not is_on():
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{API}/tm/tasks/{int(task_id)}/complete",
                                  content=b"", headers=_headers(settings()["token"]))
        if r.status_code not in (200, 204):
            state["err"] = _error(r)
        else:
            for key in ("tasks", "meetings"):
                state[key] = [x for x in state[key] if str(x.get("id")) != str(task_id)]
            state["badge"] = sum(1 for x in state["tasks"] if x["bucket"] in ("overdue", "today"))
    except (httpx.HTTPError, ValueError):
        state["err"] = "не удалось отметить задачу"
    kick.set()


async def test(token=None):
    """Проверка токена из /admin: кто я и сколько задач видно."""
    token = (token or settings().get("token") or "").strip()
    if not token:
        return {"ok": False, "err": "токен не задан"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{API}/user/me", headers=_headers(token))
            if r.status_code != 200:
                return {"ok": False, "err": _error(r)}
            user = (r.json() or {}).get("user") or {}
            name = " ".join(x for x in (user.get("firstName"), user.get("lastName")) if x) or user.get("email") or "Weeek"
            r = await client.get(f"{API}/tm/tasks", params={"completed": "0", "perPage": 1},
                                 headers=_headers(token))
            if r.status_code != 200:
                return {"ok": False, "err": _error(r)}
            tasks = len((r.json() or {}).get("tasks") or [])
            return {"ok": True, "user": name, "tasks": tasks}
    except httpx.HTTPError as e:
        return {"ok": False, "err": f"Weeek недоступен: {type(e).__name__}"}


async def loop():
    """Обновляем раз в refresh_s секунд; kick — сразу после смены настроек."""
    while True:
        try:
            if is_on() and (not state["t"] or time.time() - state["t"] >= int(settings().get("refresh_s", 120))):
                await refresh()
            elif not is_on() and state["enabled"]:
                state.update(t=0.0, err="", enabled=False, tasks=[], meetings=[], badge=0)
        except Exception:
            state["err"] = "Weeek: ошибка обновления"
        kick.clear()
        try:
            await asyncio.wait_for(kick.wait(), 5)
        except asyncio.TimeoutError:
            pass
