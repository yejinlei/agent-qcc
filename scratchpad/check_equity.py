import sys
sys.path.insert(0, 'skills/qcc-super/scripts')
import duckdb
c = duckdb.connect('skills/qcc-super/data/qcc.db', read_only=True)
print('=== equity_edge (target=91330110MAEW31RB2Q) ===')
rows = c.execute("SELECT holder_uscc, holder_name, holder_type, stake_ratio, target_uscc, target_name FROM equity_edge WHERE target_uscc='91330110MAEW31RB2Q'").fetchall()
for r in rows:
    print(r)
print()
print('=== firm_wide all ===')
for r in c.execute('SELECT uscc, name FROM firm_wide').fetchall():
    print(r)
print()
print('=== firm_wide has_* (浙江胶友) ===')
for r in c.execute("SELECT name, has_dishonest, has_restricted_consumption, has_judgment_debtor, has_equity_freeze, has_business_exception, has_admin_penalty FROM firm_wide").fetchall():
    print(r)
c.close()
