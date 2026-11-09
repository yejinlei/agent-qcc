"""批量获取所有企查查 MCP server 的工具 schema"""
import json
import urllib.request
import ssl

TOKEN = "MXx7ppTqZHy2a8nR7eMOnyhcKLDbOKVKiWnB590MBZ4oC9XZ"
BASE = "https://agent.qcc.com/mcp"

SERVERS = {
    "qcc-company": "company",
    "qcc-risk": "risk",
    "qcc-history": "history",
    "qcc-executive": "executive",
    "qcc-ipr": "ipr",
    "qcc-operation": "operation",
    "qcc-legal-regulation": "regulation",
    "qcc-legal-case": "case",
    "qcc-tender": "tender",
    "qcc-document": "document",
}

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

results = {}
errors = {}

for sid, path in SERVERS.items():
    url = f"{BASE}/{path}/stream"
    body = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
        for line in raw.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                tools = payload.get("result", {}).get("tools", [])
                results[sid] = tools
                print(f"{sid}: {len(tools)} tools")
                break
        else:
            print(f"{sid}: NO data line found, raw[:200]={raw[:200]}")
            errors[sid] = "no SSE data line"
    except Exception as e:
        print(f"{sid}: ERROR {e}")
        errors[sid] = str(e)

out = {"tools": {}, "errors": errors}
for sid, tools in results.items():
    out["tools"][sid] = []
    for t in tools:
        out["tools"][sid].append({
            "name": t.get("name"),
            "title": t.get("title"),
            "description": t.get("description", ""),
            "inputSchema": t.get("inputSchema", {}),
        })

with open(r"f:\src\agent-qcc\tools_raw.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"\nTotal servers fetched: {len(results)}/{len(SERVERS)}")
print(f"Errors: {errors if errors else 'none'}")
total = sum(len(v) for v in results.values())
print(f"Total tools: {total}")
