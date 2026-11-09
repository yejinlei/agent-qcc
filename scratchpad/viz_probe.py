import sys, json, os
sys.path.insert(0, 'skills/qcc-super/scripts')
import kg_engine

TARGET = '91330110MAEW31RB2Q'

# 1) build_cytoscape_data
cy = kg_engine.build_cytoscape_data(TARGET, use_owlrl=True)
if not cy.get('ok'):
    print('build FAILED:', cy)
    sys.exit(1)

print('center_name:', cy['center_name'])
print('merged_usccs:', cy['merged_usccs'])
print('stats:', cy['stats'])
print('owlrl:', cy['owlrl'])
print('n_nodes:', len(cy['elements']['nodes']))
print('n_edges:', len(cy['elements']['edges']))

# 2) render HTML to disk
OUT = 'scratchpad/kg-viz-test.html'
r = kg_engine.render_graph_html(cy, out_path=OUT)
print('render:', r)

# 3) 检查 HTML 里元素是否有效
with open(OUT, encoding='utf-8') as f:
    html = f.read()
print('html bytes:', len(html.encode('utf-8')))
print('has cytoscape cdn:', 'cytoscape.min.js' in html)
print('has elements_json:', 'const elements = ' in html)
print('has label 股东:', '股东' in html)
print('has label 法人:', '法人' in html)

# 4) 通过 slash 分发路径测试
from slash import dispatch
result = dispatch('/kg-visualize', f'{TARGET} --use_owlrl true')
print('slash result ok:', result.get('ok'))
print('slash out_path:', result.get('out_path'))
print('slash stats:', result.get('stats'))
