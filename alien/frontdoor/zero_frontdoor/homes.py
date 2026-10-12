"""Making a home for a new account and getting a device into a home, with the existing home tooling only.

- A new home: `sudo -n /srv/zero/bin/zero-account-home create <id>` (the root steps, the same as
  `sudo zero-home-sudo-prep home <id> --all --own-plan`), then `/srv/zero/bin/zero-home-create <id> --person ...`
  as the owner, exactly how zero-riley was made, except that the home is on the person's own plan.
- A device token: a one-time pairing code from the home itself (`zero-home-create <id> --pair`, or `taffy-hub pair`
  in the owner's home), redeemed at once on the home's own hub over loopback. The app gets the same address and
  token that pairing gives it.

Tests replace LiveHomes with a fake, so they never run these commands.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

BIN = Path(os.environ.get("ZERO_HOME_BIN", "/srv/zero/bin"))
OWNER_HUB_CLI = Path(os.environ.get("ZERO_OWNER_HUB_CLI", Path.home() / "taffy" / "bin" / "taffy-hub"))
# One home build takes a few minutes (a venv, rootless Docker and the Zip image); a stuck one is stopped.
CREATE_TIMEOUT = int(os.environ.get("ZERO_FRONTDOOR_CREATE_TIMEOUT", "1500"))


class HomeError(Exception):
    """A step failed. The message is for the log; the person sees a plain sentence from app.py."""


def load_lib():
    spec = importlib.util.spec_from_file_location("zero_home_lib", BIN / "zero-home-lib.py")
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    return lib


def slug(username: str, length: int = 12) -> str:
    s = re.sub(r"[^a-z0-9]", "", username.lower())[:length]
    return s if len(s) >= 2 else (s + "zz")[:2] if s else "user"


class LiveHomes:
    def __init__(self, prefix: str = "u"):
        self.prefix = prefix
        self._lib = None

    @property
    def lib(self):
        if self._lib is None:
            self._lib = load_lib()
        return self._lib

    def registry(self) -> dict:
        return self.lib.read_registry()

    def new_home_id(self, username: str, taken: set[str]) -> str:
        homes = set(self.registry()["homes"]) | taken
        base = f"{self.prefix}-{slug(username)}"
        if base not in homes:
            return base
        for n in range(2, 1000):
            cand = f"{self.prefix}-{slug(username, 12 - len(str(n)))}{n}"
            if cand not in homes:
                return cand
        raise HomeError("no free home id")

    def has_room(self) -> bool:
        """Whether one more home fits in the ports the homes use."""
        try:
            self.lib.allocate_ports("zz", self.registry(), self.lib.listening_ports())
            return True
        except RuntimeError:
            return False

    def create(self, home_id: str, person: str, timezone: str, step=lambda name: None) -> None:
        step("user")
        self._run(["sudo", "-n", str(BIN / "zero-account-home"), "create", home_id], 600)
        step("home")
        cmd = [str(BIN / "zero-home-create"), home_id, "--person", person, "--no-pair"]
        if timezone:
            cmd += ["--timezone", timezone]
        self._run(cmd, CREATE_TIMEOUT)
        step("done")

    def remove(self, home_id: str) -> None:
        self._run(["sudo", "-n", str(BIN / "zero-account-home"), "remove", home_id], 600)

    def device_token(self, home_id: str, device_name: str) -> tuple[str, str]:
        """(the home's address, a new device token) for a device signing in to `home_id`."""
        entry = self.registry()["homes"].get(home_id)
        if not entry:
            raise HomeError(f"{home_id} is not in the registry")
        if entry.get("owner"):
            out = self._run([str(OWNER_HUB_CLI), "pair", "0.1"], 60)
            code = _field(out, "Pairing code")
            server = _field(out, "Server")
        else:
            out = self._run([str(BIN / "zero-home-create"), home_id, "--pair"], 120)
            code = _field(out, "Code")
            server = _field(out, "Address")
        if not code or not server:
            raise HomeError(f"no pairing code from {home_id}")
        token = redeem(entry["ports"]["hub"], code, device_name)
        return server, token

    @staticmethod
    def _run(cmd: list[str], timeout: int) -> str:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise HomeError(f"{Path(cmd[0]).name if cmd[0] != 'sudo' else cmd[2]} took longer than {timeout}s") from e
        if p.returncode != 0:
            # The last lines of stderr only; the tooling never prints secrets there (backup passwords go to stdout
            # on request only).
            raise HomeError(f"{' '.join(Path(c).name for c in cmd[:3])} exited {p.returncode}: "
                            f"{(p.stderr or '').strip()[-400:]}")
        return p.stdout


def _field(out: str, name: str) -> str:
    """'Code:    ABCD-2345' -> 'ABCD2345'; 'Pairing code: ABCD-2345 (valid ...)' -> 'ABCD2345'; 'Address: https://...'."""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(name + ":"):
            value = line.split(":", 1)[1].strip()
            if name in ("Code", "Pairing code"):
                value = value.split("(")[0].strip().replace("-", "")
            return value
    return ""


def redeem(hub_port: int, code: str, device_name: str) -> str:
    body = json.dumps({"code": code, "device_name": device_name[:80]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{int(hub_port)}/api/pair", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            token = json.loads(r.read()).get("token")
    except Exception as e:  # noqa: BLE001 - any failure is the same "could not sign in" for the person
        raise HomeError(f"the hub on port {hub_port} did not pair: {type(e).__name__}") from e
    if not token:
        raise HomeError(f"the hub on port {hub_port} gave no token")
    return token
