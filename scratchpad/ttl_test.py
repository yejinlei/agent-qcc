from io import BytesIO
from pathlib import Path
from rdflib import Graph

# 最小测试：单条 property 带中文 label
ttl = """@prefix qcc: <http://qcc.internal/ontology#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
qcc:hasIPAsset a owl:ObjectProperty ;
    rdfs:label "知识产权资产"@zh .
""".encode("utf-8")
g = Graph()
try:
    g.parse(source=BytesIO(ttl), format="turtle", encoding="utf-8")
    print("min test OK, triples:", len(g))
except Exception as e:
    print("FAIL:", str(e)[:200])

# 原文件测试
p = Path("skills/qcc-super/references/ontology/turtle/qcc_ontology.ttl")
data = p.read_bytes()
print("\n--- original file ({} bytes) ---".format(len(data)))

# 找到第 225 行前后
lines = data.split(b"\n")
print("total lines:", len(lines))
print("line 200-230:")
for i in range(200, min(231, len(lines))):
    print(f"  {i+1}: {lines[i][:120]!r}")
