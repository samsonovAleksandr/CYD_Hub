import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_cyd import AppServer, BridgeError, call_hub, normalize_limits


def window(used=25, minutes=300, reset=2000000000):
    return {"usedPercent": used, "windowDurationMins": minutes, "resetsAt": reset}


def result(primary=None, secondary=None):
    return {"rateLimits": {"limitId": "codex", "primary": primary, "secondary": secondary}}


class LimitsTests(unittest.TestCase):
    def test_standard_windows(self):
        d = normalize_limits(result(window(), window(42, 10080)))
        self.assertEqual((d["five"], d["week"]), (25, 42))
        self.assertEqual(d["fiveReset"], 2000000000)

    def test_weekly_primary_is_not_five_hours(self):
        d = normalize_limits(result(window(31, 10080)))
        self.assertEqual((d["five"], d["week"], d["fiveReset"]), (-1, 31, 0))
        self.assertEqual(len(d["windows"]), 1)

    def test_unknown_duration_and_missing_windows(self):
        d = normalize_limits(result(window(0, 15, None)))
        self.assertEqual(d["five"], -1)
        self.assertEqual(d["windows"][0]["label"], "15 мин")
        self.assertEqual(d["windows"][0]["used"], 0)
        self.assertEqual(normalize_limits(result())["windows"], [])

    def test_multiple_buckets_select_exactly(self):
        d = {"rateLimitsByLimitId": {"other": result(window(90))["rateLimits"],
                                    "codex": result(window(20))["rateLimits"]}}
        self.assertEqual(normalize_limits(d)["five"], 20)
        with self.assertRaises(BridgeError):
            normalize_limits(d, "missing")

    def test_bad_values_do_not_become_zero(self):
        for bad in [None, -2, 101, float("nan"), True, "23"]:
            with self.subTest(bad=bad), self.assertRaises(BridgeError):
                normalize_limits(result(window(bad)))
        with self.assertRaises(BridgeError):
            normalize_limits({})

    def test_http_payload_and_token(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, self.headers.get("X-Token"),
                                 json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
            def log_message(self, *args):
                pass
        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.handle_request)
            worker.start()
            d = normalize_limits(result(window(29), window(42, 10080)))
            call_hub({"url": f"http://127.0.0.1:{server.server_port}", "token": "test-only"}, "/usage", d)
            worker.join(3)
        self.assertEqual(received, [("/usage", "test-only", d)])

    def test_rpc_handshake_notifications_and_cleanup(self):
        fake = '''import sys,json
def read(): return json.loads(sys.stdin.readline())
def send(x): print(json.dumps(x),flush=True)
m=read(); assert m['method']=='initialize'
send({'id':m['id'],'result':{}})
assert read()['method']=='initialized'
m=read(); assert m['method']=='account/rateLimits/read'
send({'method':'account/rateLimits/updated','params':{}})
send({'id':m['id'],'result':{'rateLimits':{'limitId':'codex','primary':{'usedPercent':7,'windowDurationMins':10080},'secondary':None}}})
sys.stdin.read()
'''
        with AppServer(command=[sys.executable, "-u", "-c", fake], timeout=3) as server:
            self.assertEqual(normalize_limits(server.request("account/rateLimits/read"))["week"], 7)
        self.assertIsNotNone(server.proc.poll())

    def test_rpc_timeout_and_exit(self):
        for fake in ["import time; time.sleep(5)", "pass"]:
            server = AppServer(command=[sys.executable, "-u", "-c", fake], timeout=0.1)
            with self.assertRaises(BridgeError):
                with server:
                    pass
            self.assertIsNotNone(server.proc.poll())


class HubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.previous = os.environ.get("CYDHUB_DATA")
        os.environ["CYDHUB_DATA"] = cls.tmp.name
        spec = importlib.util.spec_from_file_location(
            "test_hub_state", Path(__file__).resolve().parents[2] / "server/app/state.py")
        cls.state = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.state)

    @classmethod
    def tearDownClass(cls):
        if cls.previous is None:
            os.environ.pop("CYDHUB_DATA", None)
        else:
            os.environ["CYDHUB_DATA"] = cls.previous
        cls.tmp.cleanup()

    def setUp(self):
        self.state.config.data["usage_provider"] = "codex"

    def test_old_watcher_cannot_overwrite_codex(self):
        hub = self.state.Claude()
        hub.set_usage(normalize_limits(result(window(10))))
        timestamp = hub.usage["t"]
        self.assertFalse(hub.set_usage({"five": 99, "week": 99}))
        self.assertEqual((hub.usage["five"], hub.usage["t"]), (10, timestamp))

    def test_weekly_only_history_and_provider_switch(self):
        hub = self.state.Claude()
        self.assertTrue(hub.set_usage(normalize_limits(result(window(31, 10080)))))
        self.assertEqual(hub.hist[0][1:], (-1, 31))
        self.state.config.data["usage_provider"] = "claude"
        hub.set_usage({"five": 12, "week": 22})
        self.assertEqual(len(hub.hist), 1)
        self.assertEqual(hub.hist[0][1:], (12, 22))


if __name__ == "__main__":
    unittest.main()
