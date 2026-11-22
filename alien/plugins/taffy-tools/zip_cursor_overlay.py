""






















import ctypes
import ctypes.util
import fcntl
import functools
import json
import math
import os
import select
import socket
import sys
import time

REST_SEC = 20.0
EXIT_IDLE_SEC = 600.0
GLIDE_SEC = 0.32
PULSE_SEC = 0.45
FRAME_SEC = 1 / 40
SCALE = 1.5
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf")
DEFAULT_COLOR = "#F2711C"


ARROW = [(0, 0), (0, 22), (6, 16.5), (10.5, 25), (14, 23.5), (9.5, 15), (17, 15)]




class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
                ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong),
                ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
                ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
                ("event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("colormap", ctypes.c_ulong), ("cursor", ctypes.c_ulong)]


class XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("pad", ctypes.c_long * 24)]


CW_BACK_PIXEL, CW_BORDER_PIXEL, CW_OVERRIDE_REDIRECT, CW_EVENT_MASK = 1 << 1, 1 << 3, 1 << 9, 1 << 11
EXPOSURE_MASK, EXPOSE = 1 << 15, 12
SHAPE_BOUNDING, SHAPE_INPUT, SHAPE_SET = 0, 2, 0
Z_PIXMAP, INPUT_OUTPUT = 2, 1


def _lib(name: str, fallback: str):
    return ctypes.CDLL(ctypes.util.find_library(name) or fallback)


