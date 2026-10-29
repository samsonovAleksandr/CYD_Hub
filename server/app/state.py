"""Состояние CYD Hub: настройки (JSON на диске), Claude Code, сигналы для экрана."""
import asyncio
import json
import os
import re
import secrets
import time

DATA = os.environ.get("CYDHUB_DATA", "/data")
CFG_PATH = os.path.join(DATA, "config.json")

DEFAULT_COMMANDS = [
    {"label": "Uptime", "cmd": "uptime; echo; who", "confirm": False},
    {"label": "Диски", "cmd": "df -h -x tmpfs -x devtmpfs -x overlay", "confirm": False},
    {"label": "Топ CPU", "cmd": "ps -eo pcpu,pmem,comm --sort=-pcpu | head -15", "confirm": False},
    {"label": "Docker", "cmd": "docker ps --format '{{.Names}}: {{.Status}}'", "confirm": False},
    {"label": "Температура", "cmd": "cat /sys/class/thermal/thermal_zone*/temp | awk '{print $1/1000 \" C\"}'", "confirm": False},
    {"label": "Перезагрузка", "cmd": "sudo systemctl reboot", "confirm": True},
]

SERVER_DEFAULTS = {
    "name": "", "host": "", "ping": True, "tcp_port": 22, "http_url": "",
    "ssh_port": 22, "ssh_user": "monitor", "ssh_password": "", "ssh_key": "", "stats": True,
    "docker": False, "icon": "server",
    "watch": [],        # важные контейнеры — на вкладке Docker
}

SCREEN_DEFAULTS = {
    "dim_after_s": 0, "off_after_s": 0, "sound": True, "default_view": "claude",
    # яркость: max — всегда максимум, schedule — ночью тусклее, auto — по датчику света платы
    "brightness_mode": "max", "night_from": "23:00", "night_to": "07:00", "night_level": 25,
    "lux_dark": 2000, "lux_bright": 100,
    # заставка: после простоя экран сам листает вкладки
    "saver_after_s": 300, "saver_period_s": 20,
    # страница серверов: list — карточки с нагрузкой, tiles — квадратные плитки с иконками
    "servers_view": "tiles", "tiles_cols": 0,     # 0 — подобрать, чтобы все влезли
}
DASH_DEFAULTS = {"city": "Москва", "lat": 55.7558, "lon": 37.6173, "rates": "USD,EUR,CNY"}


# ---- темы экрана -------------------------------------------------------------
# Цвета и шрифты попадают в CSS-переменные страницы /screen (--bg, --panel, …).
# Особые эффекты темы (свечение, рамки, капслок) — в screen.html по [data-theme=…].
FONTS = {
    "Inter": "Inter, 'DejaVu Sans', sans-serif",
    "JetBrains Mono": "'JetBrains Mono', 'DejaVu Sans Mono', monospace",
    "Pixelify Sans": "'Pixelify Sans', 'DejaVu Sans', sans-serif",
    "Press Start 2P": "'Press Start 2P', 'DejaVu Sans Mono', monospace",
    "Russo One": "'Russo One', 'DejaVu Sans', sans-serif",
    "Exo 2": "'Exo 2', 'DejaVu Sans', sans-serif",
    "DejaVu Sans": "'DejaVu Sans', sans-serif",
}
COLOR_KEYS = ["bg", "panel", "panel2", "line", "text", "dim", "faint", "accent", "green", "yellow", "red", "purple"]

