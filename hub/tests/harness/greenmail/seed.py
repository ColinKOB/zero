"""Seeds the test home's mailbox with the messages in messages.json.

The test home's GreenMail (WP3's zero-home-test) has SMTP and IMAP on loopback ports in the home's range.
tools/zero-e2e.sh --live hands these messages to `zero-home-test up --seed FILE`, which delivers them. This script
delivers the same data over plain SMTP by hand (GreenMail accepts any recipient), for reseeding a running test home
from alien or through an ssh tunnel, and writes .eml files for a dry run. Standard library only.

  python3 seed.py --to sam@zero.test --smtp 127.0.0.1:18550
  python3 seed.py --to sam@zero.test --eml-dir /tmp/seed      # write .eml files instead (dry run)
  python3 seed.py --subjects                                           # print the subjects the Inbox Zip must list

Prints one line per message; never prints a password (GreenMail's SMTP needs none by default; --user/--password
are passed through for a server that asks).
"""

from __future__ import annotations

import argparse
import json
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent / "messages.json"


def load() -> list[dict]:
    return json.loads(DATA.read_text())["messages"]


def subjects() -> list[str]:
    return [m["subject"] for m in load()]


def build(m: dict, to: str, now: datetime | None = None) -> EmailMessage:
    now = now or datetime.now(timezone.utc)
    msg = EmailMessage()
    msg["From"] = m["from"]
    msg["To"] = to
    msg["Subject"] = m["subject"]
    msg["Date"] = format_datetime(now - timedelta(days=m.get("days_ago", 0)))
    msg["Message-ID"] = make_msgid(domain="zero-test.local")
    msg["X-Zero-Seed"] = m["key"]
    msg.set_content(m["body"])
    return msg


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--to", default="sam@zero.test")
    ap.add_argument("--smtp", default="127.0.0.1:18550", help="host:port of GreenMail's SMTP")
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--eml-dir", help="write .eml files here instead of sending")
    ap.add_argument("--subjects", action="store_true")
    ap.add_argument("--retries", type=int, default=10, help="tries while GreenMail is still starting")
    a = ap.parse_args(argv)
    if a.subjects:
        print("\n".join(subjects()))
        return 0
    messages = [build(m, a.to) for m in load()]
    if a.eml_dir:
        out = Path(a.eml_dir)
        out.mkdir(parents=True, exist_ok=True)
        for i, msg in enumerate(messages, 1):
            (out / f"{i:02d}-{msg['X-Zero-Seed']}.eml").write_bytes(bytes(msg))
            print(f"wrote {msg['X-Zero-Seed']}: {msg['Subject']}")
        return 0
    host, _, port = a.smtp.rpartition(":")
    for attempt in range(a.retries):
        try:
            with smtplib.SMTP(host or "127.0.0.1", int(port), timeout=15) as s:
                if a.user and a.password:
                    s.login(a.user, a.password)
                for msg in messages:
                    s.send_message(msg)
                    print(f"sent {msg['X-Zero-Seed']}: {msg['Subject']}")
            return 0
        except (OSError, smtplib.SMTPException) as e:
            if attempt == a.retries - 1:
                print(f"could not deliver to {a.smtp}: {type(e).__name__}: {e}", file=sys.stderr)
                return 1
            time.sleep(3)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
