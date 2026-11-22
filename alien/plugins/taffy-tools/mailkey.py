""






import hashlib
import json


def mail_call_key(m: dict) -> dict:
    ""

    out = {k: m[k] for k in ("action", "account", "folder", "subject", "in_reply_to", "attach")}
    out["body"] = m["body"].strip()
    for k in ("to", "cc"):
        out[k] = sorted({addr.lower() for _name, addr in m[k]})
    return out


def mail_fingerprint(m: dict, content_hash: str = "") -> str:
    ""

    blob = json.dumps({"tool": "email", "args": mail_call_key(m), "file": content_hash}, sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]