THEMES = {
    "night": {"name": "Ночь", "font": "Inter", "radius": 9, "colors": {
        "bg": "#0d1016", "panel": "#161b24", "panel2": "#212836", "line": "#2a3242", "text": "#e9ecf3",
        "dim": "#8b94a7", "faint": "#5c6578", "accent": "#5aa2ff", "green": "#3ecf8e", "yellow": "#f5b942",
        "red": "#ef5b5b", "purple": "#a78bfa"}},
    "claude": {"name": "Claude Code", "font": "JetBrains Mono", "radius": 6, "colors": {
        "bg": "#1a1918", "panel": "#262523", "panel2": "#34322f", "line": "#3d3a36", "text": "#f0eee6",
        "dim": "#a39e93", "faint": "#6f6a62", "accent": "#d97757", "green": "#7fb069", "yellow": "#e6b450",
        "red": "#e5534b", "purple": "#c792ea"}},
    "pixel": {"name": "Pixel Art", "font": "Pixelify Sans", "radius": 0, "colors": {
        "bg": "#1a1c2c", "panel": "#29366f", "panel2": "#3b5dc9", "line": "#41a6f6", "text": "#f4f4f4",
        "dim": "#94b0c2", "faint": "#566c86", "accent": "#41a6f6", "green": "#38b764", "yellow": "#ffcd75",
        "red": "#b13e53", "purple": "#a7f070"}},
    "starwars": {"name": "Star Wars", "font": "Russo One", "radius": 2, "colors": {
        "bg": "#000000", "panel": "#0b0b0b", "panel2": "#1c1a0a", "line": "#ffe81f", "text": "#ffe81f",
        "dim": "#bfae17", "faint": "#6b6210", "accent": "#4bd5ee", "green": "#7cfc00", "yellow": "#ffe81f",
        "red": "#ff2a2a", "purple": "#ff4fd8"}},
    "matrix": {"name": "Матрица", "font": "JetBrains Mono", "radius": 0, "colors": {
        "bg": "#000a02", "panel": "#03140a", "panel2": "#06261a", "line": "#0b4d26", "text": "#5cff8a",
        "dim": "#22b14c", "faint": "#11602a", "accent": "#a6ffbf", "green": "#5cff8a", "yellow": "#d4ff5c",
        "red": "#ff5c5c", "purple": "#5cffe1"}},
    "synthwave": {"name": "Synthwave", "font": "Exo 2", "radius": 10, "colors": {
        "bg": "#14072b", "panel": "#241047", "panel2": "#3a1a6b", "line": "#ff2a6d", "text": "#fdf0ff",
        "dim": "#c39bd8", "faint": "#7a5a99", "accent": "#05d9e8", "green": "#3bf4a0", "yellow": "#f9c80e",
        "red": "#ff2a6d", "purple": "#d16bff"}},
    "paper": {"name": "Бумага", "font": "Inter", "radius": 8, "colors": {
        "bg": "#f4f1ea", "panel": "#ffffff", "panel2": "#e6e1d6", "line": "#d6cfc0", "text": "#22201c",
        "dim": "#6e685d", "faint": "#a39c8e", "accent": "#2f6fd6", "green": "#2e9e5b", "yellow": "#c98a0b",
        "red": "#d64545", "purple": "#7b5cd6"}},
    "nord": {"name": "Nord", "font": "Inter", "radius": 8, "colors": {
        "bg": "#2e3440", "panel": "#3b4252", "panel2": "#434c5e", "line": "#4c566a", "text": "#eceff4",
        "dim": "#a3abba", "faint": "#6b7487", "accent": "#88c0d0", "green": "#a3be8c", "yellow": "#ebcb8b",
        "red": "#bf616a", "purple": "#b48ead"}},
}


def resolve_theme(t):
    """Тема из настроек: пресет + ваши правки поверх."""
    base = THEMES.get(t.get("preset"), THEMES["night"])
    colors = dict(base["colors"])
    colors.update({k: v for k, v in (t.get("colors") or {}).items() if k in COLOR_KEYS})
    font = t.get("font") or base["font"]
    return {"preset": t.get("preset", "night"), "colors": colors,
            "font": FONTS.get(font, FONTS["Inter"]), "font_name": font,
            "radius": int(t.get("radius", base["radius"]))}


def new_token():
    abc = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(abc) for _ in range(8))


