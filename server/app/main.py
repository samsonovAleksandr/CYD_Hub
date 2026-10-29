"""CYD Hub: сервер для настольного экрана CYD.

  /screen          — страница, которую видит плата (её же можно открыть в браузере)
  /admin           — настройки (логин admin, пароль из ADMIN_PASSWORD)
  /event, /perm, /usage, /ping, /state — API для bridge/claude_cyd.py (заголовок X-Token)
  /media, /media/cmd                    — музыка с компьютера: что играет и кнопки с экрана
  /api/frame, /api/touch                — API для платы (токен в ?t=)
"""
import asyncio
import base64
import hashlib
import json
import os
import secrets
import time

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import extras, monitor
from .render import Screen
from .state import DATA, FONTS, THEMES, claude, config, new_token, resolve_theme, signals

PORT = int(os.environ.get("PORT", "8770"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
STATIC = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(docs_url=None, redoc_url=None)
screen = Screen(f"http://127.0.0.1:{PORT}/screen")
basic = HTTPBasic(auto_error=False)


# ---- доступ ----------------------------------------------------------------------
def token_ok(req: Request):
    t = req.headers.get("X-Token") or req.query_params.get("t")
    if t != config.data["token"]:
        raise HTTPException(401, "bad token")


def admin_ok(req: Request, cred: HTTPBasicCredentials = Depends(basic)):
    """Админка: пароль из ADMIN_PASSWORD. Сам браузер-рендер (127.0.0.1) — без пароля."""
    if req.client and req.client.host == "127.0.0.1":
        return
    if not ADMIN_PASSWORD:
        raise HTTPException(500, "задайте ADMIN_PASSWORD в docker-compose.yml")
    if not cred or not secrets.compare_digest(cred.password, ADMIN_PASSWORD):
        raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="CYD Hub"'})


# ---- API моста Claude Code (тот же, что был на плате) ---------------------------------
@app.get("/ping", dependencies=[Depends(token_ok)])
def ping():
    return PlainTextResponse("ok")


@app.post("/event", dependencies=[Depends(token_ok)])
async def event(req: Request):
    claude.event(await req.json())
    screen.kick.set()
    return {}


@app.post("/perm", dependencies=[Depends(token_ok)])
async def perm_new(req: Request):
    claude.perm_new(await req.json())
    signals.play("perm", wake=True)
    screen.kick.set()
    return {}


@app.get("/perm", dependencies=[Depends(token_ok)])
def perm_get(id: str):
    return {"d": claude.perm_get(id)}


@app.post("/perm/cancel", dependencies=[Depends(token_ok)])
def perm_cancel(id: str):
    claude.perms.pop(id, None)
    return {}


@app.post("/usage", dependencies=[Depends(token_ok)])
async def usage(req: Request):
    claude.set_usage(await req.json())
    return {}


@app.get("/state", dependencies=[Depends(token_ok)])
def state():
    return {"sessions": list(claude.sessions.values()), "usage": claude.usage, "perms": claude.pending()}


# ---- музыка: мост присылает, что играет на компьютере, и забирает нажатия кнопок ----------
media = {}                      # app, title, artist, album, status, pos, dur, cover, t (время приёма)
covers = {}                     # id -> (bytes, тип); держим только последние
media_cmds = []
media_cmd_ready = asyncio.Event()


@app.post("/media", dependencies=[Depends(token_ok)])
async def media_set(req: Request):
    d = await req.json()
    if d.get("cover_data"):
        cid = hashlib.md5(d["cover_data"].encode()).hexdigest()[:12]
        covers[cid] = (base64.b64decode(d["cover_data"]), d.get("cover_type") or "image/jpeg")
        for old in list(covers)[:-3]:
            del covers[old]
        d["cover"] = cid
    d.pop("cover_data", None)
    d.pop("cover_type", None)
    if "cover" not in d and media.get("title") == d.get("title"):
        d["cover"] = media.get("cover", "")       # обложку шлём только при смене трека
    media.clear()
    media.update(d, t=time.time())
    screen.kick.set()
    return {}


@app.get("/media/cmd", dependencies=[Depends(token_ok)])
async def media_cmd():
    """Длинный опрос: мост ждёт здесь до 25 с, пока на экране не нажмут кнопку."""
    if not media_cmds:
        media_cmd_ready.clear()
        try:
            await asyncio.wait_for(media_cmd_ready.wait(), 25)
        except asyncio.TimeoutError:
            pass
    return {"cmds": [media_cmds.pop(0) for _ in range(len(media_cmds))]}


@app.get("/media/cover/{cid}", dependencies=[Depends(admin_ok)])
def media_cover(cid: str):
    if cid not in covers:
        raise HTTPException(404)
    data, ctype = covers[cid]
    return Response(data, media_type=ctype, headers={"Cache-Control": "max-age=86400"})


# ---- API платы ------------------------------------------------------------------------
FW_PATH = os.path.join(DATA, "firmware.bin")
FW_META = os.path.join(DATA, "firmware.json")
board = {"seen": 0, "ip": "", "fw": "", "lux": -1, "bright": 255}


def fw_meta():
    try:
        with open(FW_META) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def in_window(now_hm, a, b):
    """Попадает ли "ЧЧ:ММ" в интервал [a, b), интервал может переходить через полночь."""
    return a <= now_hm < b if a <= b else (now_hm >= a or now_hm < b)


def brightness():
    sc = config.data["screen"]
    low = max(1, min(255, round(sc["night_level"] * 255 / 100)))
    mode = sc["brightness_mode"]
    if mode == "schedule":
        return low if in_window(time.strftime("%H:%M"), sc["night_from"], sc["night_to"]) else 255
    if mode == "auto" and board["lux"] >= 0:
        dark, bright = sc["lux_dark"], sc["lux_bright"]
        k = (board["lux"] - dark) / (bright - dark) if bright != dark else 1
        return round(low + (255 - low) * max(0.0, min(1.0, k)))
    return 255


def board_headers():
    sc = config.data["screen"]
    board["bright"] = brightness()
    return {
        "X-Bright": str(board["bright"]),
        "X-Fw": fw_meta().get("md5", ""),
        "X-Led": signals.led,
        "X-Beep": f"{signals.beep_seq}:{signals.beep if sc.get('sound', True) else ''}",
        "X-Wake": str(signals.wake_seq),
        "X-Dim": str(sc.get("dim_after_s", 30)),
        "X-Off": str(sc.get("off_after_s", 300)),
    }


@app.get("/api/frame", dependencies=[Depends(token_ok)])
async def frame(req: Request, v: int = -1, beep: int = -1, lux: int = -1, fw: str = ""):
    """Долгий опрос: изменившиеся плитки кадра (см. Screen.payload), как только
    картинка поменялась, или 204 через 15 с.
    Возвращаемся раньше и при новом сигнале (писк, светодиод, пробуждение)."""
    board.update(seen=time.time(), ip=req.client.host if req.client else "", lux=lux, fw=fw)
    signals.changed.clear()
    if screen.version == v and signals.beep_seq == beep:
        frame_wait = asyncio.create_task(screen.wait_frame(v, 15))
        sig_wait = asyncio.create_task(signals.changed.wait())
        done, pending = await asyncio.wait({frame_wait, sig_wait}, timeout=15, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    else:
        screen.last_client = time.time()
    h = board_headers()
    h["X-V"] = str(screen.version)
    if screen.version != v and screen.cur is not None:
        return Response(screen.payload(v), media_type="application/octet-stream", headers=h)
    return Response(status_code=204, headers=h)


@app.get("/api/firmware", dependencies=[Depends(token_ok)])
def firmware():
    if not os.path.exists(FW_PATH):
        raise HTTPException(404)
    return FileResponse(FW_PATH, media_type="application/octet-stream")


@app.put("/api/firmware", dependencies=[Depends(admin_ok)])
async def upload_firmware(req: Request):
    """Новая прошивка для платы (.bin из Arduino). Плата скачает её сама при следующем опросе."""
    data = await req.body()
    if len(data) < 100_000 or data[0] != 0xE9:          # 0xE9 — сигнатура образа ESP32
        raise HTTPException(400, "это не прошивка ESP32 (.bin)")
    with open(FW_PATH + ".tmp", "wb") as f:
        f.write(data)
    os.replace(FW_PATH + ".tmp", FW_PATH)
    meta = {"md5": hashlib.md5(data).hexdigest(), "size": len(data), "t": time.time(),
            "name": req.headers.get("X-Name", "firmware.bin")}
    with open(FW_META, "w") as f:
        json.dump(meta, f)
    signals.changed.set()
    return meta


@app.get("/api/board", dependencies=[Depends(admin_ok)])
def board_info():
    return dict(board, online=time.time() - board["seen"] < 30, firmware=fw_meta())


@app.post("/api/touch", dependencies=[Depends(token_ok)])
async def touch(req: Request):
    # тело: "d 120 45" / "m 121 60" / "u 121 60"
    kind, x, y = (await req.body()).decode().split()
    await screen.touch({"d": "down", "m": "move", "u": "up"}[kind], int(x), int(y))
    return PlainTextResponse("ok")


# ---- данные для страницы экрана ---------------------------------------------------------
def server_view(s):
    st = monitor.state_of(s["id"])
    return {"id": s["id"], "name": s["name"], "host": s["host"], "icon": s["icon"], "watch": s["watch"], "health": st.health,
            "ping": st.ping_ms, "tcp": st.tcp_ok, "tcp_port": s["tcp_port"], "http": st.http_code,
            "stats": st.stats, "ssh_err": st.ssh_err, "checked": st.checked,
            "down_since": st.down_since, "has_ssh": bool(s["ssh_port"] and s["ssh_user"]),
            "history": [p for _, p in st.history[-48:]],
            "series": st.series[-180:], "docker": bool(s.get("docker")),
            "containers": st.containers, "docker_err": st.docker_err}


def usage_history():
    """5 ч — за сутки, неделя — за 7 дней; не больше ~150 точек на график."""
    now = time.time()
    day = [(int(t), a) for t, a, _ in claude.hist if now - t < 86400]
    week = [(int(t), b) for t, _, b in claude.hist if now - t < 7 * 86400 and b >= 0]
    thin = lambda pts: pts[::max(1, len(pts) // 150)]
    return {"day": thin(day), "week": thin(week)}


@app.get("/api/ui", dependencies=[Depends(admin_ok)])
def ui():
    claude.cleanup()
    return {
        "now": time.time(),
        "usage": claude.usage, "stale_s": config.data["usage_stale_min"] * 60,
        "sessions": sorted(claude.sessions.values(), key=lambda s: -s["t"]),
        "perms": claude.pending(),
        "servers": [server_view(s) for s in config.data["servers"]],
        "commands": [{"label": c["label"], "confirm": c["confirm"], "cmd": c["cmd"]} for c in config.data["commands"]],
        "alert": monitor.alert, "cmd": monitor.cmd_result,
        "default_view": config.data["screen"].get("default_view", "claude"),
        "servers_view": config.data["screen"]["servers_view"], "tiles_cols": config.data["screen"]["tiles_cols"],
        "theme": resolve_theme(config.data["theme"]),
        "forecast": claude.forecast(), "usage_hist": usage_history(), "today": claude.today(),
        "weather": extras.weather, "rates": extras.rates, "media": media or None,
        "wol": [{"name": w["name"]} for w in config.data["wol"]],
        "saver": {"after": config.data["screen"]["saver_after_s"], "period": config.data["screen"]["saver_period_s"]},
    }


@app.post("/api/act", dependencies=[Depends(admin_ok)])
async def act(req: Request):
    d = await req.json()
    a = d.get("a")
    if a == "perm":
        claude.answer(d["id"], d["decision"])
    elif a == "ack":
        monitor.alert["active"] = False
    elif a == "check":
        monitor.check_now()
    elif a == "cmd":
        srv = next((s for s in config.data["servers"] if s["id"] == d["server"]), None)
        if srv and not monitor.cmd_result["running"]:
            asyncio.create_task(monitor.run_command(srv, int(d["cmd"])))
    elif a == "container":
        srv = next((s for s in config.data["servers"] if s["id"] == d["server"]), None)
        if srv and not monitor.cmd_result["running"]:
            asyncio.create_task(monitor.container_action(srv, d["name"], d["action"]))
    elif a == "wol":
        i = int(d.get("i", -1))
        if 0 <= i < len(config.data["wol"]) and not monitor.cmd_result["running"]:
            asyncio.create_task(monitor.wake_on_lan(config.data["wol"][i]))
    elif a == "media" and d.get("cmd") in ("toggle", "next", "prev"):
        media_cmds.append(d["cmd"])
        media_cmd_ready.set()
        if d["cmd"] == "toggle" and media:       # сразу показать нажатие, не дожидаясь моста
            media["status"] = "paused" if media.get("status") == "playing" else "playing"
    elif a == "forget_key":
        srv = next((s for s in config.data["servers"] if s["id"] == d["server"]), None)
        if srv:
            monitor.forget_host_key(srv)
            monitor.check_now()
    screen.kick.set()
    return {}


# ---- настройки --------------------------------------------------------------------------
@app.get("/api/config", dependencies=[Depends(admin_ok)])
def get_config():
    return config.public()


@app.put("/api/config", dependencies=[Depends(admin_ok)])
async def put_config(req: Request):
    old_dash = dict(config.data["dashboard"])
    config.update(await req.json())
    monitor.check_now()
    if config.data["dashboard"] != old_dash:
        extras.refresh()
    screen.kick.set()
    return config.public()


@app.get("/api/themes", dependencies=[Depends(admin_ok)])
def themes():
    return {"presets": THEMES, "fonts": list(FONTS), "current": config.data["theme"]}


@app.put("/api/theme", dependencies=[Depends(admin_ok)])
async def put_theme(req: Request):
    config.set_theme(await req.json())
    screen.kick.set()
    return config.data["theme"]


@app.post("/api/token", dependencies=[Depends(admin_ok)])
def regen_token():
    config.data["token"] = new_token()
    config.save()
    return {"token": config.data["token"]}


@app.get("/api/preview.png", dependencies=[Depends(admin_ok)])
def preview():
    return Response(screen.png, media_type="image/png", headers={"Cache-Control": "no-store"})


# ---- страницы --------------------------------------------------------------------------
@app.get("/")
def root():
    return RedirectResponse("/admin")


@app.get("/screen")
def screen_page():
    return FileResponse(os.path.join(STATIC, "screen.html"))


@app.get("/admin", dependencies=[Depends(admin_ok)])
def admin_page():
    return FileResponse(os.path.join(STATIC, "admin.html"))


@app.get("/static/{name}")
def static(name: str):
    path = os.path.join(STATIC, os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path)


# ---- фон: мониторинг, светодиод, звуки ----------------------------------------------------
async def signal_loop():
    seen_done, last_stats = claude.done_seq, time.time()
    while True:
        claude.cleanup()
        healths = [monitor.state_of(s["id"]).health for s in config.data["servers"]]
        if claude.pending():
            led = "purple-blink"
        elif monitor.alert["active"]:
            led = "red-blink"
        elif "down" in healths:
            led = "red-slow"
        elif "warn" in healths:
            led = "yellow-flash"
        elif healths:
            led = "green-flash"
        else:
            led = "off"
        signals.set_led(led)
        if claude.done_seq != seen_done:
            seen_done = claude.done_seq
            signals.play("done")
        if time.time() - last_stats > 120:
            last_stats = time.time()
            claude.save_stats()
        await asyncio.sleep(1)


@app.on_event("startup")
async def startup():
    asyncio.create_task(monitor.loop())
    asyncio.create_task(signal_loop())
    asyncio.create_task(extras.loop())
    # браузер стартует после того, как uvicorn начнёт отвечать
    async def later():
        await asyncio.sleep(1)
        await screen.start()
    asyncio.create_task(later())
