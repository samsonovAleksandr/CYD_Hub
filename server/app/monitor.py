"""Мониторинг серверов: ping / TCP / HTTP, нагрузка, Docker и команды по SSH, Wake-on-LAN."""
import asyncio
import json
import os
import re
import time

import asyncssh
import httpx

from .state import DATA, config, signals

KNOWN_HOSTS = os.path.join(DATA, "known_hosts")   # ключи серверов, запоминаются при первом входе
HISTORY_PATH = os.path.join(DATA, "history.json")
DOCKER_CMD = "docker ps -a --format '{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Image}}'"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")

STATS_CMD = (
    "echo $(cut -d' ' -f1 /proc/loadavg) $(nproc) "
    "$(free -m | awk 'NR==2{print $2, $3}') "
    "$(df -P / | awk 'NR==2{print $5+0}') "
    "$(cut -d. -f1 /proc/uptime)"
)


class ServerState:
    def __init__(self):
        self.health = "unknown"     # unknown / up / warn / down
        self.ping_ms = None         # None = не проверяли, -1 = нет ответа
        self.tcp_ok = None
        self.http_code = None
        self.stats = None           # dict(load, cpus, mem_total, mem_used, disk, uptime)
        self.ssh_err = ""
        self.checked = 0
        self.down_since = 0
        self.fails = 0
        self.history = []           # [(t, ping_ms)] за последние сутки
        self.series = []            # [(t, cpu %, ram %, диск %)] за сутки — графики
        self.containers = None      # [{name, state, status, image}] — если включён Docker
        self.docker_err = ""
        self.stopped_by_us = set()  # контейнеры, остановленные кнопкой: их падением не считаем


states = {}            # id сервера -> ServerState
alert = {"active": False, "text": ""}
cmd_result = {"running": False, "server": "", "label": "", "output": "", "code": None, "t": 0}
_check_now = asyncio.Event()


def state_of(sid):
    return states.setdefault(sid, ServerState())


# ---- история для графиков: переживает перезапуск ----------------------------------
def load_history():
    try:
        with open(HISTORY_PATH) as f:
            h = json.load(f)
    except (OSError, ValueError):
        return
    for sid, v in h.items():
        st = state_of(sid)
        st.history = [tuple(x) for x in v.get("ping", [])]
        st.series = [tuple(x) for x in v.get("series", [])]


def save_history():
    h = {sid: {"ping": st.history, "series": st.series} for sid, st in states.items()}
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(h, f)
    os.replace(tmp, HISTORY_PATH)


def check_now():
    _check_now.set()


# ---- проверки ------------------------------------------------------------------
async def ping(host):
    try:
        p = await asyncio.create_subprocess_exec(
            "ping", "-c", "3", "-i", "0.3", "-W", "2", host,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), 10)
    except Exception:
        return -1
    times = [float(x) for x in re.findall(rb"time=([\d.]+)", out)]
    return int(min(times)) if times else -1


async def tcp(host, port):
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), 3)
        w.close()
        return True
    except Exception:
        return False


async def http(url):
    try:
        async with httpx.AsyncClient(verify=False, timeout=6, follow_redirects=False) as c:
            return (await c.get(url)).status_code
    except Exception:
        return 0


# ---- SSH ---------------------------------------------------------------------------
async def ssh_run(srv, command, timeout=30):
    """Вернуть (код, вывод). Ключ хоста: доверие при первом входе, потом сверка (как known_hosts)."""
    if not srv.get("ssh_port") or not srv.get("ssh_user"):
        raise RuntimeError("SSH не настроен")
    first = not _known(srv)
    kw = dict(host=srv["host"], port=srv["ssh_port"], username=srv["ssh_user"],
              known_hosts=None if first else KNOWN_HOSTS, connect_timeout=10)
    if srv.get("ssh_key"):
        kw["client_keys"] = [asyncssh.import_private_key(srv["ssh_key"])]
    elif srv.get("ssh_password"):
        kw["password"] = srv["ssh_password"]
        kw["client_keys"] = None
    else:
        raise RuntimeError("нет пароля и ключа")
    conn = await asyncio.wait_for(asyncssh.connect(**kw), 15)
    if first:
        key = conn.get_server_host_key()
        if key:
            with open(KNOWN_HOSTS, "a") as f:
                f.write(f"{_hostspec(srv)} {key.export_public_key().decode().strip()}\n")
    async with conn:
        r = await asyncio.wait_for(conn.run(command, check=False), timeout)
    out = (r.stdout or "") + (r.stderr or "")
    return r.exit_status if r.exit_status is not None else 0, out


