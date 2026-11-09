import sys, json
sys.path.insert(0, 'skills/qcc-super/scripts')
import kg_engine

g = kg_engine.fetch_enterprise_graph('91330110MAEW31RB2Q')
r = kg_engine.infer_risk(g)
print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
