"""回填 equity_edge.holder_uscc + firm_wide.has_* 风险布尔列"""
import sys, json, duckdb

DB = 'skills/qcc-super/data/qcc.db'
TARGET = '91330110MAEW31RB2Q'

HOLDER_USCC = {
    '浙江瑞航国际贸易有限公司': '91330201MABPBTAF58',
    '厦门胶友科技有限公司':     '91350200MA3569Y783',
}

RAW_RISK = {
    '91330201MABPBTAF58': 'skills/qcc-super/data/raw_zhejiang_ruihang/risk_scan.json',
    '91350200MA3569Y783': 'skills/qcc-super/data/raw_xiamen_jiaoyou/risk_scan.json',
    '91330110MAEW31RB2Q': 'skills/qcc-super/data/raw_zhejiang_jiaoyou/risk_scan.json',
}

# 风险因子中文键 → 布尔列
FACTOR_TO_COL = {
    '失信信息':       'has_dishonest',
    '限制高消费':     'has_restricted_consumption',
    '被执行人':       'has_judgment_debtor',
    '股权冻结':       'has_equity_freeze',
    '经营异常':       'has_business_exception',
    '行政处罚':       'has_admin_penalty',
    '严重违法':       'has_severe_illegality',
    '欠税公告':       'has_tax_arrears',
    '股权出质':       'has_equity_pledge',
    '破产重整':       'has_bankruptcy',
    '开庭公告':       'has_court_hearing',
    '法院公告':       'has_court_notice',
    '欠税公告':       'has_tax_arrears',
    '环保处罚':       'has_environmental_penalty',
}

conn = duckdb.connect(DB)

# ---- 1. 回填 equity_edge.holder_uscc ----
print('=== 回填 equity_edge.holder_uscc ===')
for name, uscc in HOLDER_USCC.items():
    n = conn.execute(
        "UPDATE equity_edge SET holder_uscc = ? WHERE target_uscc = ? AND holder_name = ?",
        [uscc, TARGET, name]
    ).fetchone()
    print(f'  {name} -> {uscc}: affected')

for r in conn.execute("SELECT holder_uscc, holder_name, stake_ratio FROM equity_edge WHERE target_uscc=?", [TARGET]).fetchall():
    print(f'  verify: {r}')

# ---- 2. 回填 firm_wide.has_* 从 raw_risk_scan ----
print('\n=== 回填 firm_wide.has_* ===')
for uscc, raw_path in RAW_RISK.items():
    try:
        with open(raw_path, encoding='utf-8') as f:
            risk = json.load(f)
    except FileNotFoundError:
        print(f'  {uscc}: risk_scan.json not found, skip')
        continue
    updates = {}
    for item in risk.get('风险因子扫描', []):
        factor = item.get('风险因子')
        count = item.get('条目数', 0)
        col = FACTOR_TO_COL.get(factor)
        if col:
            updates[col] = 1 if count and count > 0 else 0
    if updates:
        set_clause = ', '.join(f'{k} = ?' for k in updates.keys())
        values = list(updates.values()) + [uscc]
        conn.execute(f'UPDATE firm_wide SET {set_clause} WHERE uscc = ?', values)
        print(f'  {uscc}: set {len(updates)} cols, values: {list(updates.values())}')

# ---- 3. 验证 ----
print('\n=== firm_wide.has_* 验证 ===')
for r in conn.execute("SELECT name, has_dishonest, has_restricted_consumption, has_judgment_debtor, has_equity_freeze, has_business_exception, has_admin_penalty, has_severe_illegality FROM firm_wide").fetchall():
    print(f'  {r}')

conn.commit()
conn.close()
print('\nDONE')