class X:
    def __init__(self):
        x = self.x = _lib("X11", "libX11.so.6")
        e = self.e = _lib("Xext", "libXext.so.6")
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        vp, ul, i, ui = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_uint
        for name, res, args in (
                ("XDefaultScreen", i, [vp]), ("XRootWindow", ul, [vp, i]), ("XDefaultVisual", vp, [vp, i]),
                ("XDefaultDepth", i, [vp, i]), ("XDisplayWidth", i, [vp, i]), ("XDisplayHeight", i, [vp, i]),
                ("XCreateWindow", ul, [vp, ul, i, i, ui, ui, ui, i, ui, vp, ul, ctypes.POINTER(XSetWindowAttributes)]),
                ("XStoreName", i, [vp, ul, ctypes.c_char_p]), ("XMapRaised", i, [vp, ul]),
                ("XUnmapWindow", i, [vp, ul]), ("XRaiseWindow", i, [vp, ul]),
                ("XMoveResizeWindow", i, [vp, ul, i, i, ui, ui]), ("XFlush", i, [vp]), ("XSync", i, [vp, i]),
                ("XCreateGC", vp, [vp, ul, ul, vp]), ("XCreateBitmapFromData", ul, [vp, ul, ctypes.c_char_p, ui, ui]),
                ("XFreePixmap", i, [vp, ul]),
                ("XCreateImage", vp, [vp, vp, ui, i, i, ctypes.c_char_p, ui, ui, i, i]),
                ("XPutImage", i, [vp, ul, vp, vp, i, i, i, i, ui, ui]), ("XFree", i, [vp]),
                ("XPending", i, [vp]), ("XNextEvent", i, [vp, ctypes.POINTER(XEvent)]),
                ("XConnectionNumber", i, [vp]), ("XCloseDisplay", i, [vp]),
                ("XQueryPointer", i, [vp, ul, ctypes.POINTER(ul), ctypes.POINTER(ul), ctypes.POINTER(i),
                                      ctypes.POINTER(i), ctypes.POINTER(i), ctypes.POINTER(i), ctypes.POINTER(ui)]),
                ("XGetGeometry", i, [vp, ul, ctypes.POINTER(ul), ctypes.POINTER(i), ctypes.POINTER(i),
                                     ctypes.POINTER(ui), ctypes.POINTER(ui), ctypes.POINTER(ui), ctypes.POINTER(ui)]),
                ("XTranslateCoordinates", i, [vp, ul, ul, i, i, ctypes.POINTER(i), ctypes.POINTER(i),
                                              ctypes.POINTER(ul)])):
            fn = getattr(x, name)
            fn.restype, fn.argtypes = res, args
        e.XShapeCombineMask.argtypes = [vp, ul, i, i, i, ul, i]
        e.XShapeCombineRectangles.argtypes = [vp, ul, i, i, i, vp, i, i, i]
        self.dpy = x.XOpenDisplay(None)
        if not self.dpy:
            raise SystemExit("no display")


        self.failed = False

        def _noted(_dpy, _event):
            self.failed = True
            return 0
        self._on_error = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(_noted)
        x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        x.XSetErrorHandler(self._on_error)
        scr = x.XDefaultScreen(self.dpy)
        self.root = x.XRootWindow(self.dpy, scr)
        self.visual = x.XDefaultVisual(self.dpy, scr)
        self.depth = x.XDefaultDepth(self.dpy, scr)
        self.size = (x.XDisplayWidth(self.dpy, scr), x.XDisplayHeight(self.dpy, scr))
        attrs = XSetWindowAttributes(override_redirect=1, background_pixel=0, border_pixel=0, event_mask=EXPOSURE_MASK)
        self.win = x.XCreateWindow(self.dpy, self.root, 0, 0, 8, 8, 0, self.depth, INPUT_OUTPUT, self.visual,
                                   CW_BACK_PIXEL | CW_BORDER_PIXEL | CW_OVERRIDE_REDIRECT | CW_EVENT_MASK,
                                   ctypes.byref(attrs))
        x.XStoreName(self.dpy, self.win, b"zip-cursor")

        e.XShapeCombineRectangles(self.dpy, self.win, SHAPE_INPUT, 0, 0, None, 0, SHAPE_SET, 0)
        self.gc = x.XCreateGC(self.dpy, self.win, 0, None)
        self.mapped = False
        self.picture = None
        self.at = (0, 0, 0, 0)

    def fd(self) -> int:
        return self.x.XConnectionNumber(self.dpy)

    def pointer(self) -> tuple[int, int]:
        ul, i, ui = ctypes.c_ulong, ctypes.c_int, ctypes.c_uint
        r, c, rx, ry, wx, wy, m = ul(), ul(), i(), i(), i(), i(), ui()
        self.x.XQueryPointer(self.dpy, self.root, ctypes.byref(r), ctypes.byref(c), ctypes.byref(rx), ctypes.byref(ry),
                             ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(m))
        return rx.value, ry.value

    def in_window(self, window: int, wx: float, wy: float, shot_w: float, shot_h: float):
        ""

        ul, i, ui = ctypes.c_ulong, ctypes.c_int, ctypes.c_uint
        root, gx, gy, w, h, bw, depth = ul(), i(), i(), ui(), ui(), ui(), ui()
        rx, ry, child = i(), i(), ul()
        self.failed = False
        ok = self.x.XGetGeometry(self.dpy, window, ctypes.byref(root), ctypes.byref(gx), ctypes.byref(gy),
                                 ctypes.byref(w), ctypes.byref(h), ctypes.byref(bw), ctypes.byref(depth))
        if ok:
            ok = self.x.XTranslateCoordinates(self.dpy, window, self.root, 0, 0, ctypes.byref(rx), ctypes.byref(ry),
                                              ctypes.byref(child))
        self.x.XSync(self.dpy, 0)
        if not ok or self.failed or not w.value or not h.value:
            return None
        sx = w.value / shot_w if shot_w and shot_w > 0 else 1.0
        sy = h.value / shot_h if shot_h and shot_h > 0 else sx
        return rx.value + wx * sx, ry.value + wy * sy

    def draw(self, left: int, top: int, picture) -> None:
        ""
        x, dpy, win = self.x, self.dpy, self.win
        bgrx, mask, w, h = picture[:4]
        if (left, top, w, h) != self.at:
            x.XMoveResizeWindow(dpy, win, left, top, w, h)
            self.at = (left, top, w, h)
        if picture is not self.picture:
            bitmap = x.XCreateBitmapFromData(dpy, win, mask, w, h)
            self.e.XShapeCombineMask(dpy, win, SHAPE_BOUNDING, 0, 0, bitmap, SHAPE_SET)
            x.XFreePixmap(dpy, bitmap)
            self.picture = picture
        if not self.mapped:
            x.XMapRaised(dpy, win)
            self.mapped = True
        else:
            x.XRaiseWindow(dpy, win)
        self.paint()
        x.XFlush(dpy)

    def paint(self) -> None:
        if not (self.mapped and self.picture):
            return
        bgrx, _mask, w, h = self.picture[:4]
        img = self.x.XCreateImage(self.dpy, self.visual, self.depth, Z_PIXMAP, 0, bgrx, w, h, 32, 0)
        if img:
            self.x.XPutImage(self.dpy, self.win, self.gc, img, 0, 0, 0, 0, w, h)
            self.x.XFree(img)

    def hide(self) -> None:
        if self.mapped:
            self.x.XUnmapWindow(self.dpy, self.win)
            self.mapped = False
        self.x.XSync(self.dpy, 0)

    def events(self) -> None:
        ev = XEvent()
        while self.x.XPending(self.dpy):
            self.x.XNextEvent(self.dpy, ctypes.byref(ev))
            if ev.type == EXPOSE:
                self.paint()
        self.x.XFlush(self.dpy)




