import duckdb
conn = duckdb.connect('skills/qcc-super/data/qcc.db', read_only=True)
cols = [r[0] for r in conn.execute("DESCRIBE firm_wide").fetchall()]
has_cols = [c for c in cols if c.startswith('has_')]
print('has_* columns:', len(has_cols))
for c in has_cols:
    print(' ', c)
conn.close()
