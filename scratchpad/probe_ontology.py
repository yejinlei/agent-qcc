# -*- coding: utf-8 -*-
"""探测：现有图谱在 owl-r2-ml 闭世界推理下能多出多少三元组，以及本体覆盖面。"""
import sys, json
sys.path.insert(0, "skills/qcc-super/scripts")

from rdflib import Graph

from kg_engine import fetch_enterprise_graph, to_rdflib_graph

g = to_rdflib_graph(fetch_enterprise_graph("91330110MAEW31RB2Q", depth=1))
before = len(g)

# 闭世界推理：owlrl (owl-r2-ml 的现代实现)
import owlrl
g2 = Graph()
g2.parse(data=g.serialize(format="turtle"), format="turtle")
owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g2)  # in-place
after = len(g2)

from rdflib.namespace import RDF
preds = sorted({str(p) for s, p, o in g2.triples((None, None, None)) if p})
types = sorted({str(o) for s, p, o in g2.triples((None, RDF.type, None)) if str(o).startswith("http://qcc.internal/ontology")})

print(json.dumps({
    "triples_before": before,
    "triples_after_deduce": after,
    "new_inferred": after - before,
    "distinct_predicates": len(preds),
    "distinct_types": len(types),
    "types": types,
}, ensure_ascii=False, indent=2))