_REVERSE = bytes(int(f"{b:08b}"[::-1], 2) for b in range(256))


def _rgb(color: str) -> tuple[int, int, int]:
    c = str(color or "").strip().lstrip("#")
    if len(c) == 6:
        try:
            return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        except ValueError:
            pass
    return _rgb(DEFAULT_COLOR)


@functools.lru_cache(maxsize=4)
def _font(size: int):
    from PIL import ImageFont
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


_pictures: dict = {}


def _tag_width(name: str) -> int:
    label = (name or "")[:24]
    return int(_font(round(11 * SCALE)).getlength(label) + 14 * SCALE) if label else 0


def picture(color: str, name: str, pulse: float, flip_x: bool = False, flip_y: bool = False):
    ""


    ring = round(pulse * 8) / 8
    key = (color, name, ring, flip_x, flip_y)
    if key in _pictures:
        return _pictures[key]
    from PIL import Image, ImageDraw
    s, rgb = SCALE, _rgb(color)
    font = _font(round(11 * s))
    label = (name or "")[:24]
    tag_w = _tag_width(label)
    tag_h = 18 * s

    tag_left = -2 * s - tag_w if flip_x else 16 * s
    tag_top = -4 * s - tag_h if flip_y else 24 * s
    reach = 22 * s
    left = min(-reach, tag_left if label else 0) - 2
    top = min(-reach, tag_top if label else 0) - 2
    right = max(18 * s, reach + 6 * s, tag_left + tag_w if label else 0) + 2
    bottom = max(26 * s, reach + 6 * s, tag_top + tag_h if label else 0) + 2
    tx, ty = int(-left), int(-top)
    w, h = int(right - left) + 1, int(bottom - top) + 1
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if ring > 0:
        r = (8 + 14 * (1 - ring)) * s
        cx, cy = tx + 4 * s, ty + 6 * s
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=rgb + (255,), width=max(2, round(3 * s * ring)))
    pts = [(tx + px * s, ty + py * s) for px, py in ARROW]
    d.polygon(pts, fill=rgb + (255,), outline=(255, 255, 255, 255), width=max(1, round(1.5 * s)))
    if label:
        box = (tx + tag_left, ty + tag_top, tx + tag_left + tag_w, ty + tag_top + tag_h)
        d.rounded_rectangle(box, radius=9 * s, fill=rgb + (255,), outline=(255, 255, 255, 255), width=1)
        d.text((box[0] + 7 * s, box[1] + tag_h / 2), label, font=font, fill=(255, 255, 255, 255), anchor="lm")
    alpha = img.getchannel("A")

    white = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    flat = Image.alpha_composite(white, img).convert("RGB")
    bgrx = flat.tobytes("raw", "BGRX")
    mask = alpha.point(lambda a: 255 if a >= 110 else 0).convert("1").tobytes().translate(_REVERSE)
    out = (bgrx, mask, w, h, tx, ty)
    if len(_pictures) > 64:
        _pictures.clear()
    _pictures[key] = out
    return out




def _ease(t: float) -> float:
    return 1 - (1 - t) ** 3


