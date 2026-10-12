"""The tool list for an on-device voice call's Claude run (ondevice.py). It names the two tools the call offers,
ask_bot and update_job, so Claude can call them, and refuses every call: the hub runs them itself when the
run ends. A copy of the broker's own inventory (alien/broker/inert_mcp.py), so a home on its own plan runs the
same shape a home on a shared plan does through the broker. Standard library only."""
import json
import sys
from pathlib import Path


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for line in sys.stdin:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        method = row.get("method")
        result = {}
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "hermes-inert-inventory", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": manifest}
        elif method == "tools/call":
            result = {"isError": True, "content": [{"type": "text", "text": "Denied: the hub runs these tools."}]}
        if "id" in row:
            print(json.dumps({"jsonrpc": "2.0", "id": row["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
