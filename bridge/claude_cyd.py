#!/usr/bin/env python3
"""
claude_cyd.py — мост между компьютером и CYD Hub: Claude Code, лимиты, музыка.

  python claude_cyd.py install --url http://192.168.1.50 --token ABCD2345 [--wait 90]
      прописать хуки и статусную строку в ~/.claude/settings.json
  python claude_cyd.py test        проверить связь с платой
  python claude_cyd.py demo        тестовый запрос разрешения на экран платы
  python claude_cyd.py desktop     что видно в файле лимитов Claude Desktop (и отправить)
  python claude_cyd.py desktop-on  наблюдатель за лимитами Desktop + автозапуск (install делает сам)
  python claude_cyd.py desktop-off убрать автозапуск наблюдателя
  python claude_cyd.py media       что сейчас играет (музыка для вкладки CYD Hub)
  python claude_cyd.py uninstall   убрать всё из settings.json

  hook / statusline — их вызывает сам Claude Code.

Только стандартная библиотека Python. Если плата недоступна, скрипт молча
выходит и ничего не блокирует: Claude Code работает как обычно.
"""
import glob
import json
import re
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import uuid

HOME = os.path.expanduser("~")
CFG_PATH = os.environ.get("CLAUDE_CYD_CFG") or os.path.join(HOME, ".claude", "cyd.json")
SETTINGS_PATH = os.path.join(HOME, ".claude", "settings.json")
STATE_PATH = os.path.join(tempfile.gettempdir(), "claude_cyd_usage.json")
SCRIPT = os.path.abspath(__file__).replace("\\", "/")
MARK = "claude_cyd.py"

ASYNC_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                "Notification", "Stop", "SessionEnd"]
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PermissionRequest"}


# ---- связь с платой ------------------------------------------------------
def read_stdin():
    # Claude Code шлёт UTF-8, а консоль Windows по умолчанию cp1251
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    # отладка: создайте файл ~/.claude/cyd_debug — входные данные будут писаться в cyd_debug.log
    if os.path.exists(os.path.join(HOME, ".claude", "cyd_debug")):
        with open(os.path.join(HOME, ".claude", "cyd_debug.log"), "a", encoding="utf-8") as f:
            f.write(raw.strip() + "\n")
    return json.loads(raw)