def _hostspec(srv):
    return srv["host"] if srv["ssh_port"] == 22 else f"[{srv['host']}]:{srv['ssh_port']}"


def _known(srv):
    if not os.path.exists(KNOWN_HOSTS):
        return False
    spec = _hostspec(srv) + " "
    with open(KNOWN_HOSTS) as f:
        return any(line.startswith(spec) for line in f)


def forget_host_key(srv):
    if not os.path.exists(KNOWN_HOSTS):
        return
    spec = _hostspec(srv) + " "
    with open(KNOWN_HOSTS) as f:
        lines = [l for l in f if not l.startswith(spec)]
    with open(KNOWN_HOSTS, "w") as f:
        f.writelines(lines)


async def read_stats(srv, st):
    try:
        code, out = await ssh_run(srv, STATS_CMD, 20)
        v = out.split()
        st.stats = {"load": float(v[0]), "cpus": max(1, int(v[1])), "mem_total": int(v[2]),
                    "mem_used": int(v[3]), "disk": int(v[4]), "uptime": int(v[5])}
        st.ssh_err = ""
        now = time.time()
        s = st.stats
        st.series.append((int(now), round(s["load"] / s["cpus"] * 100),
                          round(s["mem_used"] * 100 / max(1, s["mem_total"])), s["disk"]))
        st.series = [p for p in st.series if now - p[0] < 86400]
    except asyncssh.HostKeyNotVerifiable:
        st.ssh_err = "КЛЮЧ СЕРВЕРА ИЗМЕНИЛСЯ!"
    except asyncssh.PermissionDenied:
        st.ssh_err = "вход отклонён"
    except (IndexError, ValueError):
        st.ssh_err = "не Linux?"
    except Exception as e:
        st.ssh_err = (str(e) or type(e).__name__)[:60]


async def read_docker(srv, st):
    """Список контейнеров. Был запущен, а теперь нет (и не мы остановили) — тревога."""
    try:
        code, out = await ssh_run(srv, DOCKER_CMD, 20)
    except Exception as e:
        st.docker_err = (str(e) or type(e).__name__)[:60]
        return
    if code != 0:
        st.docker_err = out.strip()[:60] or f"код {code}"
        return
    st.docker_err = ""
    new = []
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) >= 4:
            new.append({"name": p[0], "state": p[1], "status": p[2], "image": p[3]})
    new.sort(key=lambda c: (c["state"] == "running", c["name"]))
    if st.containers is not None:
        was = {c["name"] for c in st.containers if c["state"] == "running"}
        fell = [c["name"] for c in new
                if c["name"] in was and c["state"] != "running" and c["name"] not in st.stopped_by_us]
        # важный контейнер был запущен и исчез из списка (удалён, а не остановлен)
        names = {c["name"] for c in new}
        fell += [n for n in srv.get("watch", []) if n in was and n not in names]
        if fell:
            alert.update(active=True, text=f"Контейнер упал: {', '.join(fell)[:40]}")
            signals.play("down", wake=True)
    st.stopped_by_us &= {c["name"] for c in new if c["state"] != "running"}
    st.containers = new


async def check_server(srv, with_stats):
    st = state_of(srv["id"])
    checks = []
    if srv["ping"]:
        st.ping_ms = await ping(srv["host"])
        checks.append(st.ping_ms >= 0)
    else:
        st.ping_ms = None
    if srv["tcp_port"]:
        st.tcp_ok = await tcp(srv["host"], srv["tcp_port"])
        checks.append(st.tcp_ok)
    else:
        st.tcp_ok = None
    if srv["http_url"]:
        st.http_code = await http(srv["http_url"])
        checks.append(0 < st.http_code < 400)
    else:
        st.http_code = None
    st.checked = time.time()
    if st.ping_ms is not None:
        st.history.append((st.checked, st.ping_ms))
        st.history = [h for h in st.history if st.checked - h[0] < 86400]

    all_bad = bool(checks) and not any(checks)
    st.fails = st.fails + 1 if all_bad else 0
    prev = st.health
    if all_bad:
        h = "down" if st.fails >= config.data["fails_to_down"] else ("warn" if prev != "unknown" else "unknown")
    elif not all(checks):
        h = "warn"
    else:
        h = "up"
    if h != "down" and with_stats and srv["stats"] and srv["ssh_port"]:
        await read_stats(srv, st)
    if h != "down" and with_stats and srv.get("docker") and srv["ssh_port"]:
        await read_docker(srv, st)
    if h == "up" and (st.ssh_err or _overloaded(st.stats)):
        h = "warn"
    if h == "down" and prev != "down":
        st.down_since = time.time()
        st.stats = None
        alert.update(active=True, text=f"Не отвечает: {srv['name']}")
        signals.play("down", wake=True)
    if h != "down" and prev == "down":
        st.down_since = 0
        signals.play("up")
    st.health = h


