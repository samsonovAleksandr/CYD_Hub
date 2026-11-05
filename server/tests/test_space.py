import asyncio
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

package = types.ModuleType("space_test_app")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "app")]
sys.modules[package.__name__] = package
state = types.ModuleType("space_test_app.state")
state.config = SimpleNamespace(data={"screen": {"show_iss": False, "iss_interval_s": 15},
                                   "dashboard": {"lat": 0, "lon": 0}})
sys.modules[state.__name__] = state
from space_test_app import space


class SpaceTests(unittest.IsolatedAsyncioTestCase):
    def test_kp_formats_and_validation(self):
        rows = [{"time_tag": "2026-11-03T06:00:00", "Kp": 2},
                {"time_tag": "2026-11-03T09:00:00Z", "Kp": 5.67}]
        result = space.kp_rows(rows)
        self.assertEqual(result[-1][1], 5.67)
        self.assertEqual(result[-1][0] - result[0][0], 10800)
        self.assertEqual(space.kp_rows([["time_tag", "Kp"], [rows[0]['time_tag'], "2"]]), result[:1])
        with self.assertRaises(ValueError):
            space.kp_rows([{"time_tag": "bad", "Kp": 999}])

    async def test_disabled_worker_makes_no_requests(self):
        fetch = AsyncMock()
        store = {"t": 123, "lat": 50}
        task = asyncio.create_task(space.worker(None, store, fetch, 'show_iss', 'iss_interval_s', 1, asyncio.Event()))
        await asyncio.sleep(.01)
        fetch.assert_not_awaited()
        self.assertEqual(store, {"t": 0, "err": ""})
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_iss_units_and_local_distance(self):
        def handle(request):
            self.assertNotIn('lat', request.url.params)
            self.assertNotIn('lon', request.url.params)
            return httpx.Response(200, json={"latitude": 0, "longitude": 0,
                "altitude": 420, "velocity": 27600, "timestamp": 1793692800})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await space.fetch_iss(client)
        data = space.public()['iss']
        self.assertEqual(data['distance'], 0)
        self.assertEqual(data['speed'], 27600)
        self.assertEqual(data['altitude'], 420)


if __name__ == '__main__':
    unittest.main()
