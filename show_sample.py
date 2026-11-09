import json, random
d = json.load(open(r"f:\src\agent-qcc\tool_fields.json", encoding="utf-8"))
recs = d["records"]
print(f"总记录数: {len(recs)}")
print("=" * 100)

# 展示每个 server 的第一条工具
by_server = {}
for r in recs:
    by_server.setdefault(r["server"], []).append(r)

for server, tools in by_server.items():
    print(f"\n--- {server} ({len(tools)} 工具) ---")
    for r in tools[:3]:
        fields = r["return_fields"][:10]
        print(f"  · {r['tool']} [{r['title']}]")
        print(f"    字段({r['return_field_count']}): {' | '.join(fields)}")

# 打印字段为空的 24 个
print("\n" + "=" * 100)
print("字段为空（描述未列举具体字段）的工具：")
for r in recs:
    if r["return_field_count"] == 0:
        print(f"  · {r['server']}/{r['tool']} [{r['title']}]")
        # 打印 clean_desc 前 120 字
        print(f"    → {r['description_clean'][:150]}")