def _overloaded(s):
    if not s:
        return False
    return s["load"] / s["cpus"] > 1.5 or s["mem_used"] * 100 >= 90 * max(1, s["mem_total"]) or s["disk"] >= 90


async def run_command(srv, idx):
    c = config.data["commands"][idx]
    cmd_result.update(running=True, server=srv["name"], label=c["label"], output="", code=None, t=time.time())
    try:
        code, out = await ssh_run(srv, c["cmd"], 60)
        cmd_result.update(output=out, code=code)
    except Exception as e:
        cmd_result.update(output=f"Ошибка: {e}", code=-1)
    cmd_result["running"] = False
    signals.play("done" if cmd_result["code"] == 0 else "fail")


async def container_action(srv, name, action):
    """restart / stop / start / logs для контейнера; вывод — в тот же экран, что у команд."""
    if not NAME_RE.match(name) or action not in ("restart", "stop", "start", "logs"):
        return
    label = {"restart": "перезапуск", "stop": "остановка", "start": "запуск", "logs": "логи"}[action]
    cmd = f"docker logs --tail 80 {name} 2>&1" if action == "logs" else f"docker {action} {name}"
    st = state_of(srv["id"])
    if action == "stop":
        st.stopped_by_us.add(name)
    cmd_result.update(running=True, server=name, label=label, output="", code=None, t=time.time())
    try:
        code, out = await ssh_run(srv, cmd, 90)
        cmd_result.update(output=out, code=code)
    except Exception as e:
        cmd_result.update(output=f"Ошибка: {e}", code=-1)
    cmd_result["running"] = False
    signals.play("done" if cmd_result["code"] == 0 else "fail")
    if action != "logs":
        await read_docker(srv, st)


def ssh_ready(s):
    return bool(s["ssh_port"] and s["ssh_user"] and (s["ssh_password"] or s["ssh_key"]))


async def wake_on_lan(entry):
    """Магический пакет шлём с сервера из настроек: из своей Docker-сети контейнер
    широковещательно в домашнюю сеть не достучится."""
    mac = entry["mac"].upper()
    servers = config.data["servers"]
    srv = next((s for s in servers if s["id"] == entry.get("via") and ssh_ready(s)), None) or \
        next((s for s in servers if ssh_ready(s)), None)
    cmd_result.update(running=True, server=entry["name"], label="включение", output="", code=None, t=time.time())
    if not MAC_RE.match(mac) or not srv:
        cmd_result.update(running=False, output="Нет сервера с SSH, через который отправить пакет", code=-1)
        return
    py = ("import socket;m=bytes.fromhex('%s');s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
          "s.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);"
          "[s.sendto(b'\\xff'*6+m*16,('255.255.255.255',p)) for p in (7,9)];print('ok')") % mac.replace(":", "")
    try:
        code, out = await ssh_run(srv, f'python3 -c "{py}"', 20)
        cmd_result.update(output=f"Магический пакет для {mac} отправлен через «{srv['name']}»: {out.strip()}", code=code)
    except Exception as e:
        cmd_result.update(output=f"Ошибка: {e}", code=-1)
    cmd_result["running"] = False
    signals.play("done" if cmd_result["code"] == 0 else "fail")


async def loop():
    load_history()
    last_stats, last_save = {}, time.time()
    while True:
        forced = _check_now.is_set()
        _check_now.clear()
        servers = list(config.data["servers"])
        for sid in [k for k in states if k not in {s["id"] for s in servers}]:
            del states[sid]

        async def one(srv):
            due = forced or time.time() - last_stats.get(srv["id"], 0) >= config.data["stats_interval"]
            if due:
                last_stats[srv["id"]] = time.time()
            try:
                await check_server(srv, due)
            except Exception as e:
                state_of(srv["id"]).ssh_err = str(e)[:60]

        await asyncio.gather(*(one(s) for s in servers))
        if not any(state_of(s["id"]).health == "down" for s in servers) and not alert["text"].startswith("Контейнер"):
            alert["active"] = False
        if time.time() - last_save > 300:
            last_save = time.time()
            save_history()
        try:
            await asyncio.wait_for(_check_now.wait(), config.data["check_interval"])
        except asyncio.TimeoutError:
            pass
