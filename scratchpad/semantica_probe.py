# -*- coding: utf-8 -*-
"""semantica 0.6.8 实测：真实 qcc 数据 → KnowledgeGraph → GraphAnalyzer。"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "qcc-super" / "scripts"))

from kg_engine import fetch_enterprise_graph
from semantica.kg import KnowledgeGraph, GraphBuilder, GraphAnalyzer

USCC = "91330110MAEW31RB2Q"

# 1) 拉真实 qcc 数据
g = fetch_enterprise_graph(USCC)
firm = g["firm"]
print(f"目标企业: {firm['name']} ({USCC})")
print(f"  股东: {len(g['shareholders'])} 名")
print(f"  高管: {len(g['executives'])} 名")
print(f"  对外投资: {len(g['external_investments'])} 家")

# 2) 映射成 semantica 数据模型
entities = [
    {
        "id": USCC,
        "type": "Enterprise",
        "name": firm["name"],
        "legal_rep": firm.get("legal_rep"),
        "reg_capital": firm.get("reg_capital"),
        "status": firm.get("status"),
        "province": firm.get("province"),
    }
]
relationships = []

for s in g["shareholders"]:
    holder_id = s.get("holder_uscc") or s.get("holder_name") or "?"
    entities.append({
        "id": holder_id,
        "type": "Enterprise" if s.get("holder_uscc") else "Person",
        "name": s.get("holder_name", ""),
    })
    relationships.append({
        "source": holder_id,
        "target": USCC,
        "type": "hasShareholder",
        "weight": float(s.get("ratio", 0)) / 100.0,
        "stake_ratio": float(s.get("ratio", 0)) / 100.0,
    })

for e in g["executives"]:
    ent_id = e["name"]
    entities.append({"id": ent_id, "type": "Person", "name": ent_id})
    relationships.append({
        "source": USCC,
        "target": ent_id,
        "type": "hasExecutive",
    })
    if e.get("position") == "董事长" or "董事长" in str(e.get("position", "")):
        relationships.append({"source": USCC, "target": ent_id, "type": "hasLegalRep"})

# 3) 构建 KnowledgeGraph
kb_data = {"entities": entities, "relationships": relationships}
kb = GraphBuilder(merge_entities=False, resolve_conflicts=False)
built = kb.build_single_source(kb_data, pipeline_id="qcc-probe")

print("\n=== GraphBuilder.build_single_source() 输出 ===")
print(f"  entities: {len(built.get('entities', []))}")
print(f"  relationships: {len(built.get('relationships', []))}")
print(f"  metadata keys: {list(built.get('metadata', {}).keys()) if isinstance(built.get('metadata'), dict) else 'N/A'}")

# 4) 转成 KnowledgeGraph 对象做分析
kg = KnowledgeGraph(
    entities=built["entities"],
    relationships=built["relationships"],
    metadata=built.get("metadata", {}),
)

# 5) 图分析
ga = GraphAnalyzer()
result = ga.analyze(kg)

print("\n=== GraphAnalyzer.analyze() 输出 ===")
print(f"  centrality: {len(result.get('centrality', {}))} 节点")
centrality = result.get("centrality", {})
if isinstance(centrality, dict):
    for name, metrics in list(centrality.items())[:5]:
        print(f"    {name}: {metrics}")

print(f"  communities: {result.get('communities')}")
print(f"  connectivity: {result.get('connectivity')}")
print(f"  metrics: {result.get('metrics')}")

# 6) 保存为 JSON 供后续可视化
out = {
    "source_uscc": USCC,
    "source_name": firm["name"],
    "entities": entities,
    "relationships": relationships,
    "graph_stats": {
        "node_count": len(entities),
        "edge_count": len(relationships),
    },
    "analysis": {
        "centrality": centrality,
        "communities": result.get("communities"),
        "connectivity": result.get("connectivity"),
        "metrics": result.get("metrics"),
    },
}
out_path = Path("scratchpad/semantica-real-data.json")
out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n=== 输出保存 ===")
print(f"  {out_path.resolve()}")