class Config:
    def __init__(self):
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        d = self.data
        d.setdefault("token", new_token())
        d.setdefault("servers", [])
        d.setdefault("commands", DEFAULT_COMMANDS)
        d.setdefault("check_interval", 30)
        d.setdefault("stats_interval", 120)
        d.setdefault("fails_to_down", 2)
        d.setdefault("usage_stale_min", 25)
        d.setdefault("theme", {"preset": "night"})
        d.setdefault("wol", [])
        for key, defaults in (("screen", SCREEN_DEFAULTS), ("dashboard", DASH_DEFAULTS)):
            d.setdefault(key, {})
            for k, v in defaults.items():
                d[key].setdefault(k, v)
        for s in d["servers"]:
            for k, v in SERVER_DEFAULTS.items():
                s.setdefault(k, v)
            s.setdefault("id", secrets.token_hex(4))
        self.save()

    def save(self):
        os.makedirs(DATA, exist_ok=True)
        tmp = CFG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CFG_PATH)

    def public(self):
        """Настройки для веб-страницы: пароли и ключи не отдаём, только «задан ли»."""
        d = json.loads(json.dumps(self.data))
        for s in d["servers"]:
            s["ssh_password_set"] = bool(s.pop("ssh_password", ""))
            s["ssh_key_set"] = bool(s.pop("ssh_key", ""))
        return d

    def update(self, new):
        """Принять настройки со страницы. Пустой пароль/ключ = оставить прежний."""
        old = {s["id"]: s for s in self.data["servers"]}
        servers = []
        for s in new.get("servers", []):
            o = old.get(s.get("id"), {})
            m = dict(SERVER_DEFAULTS)
            m.update({k: s[k] for k in SERVER_DEFAULTS if k in s})
            m["id"] = s.get("id") or secrets.token_hex(4)
            for secret in ("ssh_password", "ssh_key"):
                if s.get(secret + "_clear"):
                    m[secret] = ""
                elif not s.get(secret):
                    m[secret] = o.get(secret, "")
            m["watch"] = sorted({str(x).strip() for x in m["watch"] or [] if str(x).strip()})[:30]
            m["tcp_port"] = int(m["tcp_port"] or 0)
            m["ssh_port"] = int(m["ssh_port"] or 0)
            if not m["name"]:
                m["name"] = m["host"]
            if m["host"]:
                servers.append(m)
        self.data["servers"] = servers
        self.data["commands"] = [
            {"label": c["label"], "cmd": c["cmd"], "confirm": bool(c.get("confirm"))}
            for c in new.get("commands", []) if c.get("label") and c.get("cmd")
        ][:6]
        for k in ("check_interval", "stats_interval", "fails_to_down", "usage_stale_min"):
            if k in new:
                self.data[k] = max(1, int(new[k]))
        for key, defaults in (("screen", SCREEN_DEFAULTS), ("dashboard", DASH_DEFAULTS)):
            if key in new:
                for k, v in new[key].items():
                    if k in defaults:
                        self.data[key][k] = type(defaults[k])(v) if not isinstance(defaults[k], bool) else bool(v)
        if "wol" in new:
            self.data["wol"] = [
                {"name": w.get("name") or w["mac"], "mac": w["mac"].strip().upper().replace("-", ":"), "via": w.get("via", "")}
                for w in new["wol"] if re.fullmatch(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", (w.get("mac") or "").strip())
            ][:6]
        self.save()

    def set_theme(self, t):
        """Тема: пресет, правки цветов, шрифт, скругление."""
        th = {"preset": t.get("preset") if t.get("preset") in THEMES else "night"}
        cols = {k: v for k, v in (t.get("colors") or {}).items()
                if k in COLOR_KEYS and isinstance(v, str) and len(v) == 7 and v.startswith("#")}
        if cols:
            th["colors"] = cols
        if t.get("font") in FONTS:
            th["font"] = t["font"]
        if t.get("radius") is not None:
            th["radius"] = max(0, min(16, int(t["radius"])))
        self.data["theme"] = th
        self.save()


# ---- Claude Code ------------------------------------------------------------
MAX_SESS, MAX_ACTS = 8, 10
STATS_PATH = os.path.join(DATA, "stats.json")


class Claude:
    def __init__(self):
        self.sessions = {}     # sid -> dict
        self.perms = {}        # id -> dict
        self.usage = None
        self.hist = []         # [(t, % за 5 ч, % за неделю)] — для графика и прогноза
        self.done_seq = 0
        self.perm_seq = 0
        try:
            with open(STATS_PATH, encoding="utf-8") as f:
                self.stats = json.load(f)
        except (OSError, ValueError):
            self.stats = {}

    # ---- статистика за день: запросы, сессии, проекты, разрешения ----
    def _day(self):
        day = time.strftime("%Y-%m-%d")
        st = self.stats.setdefault(day, {"prompts": 0, "tools": 0, "allowed": 0, "denied": 0, "sessions": [], "projects": {}})
        for old in sorted(self.stats)[:-14]:      # храним две недели
            del self.stats[old]
        return st

    def save_stats(self):
        tmp = STATS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.stats, f, ensure_ascii=False)
        os.replace(tmp, STATS_PATH)

    def today(self):
        st = self._day()
        top = sorted(st["projects"].items(), key=lambda kv: -kv[1])[:3]
        return {"prompts": st["prompts"], "tools": st["tools"], "allowed": st["allowed"], "denied": st["denied"],
                "sessions": len(st["sessions"]), "projects": top}

    def _sess(self, sid, project=None):
        s = self.sessions.get(sid)
        if not s:
            if len(self.sessions) >= MAX_SESS:
                del self.sessions[min(self.sessions, key=lambda k: self.sessions[k]["t"])]
            s = self.sessions[sid] = {"sid": sid, "project": "?", "state": "idle", "text": "",
                                      "prompt": "", "answer": "", "acts": [], "t": time.time()}
        if project:
            s["project"] = project
        return s

    def _drop_perms(self, sid):
        for pid in [p for p, v in self.perms.items() if v["sid"] == sid and v["decision"] == "wait"]:
            del self.perms[pid]

    def _act(self, s, a):
        s["acts"].append(a[:160])
        s["acts"] = s["acts"][-MAX_ACTS:]
        s["n_acts"] = s.get("n_acts", 0) + 1

    def event(self, d):
        sid, ev = d.get("sid", ""), d.get("event", "")
        if not sid:
            return
        if ev == "SessionEnd":
            self.sessions.pop(sid, None)
            self._drop_perms(sid)
            return
        s = self._sess(sid, d.get("project"))
        tool, text = d.get("tool", ""), d.get("text", "")
        if ev == "SessionStart":
            s.update(state="idle", text="новая сессия")
        elif ev == "UserPromptSubmit":
            s.update(state="working", text=text, prompt=d.get("prompt") or text, answer="", acts=[], n_acts=0)
            st = self._day()
            st["prompts"] += 1
            if sid not in st["sessions"]:
                st["sessions"].append(sid)
            st["projects"][s["project"]] = st["projects"].get(s["project"], 0) + 1
        elif ev in ("PreToolUse", "PostToolUse"):
            if ev == "PostToolUse":
                self._drop_perms(sid)
            s.update(state="working", text=f"{tool}: {text}")
            if ev == "PreToolUse":
                self._act(s, f"{tool}: {text}")
                self._day()["tools"] += 1
        elif ev == "Notification":
            s.update(state="done" if d.get("ntype") == "idle_prompt" else "waiting", text=text)
        elif ev == "Stop":
            self._drop_perms(sid)
            s.update(state="done", answer=d.get("answer") or text)
            if text:
                s["text"] = text
            self.done_seq += 1
        s["t"] = time.time()

    def perm_new(self, d):
        if len(self.perms) >= 6:
            del self.perms[min(self.perms, key=lambda k: self.perms[k]["t"])]
        p = {"id": d.get("id", ""), "sid": d.get("sid", ""), "project": d.get("project", "?"),
             "tool": d.get("tool", "?"), "text": d.get("text", ""), "wait": int(d.get("wait", 90)),
             "t": time.time(), "decision": "wait",
             # Claude Code предложил правило («всегда разрешать …») — можно кнопку «Всегда»
             "always": bool(d.get("suggestions"))}
        self.perms[p["id"]] = p
        s = self._sess(p["sid"], p["project"])
        s.update(state="waiting", text=f"ждёт разрешения: {p['tool']}", t=time.time())
        self._act(s, f"? {p['tool']}: {p['text']}")
        self.perm_seq += 1

    def perm_get(self, pid):
        p = self.perms.get(pid)
        if not p:
            return "gone"
        if p["decision"] != "wait":
            del self.perms[pid]
        return p["decision"]

    def answer(self, pid, decision):
        """decision: allow / always / deny / pass (решать в терминале)."""
        p = self.perms.get(pid)
        if not p or p["decision"] != "wait" or decision not in ("allow", "always", "deny", "pass"):
            return False
        p["decision"] = decision
        s = self.sessions.get(p["sid"])
        if s and decision != "pass":
            ok = decision != "deny"
            s["state"] = "working" if ok else "done"
            self._act(s, ("+ разрешено всегда с экрана" if decision == "always" else "+ разрешено с экрана") if ok else "- запрещено с экрана")
            s["t"] = time.time()
            self._day()["allowed" if ok else "denied"] += 1
        return True

    def set_usage(self, d):
        self.usage = {k: v for k, v in d.items() if k != "history"}
        self.usage["t"] = time.time() - int(d.get("age", 0))
        if d.get("history"):
            # мост присылает замеры Claude Desktop: [[t, 5ч, неделя], ...]
            self.hist = sorted((float(t), float(a), float(b)) for t, a, b in d["history"])[-2000:]
        point = (self.usage["t"], float(d.get("five", -1)), float(d.get("week", -1)))
        if point[1] >= 0 and (not self.hist or point[0] > self.hist[-1][0] + 30):
            self.hist.append(point)

    def forecast(self):
        """Когда при нынешнем темпе кончится лимит (unix-время) — или None.
        5 ч: наклон по замерам текущего окна за последний час; неделя — за сутки."""
        u = self.usage
        if not u or not self.hist:
            return {}
        now, out = time.time(), {}
        for key, idx, span, reset in (("five", 1, 3600, u.get("fiveReset", 0)), ("week", 2, 86400, u.get("weekReset", 0))):
            pts = [(t, v[idx]) for v in self.hist for t in [v[0]] if now - t <= span and v[idx] >= 0]
            # только текущее окно: после падения процента (сброса) старые точки не считаем
            for i in range(len(pts) - 1, 0, -1):
                if pts[i][1] < pts[i - 1][1]:
                    pts = pts[i:]
                    break
            if len(pts) < 2 or pts[-1][0] - pts[0][0] < 600:
                continue
            rate = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])     # % в секунду
            cur = float(u.get(key, -1))
            if rate <= 0 or cur < 0:
                out[key] = {"rate_h": 0}
                continue
            eta = now + (100 - cur) / rate
            out[key] = {"eta": eta, "before_reset": bool(reset) and eta < reset, "rate_h": round(rate * 3600, 1)}
        return out

    def cleanup(self):
        now = time.time()
        for pid in [k for k, p in self.perms.items() if now - p["t"] > p["wait"] + 5]:
            del self.perms[pid]
        for sid in [k for k, s in self.sessions.items() if now - s["t"] > 3 * 3600]:
            del self.sessions[sid]

    def pending(self):
        return sorted((p for p in self.perms.values() if p["decision"] == "wait"), key=lambda p: p["t"])


# ---- Сигналы для платы: светодиод, звук, пробуждение -----------------------------
class Signals:
    """Плата получает их в заголовках каждого ответа /api/frame."""

    def __init__(self):
        self.led = "off"
        self.beep_seq, self.beep = 0, ""
        self.wake_seq = 0
        self.changed = asyncio.Event()

    def set_led(self, mode):
        if mode != self.led:
            self.led = mode
            self.changed.set()

    def play(self, pattern, wake=False):
        self.beep_seq += 1
        self.beep = pattern
        if wake:
            self.wake_seq += 1
        self.changed.set()


config = Config()
claude = Claude()
signals = Signals()
