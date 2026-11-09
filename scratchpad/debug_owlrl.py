import sys, inspect
sys.path.insert(0, 'skills/qcc-super/scripts')
import kg_engine
print('module file:', kg_engine.__file__)
print('mtime check via inspect.getsourcelines:')
lines, _ = inspect.getsourcelines(kg_engine.owlrl_closure)
for i, ln in enumerate(lines, 1):
    print(f'{i}: {ln.rstrip()}')

# 直接独立调用测试
print('\n--- standalone call ---')
from pathlib import Path
from rdflib import Graph
g = Graph()
g.add((Path('skills/qcc-super/references/ontology/turtle/qcc_ontology.ttl') and Graph().__init__() and None, None, None)) if False else None

from rdflib import Namespace, URIRef
g.add((URIRef('http://x/1'), URIRef('http://x/type'), URIRef('http://x/Enterprise')))
print('data triples:', len(g))
try:
    Gc, new, base = kg_engine.owlrl_closure(g, ont_graph=kg_engine.load_ontology())
    print(f'data={base}, closed={len(Gc)}, new={new}')
except Exception as e:
    import traceback; traceback.print_exc()
