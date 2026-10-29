"""Экран платы = страница /screen в Chromium без окна. Сервер снимает её без потерь,
переводит в RGB565 (родной формат дисплея) и отдаёт плате только изменившиеся
плитки, сжатые zlib. Касания платы превращаются в клики и прокрутку в этой странице."""
import asyncio
import io
import struct
import time
import zlib
from collections import OrderedDict

import numpy as np
from PIL import Image
from playwright.async_api import async_playwright

W, H = 320, 240
TW, TH = 32, 24          # плитка: 10×10 плиток на экран, 1,5 КБ каждая до сжатия
HISTORY = 12             # сколько прошлых кадров помним, чтобы слать плате только разницу


def to_rgb565(png):
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.uint16)
    return ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)


class Screen:
    def __init__(self, url):
        self.url = url
        self.page = None
        self.png = b""                     # для превью в /admin
        self.cur = None                    # текущий кадр, uint16[H, W]
        self.frames = OrderedDict()        # версия -> кадр
        self.version = 0
        self.changed = asyncio.Event()
        self.kick = asyncio.Event()        # снять кадр немедленно (после касания)
        self.last_client = 0               # когда плата последний раз спрашивала кадр
        self._down = None                  # жест в процессе
        self._lock = asyncio.Lock()

    async def start(self):
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(args=["--disable-gpu"])
        ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
        self.page = await ctx.new_page()
        await self.page.goto(self.url)
        asyncio.create_task(self._loop())

    async def _snap(self):
        async with self._lock:
            png = await self.page.screenshot(type="png")
        img = to_rgb565(png)
        if self.cur is not None and np.array_equal(img, self.cur):
            return
        self.png = png
        self.cur = img
        self.version += 1
        self.frames[self.version] = img
        while len(self.frames) > HISTORY:
            self.frames.popitem(last=False)
        self.changed.set()

    async def _loop(self):
        while True:
            # пока плата на связи — 5 снимков/с, иначе раз в 2 с (держим кадр свежим)
            active = time.time() - self.last_client < 30
            try:
                await asyncio.wait_for(self.kick.wait(), 0.2 if active else 2)
            except asyncio.TimeoutError:
                pass
            self.kick.clear()
            try:
                await self._snap()
            except Exception:
                try:
                    await self.page.goto(self.url)    # страница упала — перезагрузить
                except Exception:
                    await asyncio.sleep(2)

    def payload(self, have):
        """Плитки, которые отличаются от кадра have (или все, если его уже не помним).
        Формат: b"CYD2", uint16 число плиток, затем для каждой
        uint16 x, y, w, h, uint32 длина, zlib(RGB565 little-endian)."""
        base = self.frames.get(have)
        cur = self.cur
        parts = []
        for y in range(0, H, TH):
            for x in range(0, W, TW):
                t = cur[y:y + TH, x:x + TW]
                if base is not None and np.array_equal(t, base[y:y + TH, x:x + TW]):
                    continue
                z = zlib.compress(t.astype("<u2").tobytes(), 6)
                parts.append(struct.pack("<HHHHI", x, y, t.shape[1], t.shape[0], len(z)) + z)
        return b"CYD2" + struct.pack("<H", len(parts)) + b"".join(parts)

    async def touch(self, kind, x, y):
        """down / move / up. Клик — при отпускании без движения, движение — прокрутка."""
        m = self.page.mouse
        async with self._lock:
            if kind == "down":
                self._down = [x, y, False, y]
                await m.move(x, y)
            elif kind == "move" and self._down:
                d = self._down
                if abs(x - d[0]) > 8 or abs(y - d[1]) > 8:
                    d[2] = True
                if d[2]:
                    await m.move(x, y)
                    await m.wheel(0, d[3] - y)     # палец вверх — текст вверх
                    d[3] = y
            elif kind == "up" and self._down:
                if not self._down[2]:
                    await m.click(self._down[0], self._down[1])
                self._down = None
        self.kick.set()

    async def wait_frame(self, have, timeout):
        """Ждать, пока версия кадра станет новее have (до timeout секунд)."""
        self.last_client = time.time()
        self.kick.set()
        if self.version == have:
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), timeout)
            except asyncio.TimeoutError:
                pass
