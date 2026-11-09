"""关联方识别实测（调用 kg_engine.find_related_parties）"""
import sys, json
sys.path.insert(0, 'skills/qcc-super/scripts')
import kg_engine

r = kg_engine.find_related_parties('91330110MAEW31RB2Q')
print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
