"""Grabs one frame of a bot's screen through the hub's WebSocket bridge and saves it as a PPM.

Usage: screen_grab.py WS_URL OUT.ppm   (WS_URL includes a fresh ?ticket=). Test tool only.
"""
import asyncio
import struct
import sys

import websockets


class Stream:
    def __init__(self, ws):
        self.ws, self.buf = ws, bytearray()

    async def read(self, n):
        while len(self.buf) < n:
            self.buf += await asyncio.wait_for(self.ws.recv(), 15)
        out, self.buf = bytes(self.buf[:n]), self.buf[n:]
        return out


async def main(url, out):
    async with websockets.connect(url, subprotocols=["binary"], max_size=None) as ws:
        s = Stream(ws)
        await s.read(12)
        await ws.send(b"RFB 003.008\n")
        n = (await s.read(1))[0]
        types = await s.read(n)
        await ws.send(bytes([1]))                       # security: None
        if struct.unpack(">I", await s.read(4))[0] != 0:
            raise SystemExit("security failed")
        await ws.send(bytes([1]))                       # ClientInit: shared
        w, h = struct.unpack(">HH", await s.read(4))
        await s.read(16)                                 # server pixel format
        name_len = struct.unpack(">I", await s.read(4))[0]
        name = (await s.read(name_len)).decode(errors="replace")
        # 32bpp little-endian true colour, red at shift 16
        pf = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        await ws.send(b"\x00\x00\x00\x00" + pf)
        await ws.send(struct.pack(">BxHi", 2, 1, 0))     # SetEncodings: raw
        await ws.send(struct.pack(">BBHHHH", 3, 0, 0, 0, w, h))
        frame = bytearray(w * h * 3)
        while True:
            mtype = (await s.read(1))[0]
            if mtype != 0:
                continue
            await s.read(1)
            rects = struct.unpack(">H", await s.read(2))[0]
            for _ in range(rects):
                x, y, rw, rh, enc = struct.unpack(">HHHHi", await s.read(12))
                if enc != 0:
                    continue
                data = await s.read(rw * rh * 4)
                for row in range(rh):
                    for col in range(rw):
                        b, g, r = data[(row * rw + col) * 4:(row * rw + col) * 4 + 3]
                        i = ((y + row) * w + x + col) * 3
                        frame[i:i + 3] = bytes((r, g, b))
            break
        with open(out, "wb") as f:
            f.write(f"P6 {w} {h} 255\n".encode() + frame)
        print(f"grabbed {w}x{h} from '{name}'")


asyncio.run(main(sys.argv[1], sys.argv[2]))