class Cursor:
    def __init__(self, x: X):
        self.x = x
        self.pos = None
        self.last = None
        self.start = self.goal = None
        self.glide_at = self.press_at = 0.0
        self.color, self.name = DEFAULT_COLOR, ""
        self.moved_at = 0.0

    def show(self, cmd: dict, now: float) -> None:
        if cmd.get("op") == "pointer":
            gx, gy = self.x.pointer()
        elif cmd.get("window"):
            try:
                spot = self.x.in_window(int(cmd["window"]), float(cmd.get("x", 0)), float(cmd.get("y", 0)),
                                        float(cmd.get("shot_w") or 0), float(cmd.get("shot_h") or 0))
            except (TypeError, ValueError):
                spot = None
            if spot is None:
                return
            gx, gy = int(spot[0]), int(spot[1])
        elif cmd.get("x") is None or cmd.get("y") is None:
            if self.last is None:
                return
            gx, gy = self.last
        else:
            gx, gy = int(cmd["x"]), int(cmd["y"])
        sw, sh = self.x.size
        gx, gy = max(0, min(sw - 1, gx)), max(0, min(sh - 1, gy))
        self.color = str(cmd.get("color") or self.color)
        self.name = str(cmd.get("name") if cmd.get("name") is not None else self.name)
        here = self.pos if self.pos is not None else (self.last or (gx, gy))
        self.start, self.goal, self.glide_at, self.last = here, (gx, gy), now, (gx, gy)
        if self.pos is None:
            self.pos = here
        self.press_at = now + (GLIDE_SEC if here != (gx, gy) else 0) if cmd.get("press") else 0.0
        self.moved_at = now

    def hide(self) -> None:
        self.pos = self.start = self.goal = None
        self.press_at = 0.0
        self.x.hide()

    def busy(self, now: float) -> bool:
        return self.pos is not None and (now - self.glide_at < GLIDE_SEC or 0 < self.press_at and now - self.press_at < PULSE_SEC)

    def frame(self, now: float) -> None:
        if self.pos is None:
            return
        if now - self.moved_at > REST_SEC:
            self.hide()
            return
        t = min(1.0, (now - self.glide_at) / GLIDE_SEC) if GLIDE_SEC else 1.0
        k = _ease(t)
        x = round(self.start[0] + (self.goal[0] - self.start[0]) * k)
        y = round(self.start[1] + (self.goal[1] - self.start[1]) * k)
        self.pos = (x, y)
        pulse = 0.0
        if self.press_at and now >= self.press_at:
            pulse = max(0.0, 1 - (now - self.press_at) / PULSE_SEC)
        sw, sh = self.x.size
        tag = _tag_width(self.name)
        pic = picture(self.color, self.name, pulse, flip_x=x + (16 * SCALE + tag + 4) > sw,
                      flip_y=y + 44 * SCALE + 4 > sh)
        self.x.draw(x - pic[4], y - pic[5], pic)


def _single(sock_path: str):
    ""
    fd = os.open(sock_path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def main(sock_path: str) -> int:
    lock = _single(sock_path)
    if lock is None:
        return 0
    x = X()
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    old = os.umask(0o177)
    try:
        sock.bind(sock_path)
    finally:
        os.umask(old)
    cursor = Cursor(x)
    heard = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            wait = FRAME_SEC if cursor.busy(now) else 1.0
            ready, _, _ = select.select([sock, x.fd()], [], [], wait)
            now = time.monotonic()
            if sock in ready:
                while True:
                    try:
                        data, sender = sock.recvfrom(4096, socket.MSG_DONTWAIT)
                    except BlockingIOError:
                        break
                    heard = now
                    try:
                        cmd = json.loads(data)
                    except ValueError:
                        continue
                    if not isinstance(cmd, dict):
                        continue
                    op = cmd.get("op")
                    if op in ("show", "pointer"):
                        cursor.show(cmd, now)
                    elif op == "hide":
                        cursor.hide()
                        if cmd.get("ack") and sender:
                            try:
                                sock.sendto(b"ok", sender)
                            except OSError:
                                pass
                    elif op == "quit":
                        cursor.hide()
                        return 0
            x.events()
            cursor.frame(now)
            if cursor.pos is None and now - heard > EXIT_IDLE_SEC:
                return 0
    finally:
        try:
            os.unlink(sock_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
