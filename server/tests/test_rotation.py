"""Run inside the Hub image: python /tmp/rotation/test_rotation.py."""
import asyncio
import io
import struct
import unittest
import zlib
from unittest.mock import AsyncMock

import numpy as np
from PIL import Image

try:
    from app.render import Screen, W, H, to_rgb565
except ModuleNotFoundError as exc:      # тест гоняется внутри образа Hub
    raise unittest.SkipTest(f"нет зависимости {exc.name}") from exc


class RotationTest(unittest.IsolatedAsyncioTestCase):
    async def test_frame_preview_tiles_and_touch(self):
        image = Image.new("RGB", (W, H), "black")
        image.putpixel((0, 0), (255, 0, 0))
        image.putpixel((W - 1, H - 1), (0, 255, 0))
        output = io.BytesIO()
        image.save(output, format="PNG")
        rotation = 0
        screen = Screen("unused", lambda: rotation)
        screen.page = AsyncMock()
        screen.page.screenshot.return_value = output.getvalue()
        await screen._snap()
        original = screen.cur.copy()
        version = screen.version
        await screen.touch("down", 15, 20)
        rotation = 180
        await screen._snap()
        self.assertIsNone(screen._down)
        np.testing.assert_array_equal(screen.cur, original[::-1, ::-1])
        np.testing.assert_array_equal(to_rgb565(screen.png), screen.cur)
        payload = screen.payload(version)
        self.assertEqual(payload[:4], b"CYD2")
        count, = struct.unpack_from("<H", payload, 4)
        offset = 6
        for _ in range(count):
            x, y, w, h, size = struct.unpack_from("<HHHHI", payload, offset)
            offset += 12
            tile = np.frombuffer(zlib.decompress(payload[offset:offset+size]), dtype="<u2").reshape(h, w)
            original[y:y+h, x:x+w] = tile
            offset += size
        np.testing.assert_array_equal(original, screen.cur)
        await screen.touch("down", 0, 0)
        await screen.touch("up", 0, 0)
        screen.page.mouse.click.assert_awaited_with(319, 239)
        await screen.touch("down", 100, 100)
        await screen.touch("move", 100, 120)
        screen.page.mouse.wheel.assert_awaited_with(0, 20)
        await screen.touch("up", 100, 120)
        rotation = 0
        await screen._snap()
        await screen.touch("down", 15, 20)
        await screen.touch("up", 15, 20)
        screen.page.mouse.click.assert_awaited_with(15, 20)
        self.assertEqual(screen.png, output.getvalue())


if __name__ == "__main__":
    unittest.main()
