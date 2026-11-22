""



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
