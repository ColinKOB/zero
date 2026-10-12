"""The broker's own tool inventory for Claude runs it makes for a home. It lists the tools Hermes offered
(the manifest the home's client sent) so Claude can name them, and refuses every call: Hermes, in the home,
runs the tools itself. Standard library only; no file or network access beyond reading the manifest."""
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
            result = {"isError": True, "content": [{"type": "text", "text": "Denied: native tools are inert; only Hermes executes tools."}]}
        if "id" in row:
            print(json.dumps({"jsonrpc": "2.0", "id": row["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