def load_cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def call(cfg, method, path, data=None, timeout=1.5):
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    req = urllib.request.Request(cfg["url"].rstrip("/") + path, data=body, method=method,
                                 headers={"X-Token": cfg["token"], "Content-Type": "application/json"})
    for attempt in range(3):   # ESP32 иногда рвёт соединение — повторяем
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                txt = r.read().decode("utf-8", "replace")
            break
        except (ConnectionError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(0.15)
    try:
        return json.loads(txt)
    except ValueError:
        return txt


# ---- что показать на экране ----------------------------------------------
def short(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def rel(path, cwd):
    try:
        r = os.path.relpath(path, cwd)
        return path if r.startswith("..") else r
    except ValueError:
        return path


def tool_summary(tool, inp, cwd, full=False):
    """Одна строка для списка сессий или подробный текст для запроса разрешения."""
    inp = inp or {}
    n = 380 if full else 120
    if tool == "Bash" or tool == "PowerShell":
        cmd = inp.get("command", "")
        if full:
            desc = inp.get("description")
            return (desc + "\n" if desc else "") + "$ " + cmd[:n]
        return short(cmd, n)
    if "file_path" in inp or "notebook_path" in inp:
        p = rel(inp.get("file_path") or inp.get("notebook_path"), cwd)
        if not full:
            return p
        if "old_string" in inp:
            return f"{p}\n- {short(inp.get('old_string', ''), 140)}\n+ {short(inp.get('new_string', ''), 140)}"
        if "content" in inp:
            return f"{p}\n{inp['content'][:300]}"
        return p
    for k in ("url", "pattern", "query", "prompt", "description"):
        if k in inp:
            return short(inp[k], n)
    return short(json.dumps(inp, ensure_ascii=False), n)


def md_plain(md, limit=2500):
    """Markdown → простой текст для экрана платы. Заголовки помечаются \\x01 (плата красит их)."""
    out, in_code = [], False
    for line in md.replace("\r", "").split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            out.append("  " + line)
            continue
        h = re.match(r"^\s*#{1,6}\s+(.*)", line)
        if h:
            out.append("\x01" + h.group(1).strip())
            continue
        if re.match(r"^\s*\|?\s*:?-{3,}", line):          # разделитель таблицы
            continue
        if line.strip().startswith("|"):
            line = " · ".join(c.strip() for c in line.strip().strip("|").split("|"))
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1· ", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = line.replace("**", "").replace("__", "").replace("`", "")
        out.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def project_name(ev):
    title = ev.get("session_title")
    base = os.path.basename((ev.get("cwd") or "").rstrip("/\\")) or "?"
    return f"{base}: {title}" if title else base


# ---- хуки -----------------------------------------------------------------
def hook(cfg):
    ev = read_stdin()
    name = ev.get("hook_event_name", "")
    sid = ev.get("session_id", "")
    cwd = ev.get("cwd", "")
    tool = ev.get("tool_name", "")
    if name == "PermissionRequest":
        return permission(cfg, ev, sid, cwd, tool)

    msg = {"sid": sid, "project": project_name(ev), "event": name, "tool": tool, "text": ""}
    if name == "UserPromptSubmit":
        msg["text"] = short(ev.get("prompt", ""), 150)
        msg["prompt"] = ev.get("prompt", "")[:390]
    elif name in TOOL_EVENTS:
        msg["text"] = tool_summary(tool, ev.get("tool_input"), cwd)
    elif name == "Notification":
        msg["text"] = short(ev.get("message", ""), 150)
        msg["ntype"] = ev.get("notification_type", "")
    elif name == "Stop":
        msg["answer"] = md_plain(ev.get("last_assistant_message") or "")
        msg["text"] = short(msg["answer"].replace("\x01", ""), 150)
    call(cfg, "POST", "/event", msg, timeout=3)


def permission(cfg, ev, sid, cwd, tool):
    """Показать запрос на экране и ждать кнопку. Возвращает allow/always/deny/pass/gone/None."""
    wait = int(cfg.get("wait", 90))
    pid = uuid.uuid4().hex[:12]
    # правила, которые Claude Code предлагает добавить («всегда разрешать …») — для кнопки «Всегда»
    suggestions = ev.get("permission_suggestions") or []
    call(cfg, "POST", "/perm", {
        "id": pid, "sid": sid, "project": project_name(ev), "tool": tool, "wait": wait,
        "text": tool_summary(tool, ev.get("tool_input"), cwd, full=True),
        "suggestions": bool(suggestions),
    })
    end = time.time() + wait
    decision = None
    while time.time() < end:
        time.sleep(0.4)
        try:
            d = call(cfg, "GET", "/perm?id=" + pid).get("d")
        except Exception:
            continue          # плата моргнула Wi-Fi — пробуем дальше
        if d == "wait":
            continue
        decision = d
        break
    else:
        try:
            call(cfg, "POST", "/perm/cancel?id=" + pid)
        except Exception:
            pass

    if decision in ("allow", "always", "deny"):
        out = {"behavior": "deny" if decision == "deny" else "allow"}
        if decision == "always" and suggestions:
            out["updatedPermissions"] = suggestions
        if decision == "deny":
            out["message"] = "Пользователь отклонил это действие на CYD."
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": out}}))
    # "pass", "gone", таймаут — без решения: обычный диалог в терминале
    return decision


def demo(cfg):
    ev = {"session_id": "demo", "cwd": os.getcwd(), "tool_name": "Bash",
          "tool_input": {"command": "rm -rf node_modules && npm install", "description": "Переустановить зависимости"}}
    print(f"Запрос на экране платы, жду до {cfg.get('wait', 90)} с... (это тест, ничего не выполнится)")
    sys.stdout.flush()
    d = permission(cfg, ev, "demo", os.getcwd(), "Bash")
    names = {"allow": "Разрешить", "always": "Разрешить всегда", "deny": "Запретить", "pass": "В терминале", "gone": "запрос исчез"}
    print("Ответ с платы:", names.get(d, "не дождался (таймаут)"))
    call(cfg, "POST", "/event", {"sid": "demo", "event": "SessionEnd"})


# ---- статусная строка ------------------------------------------------------
def statusline(cfg):
    ev = read_stdin()
    rl = ev.get("rate_limits") or {}
    five = (rl.get("five_hour") or {})
    week = (rl.get("seven_day") or {})
    ctx = (ev.get("context_window") or {}).get("used_percentage")
    model = (ev.get("model") or {}).get("display_name", "")

    parts = [model] if model else []
    if ctx is not None:
        parts.append(f"ctx {int(ctx)}%")
    if five.get("used_percentage") is not None:
        parts.append(f"5ч {int(five['used_percentage'])}%")
    if week.get("used_percentage") is not None:
        parts.append(f"нед {int(week['used_percentage'])}%")
    print(" · ".join(parts))
    sys.stdout.flush()

    if not cfg:
        return
    usage = {
        "five": five.get("used_percentage", -1), "fiveReset": five.get("resets_at", 0),
        "week": week.get("used_percentage", -1), "weekReset": week.get("resets_at", 0),
        "ctx": int(ctx) if ctx is not None else -1, "model": model,
    }
    st = load_state()
    now = time.time()
    # время сброса из терминала точное — запоминаем для данных из приложения
    if usage["fiveReset"]:
        st["fiveReset"] = usage["fiveReset"]
    if usage["weekReset"]:
        st["weekReset"] = usage["weekReset"]
    # Статусная строка перезапускается по таймеру и с теми же старыми цифрами,
    # поэтому свежесть считаем от момента, когда цифры последний раз поменялись.
    last = st.get("sl", {})
    changed = last.get("usage") != usage
    changed_at = now if changed else last.get("changed", now)
    st["sl"] = {"usage": usage, "changed": changed_at, "t": last.get("t", 0)}
    # не чаще раза в 15 с, если ничего не поменялось
    if changed or now - last.get("t", 0) >= 15:
        if post_usage(cfg, st, usage, changed_at, timeout=0.8):
            st["sl"]["t"] = now
    save_state(st)


# ---- лимиты из десктопного приложения ------------------------------------------
# Claude Desktop сам пишет замеры лимитов в plan-usage-history.json (примерно раз
# в 5–15 минут): {"samples": [{"t": мс, "u": {"fh": % за 5 ч, "sd": % за 7 дней}}]}.
# Формат не документирован — если файл поменяется, просто не будет этих данных.
def desktop_history_path():
    la = os.environ.get("LOCALAPPDATA", "")
    ad = os.environ.get("APPDATA", "")
    pats = [
        os.path.join(la, "Claude-Data", "*", "plan-usage-history.json"),
        os.path.join(la, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "plan-usage-history.json"),
        os.path.join(ad, "Claude", "plan-usage-history.json"),
        os.path.join(HOME, "Library", "Application Support", "Claude", "plan-usage-history.json"),
        os.path.join(HOME, ".config", "Claude", "plan-usage-history.json"),
    ]
    found = [p for pat in pats for p in glob.glob(pat)]
    return max(found, key=os.path.getmtime) if found else None


def desktop_usage(path, remembered):
    """Последний замер + оценка времени сброса. None, если данных нет."""
    with open(path, encoding="utf-8") as f:
        samples = [s for s in json.load(f).get("samples", []) if "fh" in s.get("u", {})]
    if not samples:
        return None
    samples.sort(key=lambda s: s["t"])
    org = samples[-1].get("org")
    samples = [s for s in samples if s.get("org") == org]
    now = time.time()
    last = samples[-1]
    t_last = last["t"] / 1000

    # 5 часов: окно начинается с первого замера > 0 после нуля или падения;
    # точность ±интервал замеров, поэтому точное время из терминала в приоритете.
    five_reset = remembered.get("fiveReset", 0)
    if five_reset <= now:
        start, prev = None, None
        for s in samples:
            t, fh = s["t"] / 1000, s["u"]["fh"]
            if fh > 0 and (prev is None or prev[1] == 0 or fh < prev[1] or (start and t - start > 5 * 3600)):
                start = (prev[0] + t) / 2 if prev and t - prev[0] < 1800 else t
            prev = (t, fh)
        five_reset = int(start + 5 * 3600) if start and start + 5 * 3600 > now else 0

    # неделя: сброс по расписанию, шагаем от известного момента по 7 дней
    week_reset = remembered.get("weekReset", 0)
    if not week_reset:
        prev = None
        for s in samples:
            t, sd = s["t"] / 1000, s["u"].get("sd", 0)
            # падение видно только между частыми замерами; через большой
            # промежуток (компьютер был выключен) время сброса не угадать
            if prev and sd < prev[1] and t - prev[0] < 2 * 3600:
                week_reset = int((prev[0] + t) / 2)
            prev = (t, sd)
    while week_reset and week_reset <= now:
        week_reset += 7 * 86400

    u = last["u"]
    # история за 8 дней — для графика и прогноза на экране
    hist = [[int(s["t"] / 1000), s["u"]["fh"], s["u"].get("sd", -1)] for s in samples if now - s["t"] / 1000 < 8 * 86400]
    return {"five": u["fh"], "fiveReset": five_reset, "week": u.get("sd", -1), "weekReset": week_reset,
            "ctx": -1, "model": "Claude Desktop", "history": hist}, t_last


# ---- общее состояние и отправка ----------------------------------------------
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def post_usage(cfg, st, usage, data_time, timeout=1.5):
    """Отправить, только если данные не старее уже показанных (терминал и приложение)."""
    if data_time < st.get("posted", 0) - 1:
        return False
    try:
        call(cfg, "POST", "/usage", dict(usage, age=int(time.time() - data_time)), timeout=timeout)
    except Exception:
        return False
    st["posted"] = data_time
    return True


def desktop_tick(cfg, force=False):
    """Проверить файл приложения; отправить, если появился новый замер."""
    path = desktop_history_path()
    if not path:
        return False
    st = load_state()
    mtime = os.path.getmtime(path)
    if not force and st.get("dsk_mtime") == mtime and time.time() - st.get("dsk_t", 0) < 300:
        return False
    try:
        r = desktop_usage(path, st)
    except Exception:
        return False
    if not r:
        return False
    usage, t_data = r
    ok = post_usage(cfg, st, usage, t_data)
    st["dsk_mtime"] = mtime
    st["dsk_t"] = time.time()
    save_state(st)
    return ok


# ---- музыка: что играет на этом компьютере ----------------------------------------
# Яндекс Музыка, браузер и другие плееры сообщают Windows, что играет (как на панели
# громкости). Читаем это через WinRT и шлём в CYD Hub; кнопки с экрана забираем
# длинным опросом /media/cmd. Нужны пакеты (единственное, что не из стандартной библиотеки):
#   pip install winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation
#               winrt-Windows.Foundation.Collections winrt-Windows.Storage.Streams
STATUS = {4: "playing", 5: "paused"}


def media_app(aumid):
    a = aumid.lower()
    if "yandex" in a and "music" in a:
        return "Яндекс Музыка"
    for k, name in (("chrome", "браузер"), ("msedge", "браузер"), ("firefox", "браузер"), ("yandex", "Яндекс Браузер"),
                    ("spotify", "Spotify"), ("vlc", "VLC")):
        if k in a:
            return name
    return aumid.split("!")[0].removesuffix(".exe")


async def media_read(cover=False):
    """Что играет: словарь для /media или None. cover=True — с обложкой (base64)."""
    import base64
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    mgr = await Manager.request_async()
    sessions = list(mgr.get_sessions())
    # играющая сессия важнее той, что Windows считает текущей
    s = next((x for x in sessions if x.get_playback_info().playback_status == 4), None) or mgr.get_current_session()
    if not s:
        return None
    p = await s.try_get_media_properties_async()
    if not p.title:
        return None
    pb, tl = s.get_playback_info(), s.get_timeline_properties()
    status = STATUS.get(int(pb.playback_status), "stopped")
    pos = tl.position.total_seconds()
    if status == "playing":      # позиция обновляется не каждую секунду — досчитываем
        pos += max(0.0, time.time() - tl.last_updated_time.timestamp())
    d = {"app": media_app(s.source_app_user_model_id), "title": p.title, "artist": p.artist or p.album_artist,
         "album": p.album_title, "status": status, "pos": round(pos, 1), "dur": round(tl.end_time.total_seconds(), 1)}
    if cover and p.thumbnail:
        from winrt.windows.storage.streams import DataReader
        st = await p.thumbnail.open_read_async()
        r = DataReader(st.get_input_stream_at(0))
        await r.load_async(st.size)
        buf = bytearray(st.size)
        r.read_bytes(buf)
        d.update(cover_data=base64.b64encode(bytes(buf)).decode(), cover_type=st.content_type or "image/jpeg")
    return d


async def media_do(cmd):
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    mgr = await Manager.request_async()
    s = next((x for x in mgr.get_sessions() if x.get_playback_info().playback_status == 4), None) or mgr.get_current_session()
    if s:
        await {"toggle": s.try_toggle_play_pause_async, "next": s.try_skip_next_async,
               "prev": s.try_skip_previous_async}[cmd]()


def media_watch():
    """Фоновые потоки наблюдателя: раз в секунду смотрим плеер, кнопки ждём от хаба."""
    import asyncio
    try:
        import winrt.windows.media.control  # noqa: F401
    except ImportError:
        return
    cmds = []

    def poll_cmds():
        while True:
            cfg = load_cfg()
            try:
                cmds.extend(call(cfg, "GET", "/media/cmd", timeout=35).get("cmds", []))
            except Exception:
                time.sleep(5)

    threading.Thread(target=poll_cmds, daemon=True).start()
    last, sent_at = None, 0
    while True:
        cfg = load_cfg()
        try:
            while cmds:
                asyncio.run(media_do(cmds.pop(0)))
                time.sleep(0.3)          # плеер не сразу сообщает новое состояние
            d = asyncio.run(media_read())
            key = d and (d["title"], d["artist"], d["status"])
            # позиция «уплыла» — перемотали
            jumped = d and last and d["status"] == "playing" and abs(d["pos"] - (last["pos"] + time.time() - sent_at)) > 3
            if key != (last and (last["title"], last["artist"], last["status"])) or jumped or time.time() - sent_at > 20:
                new_track = d and not (last and (last["title"], last["artist"]) == (d["title"], d["artist"]))
                body = asyncio.run(media_read(cover=True)) if new_track else d
                call(cfg, "POST", "/media", body or {"status": "none"}, timeout=5)
                last, sent_at = d, time.time()
        except Exception:
            pass
        time.sleep(1)


WATCH_PORT = 47321   # занят = наблюдатель уже запущен


def watch(cfg):
    """Фоновый процесс: раз в минуту смотрит файл десктопного приложения."""
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", WATCH_PORT))
    except OSError:
        return   # уже работает
    threading.Thread(target=media_watch, daemon=True).start()
    url = None
    while True:
        # настройки перечитываем каждый раз: после install с новым адресом
        # наблюдатель сразу шлёт туда, без перезапуска
        cfg = load_cfg() or cfg
        try:
            desktop_tick(cfg, force=cfg.get("url") != url)
            url = cfg.get("url")
        except Exception:
            pass
        time.sleep(60)


# ---- установка ---------------------------------------------------------------
def is_ours(handler):
    return MARK in handler.get("command", "") or any(MARK in a for a in handler.get("args", []))


def load_settings():
    if not os.path.exists(SETTINGS_PATH):
        return {}
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        txt = f.read().strip()
    return json.loads(txt) if txt else {}


def save_settings(s):
    if os.path.exists(SETTINGS_PATH):
        shutil.copy(SETTINGS_PATH, SETTINGS_PATH + ".bak")
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
        f.write("\n")


def strip_ours(s):
    hooks = s.get("hooks", {})
    for evname in list(hooks):
        groups = []
        for g in hooks[evname]:
            g["hooks"] = [h for h in g.get("hooks", []) if not is_ours(h)]
            if g["hooks"]:
                groups.append(g)
        if groups:
            hooks[evname] = groups
        else:
            del hooks[evname]
    if not hooks:
        s.pop("hooks", None)
    sl = s.get("statusLine")
    if sl and MARK in sl.get("command", ""):
        del s["statusLine"]


def shell_path(p):
    p = p.replace("\\", "/")
    return f'"{p}"' if " " in p else p


def install(args):
    opts = dict(zip(args[::2], args[1::2]))
    url, token = opts.get("--url"), opts.get("--token")
    if not url or not token:
        sys.exit("нужно: install --url http://IP_платы --token ТОКЕН [--wait 90]")
    if not url.startswith("http"):
        url = "http://" + url
    cfg = {"url": url, "token": token.upper(), "wait": int(opts.get("--wait", 90))}
    try:
        r = call(cfg, "GET", "/ping", timeout=3)
    except Exception as e:
        sys.exit(f"плата не отвечает ({e}). Проверьте IP и токен на экране Claude.")
    if r != "ok":
        sys.exit(f"неожиданный ответ платы: {r}")
    os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    py = sys.executable
    s = load_settings()
    strip_ours(s)
    hooks = s.setdefault("hooks", {})
    for evname in ASYNC_EVENTS:
        h = {"type": "command", "command": py, "args": [SCRIPT, "hook"], "async": True, "timeout": 5}
        g = {"hooks": [h]}
        if evname in TOOL_EVENTS:
            g["matcher"] = "*"
        hooks.setdefault(evname, []).append(g)
    hooks.setdefault("PermissionRequest", []).append({
        "matcher": "*",
        "hooks": [{"type": "command", "command": py, "args": [SCRIPT, "hook"], "timeout": cfg["wait"] + 15}],
    })
    if "statusLine" in s:
        print("! statusLine уже настроен — не трогаю. Лимиты на плате будут только с ним:")
        print(f"  {shell_path(py)} {shell_path(SCRIPT)} statusline")
    else:
        s["statusLine"] = {"type": "command", "command": f"{shell_path(py)} {shell_path(SCRIPT)} statusline",
                           "refreshInterval": 30}
    save_settings(s)
    print(f"Готово: плата {url} отвечает, хуки записаны в {SETTINGS_PATH}")
    print("Новые сессии Claude Code подхватят их сами (уже открытые — после перезапуска).")
    desktop_on()


# ---- автозапуск наблюдателя (лимиты из десктопного приложения) ---------------
def startup_file():
    ad = os.environ.get("APPDATA")
    if not ad:
        return None
    return os.path.join(ad, "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "claude_cyd_watch.vbs")


def pythonw():
    w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return w if os.path.exists(w) else sys.executable


def desktop_on():
    if not desktop_history_path():
        print("Файл лимитов Claude Desktop не найден — наблюдатель не нужен.")
        return
    sf = startup_file()
    if not sf:
        print(f"Автозапуск только для Windows. Запускайте вручную: {sys.executable} {SCRIPT} watch")
        return
    # .vbs запускает pythonw без окна
    with open(sf, "w", encoding="utf-16") as f:
        f.write(f'CreateObject("WScript.Shell").Run """{pythonw()}"" ""{SCRIPT}"" watch", 0, False\n')
    import subprocess
    subprocess.Popen([pythonw(), SCRIPT, "watch"], close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print("Лимиты из Claude Desktop: наблюдатель запущен и добавлен в автозагрузку")
    print("  ", sf)


def desktop_off():
    sf = startup_file()
    if sf and os.path.exists(sf):
        os.remove(sf)
        print("Убран из автозагрузки:", sf)
    # остановить запущенный наблюдатель
    try:
        socket.create_connection(("127.0.0.1", WATCH_PORT), timeout=1).close()
    except OSError:
        return
    print("Наблюдатель остановится после перезагрузки (или завершите pythonw.exe в диспетчере задач).")


def uninstall():
    s = load_settings()
    strip_ours(s)
    save_settings(s)
    if os.path.exists(CFG_PATH):
        os.remove(CFG_PATH)
    print("Хуки CYD убраны из", SETTINGS_PATH)
    desktop_off()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        return install(sys.argv[2:])
    if cmd == "uninstall":
        return uninstall()
    cfg = load_cfg()
    if cmd == "test":
        if not cfg:
            sys.exit("не настроено: сначала install")
        print("плата:", call(cfg, "GET", "/ping", timeout=3))
        return
    if cmd == "demo":
        if not cfg:
            sys.exit("не настроено: сначала install")
        return demo(cfg)
    if cmd == "desktop-on":
        return desktop_on()
    if cmd == "desktop-off":
        return desktop_off()
    if cmd == "desktop":     # разово: что видно в файле приложения
        p = desktop_history_path()
        print("файл:", p)
        if p:
            u, t = desktop_usage(p, load_state())
            print("замер:", time.strftime("%d.%m %H:%M", time.localtime(t)), u)
            for k in ("fiveReset", "weekReset"):
                if u[k]:
                    print(f"  {k}:", time.strftime("%a %d.%m %H:%M", time.localtime(u[k])))
            if cfg:
                print("отправлено на плату:", desktop_tick(cfg, force=True))
        return
    if cmd == "media":       # разово: что видит мост и отправить на экран
        import asyncio
        d = asyncio.run(media_read(cover=True))
        if d and d.get("cover_data"):
            print("обложка:", d["cover_type"], len(d["cover_data"]) * 3 // 4, "байт")
        print({k: v for k, v in (d or {}).items() if k != "cover_data"} or "ничего не играет")
        if cfg:
            call(cfg, "POST", "/media", d or {"status": "none"}, timeout=5)
            print("отправлено в CYD Hub")
        return
    if cmd == "watch":
        if cfg:
            watch(cfg)
        return
    if cmd == "statusline":
        try:
            statusline(cfg)
        except Exception:
            pass
        return
    if cmd == "hook":
        if not cfg:
            return
        try:
            hook(cfg)
        except Exception:
            pass      # плата недоступна — Claude Code работает как обычно
        return
    print(__doc__)


if __name__ == "__main__":
    # консоль Windows: печатаем UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
