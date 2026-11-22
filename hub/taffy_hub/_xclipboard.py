""




import ctypes as c
import ctypes.util
import json
import select
import time

MAX_BYTES = 128 * 1024


def copy_selection():
    x = c.CDLL(ctypes.util.find_library("X11"))
    fixes = c.CDLL(ctypes.util.find_library("Xfixes"))
    test = c.CDLL(ctypes.util.find_library("Xtst"))
    pointer, integer, ulong = c.c_void_p, c.c_int, c.c_ulong

    def bind(lib, name, result, *args):
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, args
        return fn

    bind(x, "XOpenDisplay", pointer, c.c_char_p)
    bind(x, "XDefaultRootWindow", ulong, pointer)
    bind(x, "XCreateSimpleWindow", ulong, pointer, ulong, integer, integer, c.c_uint, c.c_uint, c.c_uint, ulong, ulong)
    bind(x, "XInternAtom", ulong, pointer, c.c_char_p, integer)
    bind(x, "XConvertSelection", integer, pointer, ulong, ulong, ulong, ulong, ulong)
    bind(x, "XFlush", integer, pointer)
    bind(x, "XSync", integer, pointer, integer)
    bind(x, "XPending", integer, pointer)
    bind(x, "XConnectionNumber", integer, pointer)
    bind(x, "XNextEvent", integer, pointer, pointer)
    bind(x, "XKeysymToKeycode", c.c_ubyte, pointer, ulong)
    bind(x, "XGetWindowProperty", integer, pointer, ulong, ulong, c.c_long, c.c_long, integer, ulong,
         pointer, pointer, pointer, pointer, pointer)
    bind(x, "XFree", integer, pointer)
    bind(x, "XDestroyWindow", integer, pointer, ulong)
    bind(x, "XCloseDisplay", integer, pointer)
    bind(fixes, "XFixesQueryExtension", integer, pointer, pointer, pointer)
    bind(fixes, "XFixesSelectSelectionInput", None, pointer, ulong, ulong, ulong)
    bind(test, "XTestFakeKeyEvent", integer, pointer, c.c_uint, integer, ulong)

    display = x.XOpenDisplay(None)
    if not display:
        raise ValueError("The computer is not connected yet.")
    window = 0
    try:
        window = x.XCreateSimpleWindow(display, x.XDefaultRootWindow(display), 0, 0, 1, 1, 0, 0, 0)
        clipboard = x.XInternAtom(display, b"CLIPBOARD", 0)
        prop = x.XInternAtom(display, b"ZERO_COPY", 0)
        event_base, error_base = integer(), integer()
        if not fixes.XFixesQueryExtension(display, c.byref(event_base), c.byref(error_base)):
            raise ValueError("Copy is not available on this computer.")
        fixes.XFixesSelectSelectionInput(display, window, clipboard, 1)
        x.XSync(display, 0)
        event = (c.c_long * 24)()
        while x.XPending(display):
            x.XNextEvent(display, c.byref(event))

        def wait_for(kind, seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if not x.XPending(display):
                    select.select([x.XConnectionNumber(display)], [], [], max(0, deadline - time.monotonic()))
                    continue
                x.XNextEvent(display, c.byref(event))
                if c.cast(c.byref(event), c.POINTER(integer)).contents.value == kind:
                    return True
            return False


        for sym in (0xffe1, 0xffe2, 0xffe3, 0xffe4, 0xffe9, 0xffea, 0xffeb, 0xffec):
            test.XTestFakeKeyEvent(display, x.XKeysymToKeycode(display, sym), 0, 0)
        control, key_c = (x.XKeysymToKeycode(display, sym) for sym in (0xffe3, ord("c")))
        for key, down in ((control, 1), (key_c, 1), (key_c, 0), (control, 0)):
            test.XTestFakeKeyEvent(display, key, down, 0)
        x.XFlush(display)
        if not wait_for(event_base.value, 1.5):
            raise ValueError("Select some text on the computer, then Copy.")

        for target, encoding in ((b"UTF8_STRING", "utf-8"), (b"STRING", "latin-1")):
            atom = x.XInternAtom(display, target, 0)
            x.XConvertSelection(display, clipboard, atom, prop, window, 0)
            x.XFlush(display)
            if not wait_for(31, 1):
                continue
            actual, size, count, remaining = ulong(), integer(), ulong(), ulong()
            data = c.POINTER(c.c_ubyte)()
            x.XGetWindowProperty(display, window, prop, 0, MAX_BYTES // 4 + 1, 1, 0,
                                 c.byref(actual), c.byref(size), c.byref(count), c.byref(remaining), c.byref(data))
            try:
                if actual.value == x.XInternAtom(display, b"INCR", 0) or remaining.value or count.value > MAX_BYTES:
                    raise ValueError("Copy less text and try again.")
                if actual.value == atom and size.value == 8:
                    return c.string_at(data, count.value).decode(encoding)
            finally:
                if data:
                    x.XFree(data)
        raise ValueError("Select some text on the computer, then Copy.")
    finally:
        if window:
            x.XDestroyWindow(display, window)
        x.XCloseDisplay(display)


if __name__ == "__main__":
    try:
        print(json.dumps({"text": copy_selection()}))
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}))
