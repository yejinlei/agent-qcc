# -*- coding: utf-8 -*-
import duckdb, json
conn = duckdb.connect("f:/src/agent-qcc/skills/qcc-super/data/qcc.db", read_only=True)
out = {}
out["firm_wide_cols_risk"] = [r[1] for r in conn.execute("PRAGMA table_info(firm_wide)").fetchall() if "has_" in r[1] or r[1] in ("risk_event_count","mcp_call_count")]
out["equity_edge_cols"] = [r[1] for r in conn.execute("PRAGMA table_info(equity_edge)").fetchall()]
out["event_wide_cols"] = [r[1] for r in conn.execute("PRAGMA table_info(event_wide)").fetchall()]
out["firm_wide_rows"] = conn.execute("SELECT uscc, name, legal_rep, controller_name, controller_stake, shareholder_count FROM firm_wide").fetchall()
out["equity_edge_all"] = conn.execute("SELECT holder_uscc, holder_name, holder_type, target_uscc, stake_ratio, subscribed_capital, paid_in_capital FROM equity_edge").fetchall()
out["event_wide_all"] = conn.execute("SELECT uscc, id, event_type FROM event_wide").fetchall()
out["executive_all"] = conn.execute("SELECT uscc, name, role FROM executive").fetchall()
conn.close()
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
