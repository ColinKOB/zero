"""The client half of Zero's plan broker. A home on a shared plan runs this as `claude` and `codex`
(claude-client and codex-client, linked from ~/.local/bin). It sends the argument list, the small files the
argument list names, and stdin to the broker over /srv/zero/broker/broker.sock, then plays back the broker's
stdout, stderr and exit status. It holds no token and makes no decision: the broker checks everything.

Standard library only, and Python 3.8 or newer, so the system python3 runs it.
"""

import json
import os
import signal
import socket
import struct
import sys
import threading

SOCKET = os.environ.get("ZERO_BROKER_SOCKET", "/srv/zero/broker/broker.sock")
PASS_ENV = ("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "DISABLE_TELEMETRY", "DISABLE_ERROR_REPORTING",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "CLAUDE_CODE_ENABLE_TELEMETRY")
FILE_FLAGS = ("--system-prompt-file", "--settings")
MAX_FILES = 16 * 1024 * 1024
UNREACHABLE = "The shared plan is not reachable right now. The owner can check it with: zero-broker status\n"


def send(sock, kind, payload=b"", lock=None):
    data = kind + struct.pack(">I", len(payload)) + payload
    if lock:
        with lock:
            sock.sendall(data)
    else:
        sock.sendall(data)


def recv(f):
    head = f.read(5)
    if len(head) < 5:
        return None
    size = struct.unpack(">I", head[1:])[0]
    payload = b""
    while len(payload) < size:
        chunk = f.read(size - len(payload))
        if not chunk:
            return None
        payload += chunk
    return head[:1], payload


def collect_files(argv):
    """The contents of the files the Claude handshake names: the system prompt, the settings and the tool list
    inside the MCP config. The broker runs as another user and cannot read this user's temporary folder."""
    paths = []
    for i, a in enumerate(argv[:-1]):
        if a in FILE_FLAGS:
            paths.append(argv[i + 1])
        elif a == "--mcp-config":
            try:
                servers = json.loads(argv[i + 1]).get("mcpServers", {})
                for server in servers.values():
                    paths += [p for p in server.get("args", []) if isinstance(p, str) and p.endswith(".json")]
            except (ValueError, AttributeError):
                pass
    files, total = {}, 0
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read(MAX_FILES + 1)
        except OSError:
            continue
        total += len(text)
        if total > MAX_FILES:
            break
        files[p] = text
    return files


def main(tool):
    argv = sys.argv[1:]
    header = {"v": 1, "tool": tool, "argv": argv,
              "files": collect_files(argv) if tool == "claude" else {},
              "env": {k: os.environ[k] for k in PASS_ENV if k in os.environ}}
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(SOCKET)
    except OSError:
        sys.stderr.write(UNREACHABLE)
        return 1
    lock = threading.Lock()
    send(sock, b"H", json.dumps(header).encode(), lock)

    def stop(signum, _frame):
        try:
            send(sock, b"S", b"", lock)
        except OSError:
            pass
        os._exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)

    def pump_stdin():
        try:
            while True:
                chunk = os.read(0, 65536)
                if not chunk:
                    break
                send(sock, b"I", chunk, lock)
        except OSError:
            pass
        try:
            send(sock, b"E", b"", lock)
        except OSError:
            pass

    threading.Thread(target=pump_stdin, daemon=True).start()
    f = sock.makefile("rb")
    out, err = sys.stdout.buffer, sys.stderr.buffer
    while True:
        frame = recv(f)
        if frame is None:
            sys.stderr.write(UNREACHABLE)
            return 1
        kind, payload = frame
        if kind == b"O":
            out.write(payload)
            out.flush()
        elif kind == b"R":
            err.write(payload)
            err.flush()
        elif kind == b"F":
            err.write(payload + b"\n")
            err.flush()
            return 126
        elif kind == b"X":
            try:
                return int(json.loads(payload).get("code", 1))
            except (ValueError, AttributeError):
                return 1
