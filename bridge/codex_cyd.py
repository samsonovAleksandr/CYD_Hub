#!/usr/bin/env python3
"""Read Codex subscription limits through app-server; publish to CYD /usage.

Only Python's standard library is required. No model calls, hooks, or direct
credential-file access. See README.md for installation and background operation.
"""
import argparse
import json
import math
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request


def config_path():
    return Path(os.environ.get("CODEX_CYD_CFG") or
                Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "cyd.json")


class BridgeError(Exception):
    pass


def resolve_codex(executable):
    resolved = shutil.which(executable)
    if resolved:
        return resolved
    if executable == "codex" and os.name == "nt":
        # Startup processes retain an old PATH when the desktop app updates.
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "OpenAI/Codex/bin"
        candidates = [p for p in root.glob("*/codex.exe") if p.is_file()]
        if candidates:
            return str(max(candidates, key=lambda p: p.stat().st_mtime))
    return executable


class AppServer:
    """One stdio connection. Requests have deadlines; subprocess always reaped."""
    def __init__(self, executable="codex", timeout=30, command=None):
        self.timeout = timeout
        self.seq = 0
        self.messages = queue.Queue()
        self.command = command or [resolve_codex(executable),
                                   "app-server", "--listen", "stdio://"]
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.request("initialize", {"clientInfo": {
                "name": "cyd_limits", "title": "CYD Codex Limits", "version": "1.0.0"}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.messages.put(None)

    def send(self, message):
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise BridgeError("Codex app-server disconnected") from exc

    def request(self, method, params=None):
        self.seq += 1
        rid = self.seq
        message = {"id": rid, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        end = time.monotonic() + self.timeout
        while True:
            try:
                reply = self.messages.get(timeout=max(0, end - time.monotonic()))
            except queue.Empty as exc:
                raise BridgeError(f"Codex timed out: {method}") from exc
            if reply is None:
                raise BridgeError("Codex app-server exited; check codex login status")
            if not isinstance(reply, dict):
                continue
            if "method" in reply:
                if "id" in reply:
                    # This client cannot answer approval or credential requests.
                    self.send({"id": reply["id"], "error": {
                        "code": -32601, "message": "Read-only limits client"}})
                continue
            if reply.get("id") != rid:
                continue
            if "error" in reply:
                error = reply["error"]
                raise BridgeError(f"{method}: {error.get('message', 'request failed')}")
            if not isinstance(reply.get("result"), dict):
                raise BridgeError(f"Invalid Codex response: {method}")
            return reply["result"]

    def __exit__(self, *_):
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait(timeout=3)
            self.reader.join(timeout=2)
            self.proc.stdout.close()


def number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def normalize_limits(result, limit_id="codex"):
    """Map by duration, never assume primary=5h (weekly-only accounts exist)."""
    buckets = result.get("rateLimitsByLimitId") or {}
    snapshot = buckets.get(limit_id)
    if snapshot is None:
        legacy = result.get("rateLimits") or {}
        if legacy.get("limitId", "codex") == limit_id:
            snapshot = legacy
    if not isinstance(snapshot, dict) or not snapshot:
        raise BridgeError(f"No quota bucket {limit_id!r}; available: {', '.join(buckets) or 'none'}")
    payload = {"provider": "codex", "source": "Codex app-server", "model": "Codex",
               "limitId": limit_id, "plan": snapshot.get("planType"),
               "five": -1, "week": -1, "fiveReset": 0, "weekReset": 0,
               "ctx": -1, "age": 0, "windows": []}
    for key in ("primary", "secondary"):
        window = snapshot.get(key)
        if window is None:
            continue
        if not isinstance(window, dict):
            raise BridgeError("Invalid quota window")
        used, mins, reset = (window.get(k) for k in
                             ("usedPercent", "windowDurationMins", "resetsAt"))
        if not number(used) or not 0 <= used <= 100:
            raise BridgeError("Invalid usedPercent")
        if mins is not None and (not number(mins) or mins <= 0):
            raise BridgeError("Invalid windowDurationMins")
        if reset is not None and (not number(reset) or reset < 0):
            raise BridgeError("Invalid resetsAt")
        label = ("5 часов" if mins == 300 else "Неделя" if mins == 10080 else
                 f"{mins / 60:g} ч" if mins and mins % 60 == 0 else
                 f"{mins:g} мин" if mins else "Окно лимита")
        payload["windows"].append({"id": key, "label": label, "minutes": mins,
                                   "used": used, "reset": reset or 0})
        compat = {300: "five", 10080: "week"}.get(mins)
        if compat:
            payload[compat], payload[compat + "Reset"] = used, reset or 0
    payload["windows"].sort(key=lambda w: w["minutes"] or float("inf"))
    return payload


def load_config():
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BridgeError("Run install --url URL --token TOKEN first") from exc


def call_hub(cfg, path, payload=None):
    req = urllib.request.Request(cfg["url"].rstrip("/") + path,
                                 data=None if payload is None else json.dumps(payload).encode(),
                                 headers={"X-Token": cfg["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.read().decode("utf-8")


def save_config(cfg):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_watch(cfg, executable, limit_id, interval, media=False):
    # Separate from the old Claude watcher. Closing the process releases the lock.
    with socket.socket() as lock:
        try:
            lock.bind(("127.0.0.1", 47322))
        except OSError as exc:
            raise BridgeError("Codex CYD watcher already running (port 47322)") from exc
        if media:
            import claude_cyd as legacy
            legacy.CFG_PATH = str(config_path())
            threading.Thread(target=legacy.media_watch, daemon=True).start()
        while True:
            try:
                with AppServer(executable) as server:
                    while True:
                        cfg = load_config()
                        payload = normalize_limits(server.request("account/rateLimits/read"), limit_id)
                        call_hub(cfg, "/usage", payload)
                        print(time.strftime("%H:%M:%S"), "Codex limits sent", flush=True)
                        time.sleep(interval)
            except (OSError, BridgeError, ValueError) as exc:
                print(f"Retry in {interval}s: {exc}", file=sys.stderr, flush=True)
                # No fabricated zeroes and no timestamp refresh after a failure.
                time.sleep(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "status", "once", "watch", "test"])
    parser.add_argument("--url")
    parser.add_argument("--token")
    parser.add_argument("--codex", help="Path to codex executable")
    parser.add_argument("--limit-id", help="Quota bucket (default: codex)")
    parser.add_argument("--interval", type=int, help="Polling seconds, minimum 30 (default: 60)")
    parser.add_argument("--media", action="store_true", help="Also run Windows music bridge")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            with AppServer(args.codex or "codex") as server:
                payload = normalize_limits(server.request("account/rateLimits/read"), args.limit_id or "codex")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.command == "install":
            if not args.url or not args.token:
                raise BridgeError("install requires --url and --token")
            url = args.url if "://" in args.url else "http://" + args.url
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
                raise BridgeError("Use an HTTP(S) URL without credentials, query or fragment")
            cfg = {"url": url.rstrip("/"), "token": args.token,
                   "codex": args.codex or "codex", "limit_id": args.limit_id or "codex",
                   "interval": max(30, args.interval or 60), "media": args.media}
            if call_hub(cfg, "/ping").strip() != "ok":
                raise BridgeError("Unexpected CYD /ping response")
            with AppServer(cfg["codex"]) as server:
                payload = normalize_limits(server.request("account/rateLimits/read"), cfg["limit_id"])
            call_hub(cfg, "/usage", payload)
            save_config(cfg)
            print(f"Configured: {config_path()}\nStart: python bridge/codex_cyd.py watch")
            return 0
        cfg = load_config()
        if args.command == "test":
            print(call_hub(cfg, "/ping"))
            return 0
        executable, limit_id = args.codex or cfg.get("codex", "codex"), args.limit_id or cfg.get("limit_id", "codex")
        if args.command == "watch":
            run_watch(cfg, executable, limit_id, max(30, args.interval or cfg.get("interval", 60)),
                      args.media or cfg.get("media", False))
        else:
            with AppServer(executable) as server:
                payload = normalize_limits(server.request("account/rateLimits/read"), limit_id)
            call_hub(cfg, "/usage", payload)
            print("Codex limits sent")
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, BridgeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
