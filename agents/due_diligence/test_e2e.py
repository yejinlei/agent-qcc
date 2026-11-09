"""端到端验证：造 mock 数据 → skill reason → 上层 Agent → 对比

用途：
  · 一次性验证 12 条规则在 skill 测试版和上层 Agent 版本上输出一致
  · 提供真实 DuckDB 场景下的推理样例

用法：
    python -m agents.due_diligence.test_e2e
    python agents/due_diligence/test_e2e.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    import duckdb
except ImportError as e:
    print(f"[error] duckdb not installed: {e}", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "skills" / "qcc-super"
DB_PATH = SKILL_ROOT / "data" / "qcc.db"


# mock 数据：一家带多重风险信号的公司 + 一个风险股东
DEMO_FIRM = {
    "uscc": "91E2ETST00000001",
    "name": "演示风险科技有限公司",
    "legal_rep": "张三",
    "controller_name": "张三",
    "controller_stake": 0.65,
    "controller_voting": 0.65,
    "ubo_name": "张三",
    "ubo_stake": 0.65,
    "reg_capital": 10_000_000.00,
    "business_status": "存续",
    "company_type": "有限责任公司",
    "industry": "软件和信息技术服务业",
    "employee_count": 0,               # 无社保 → 触发 R3 部分条件
    "shareholder_count": 2,
    "executive_count": 1,
    "has_dishonest": False,
    "has_judgment_debtor": False,
    "has_restricted_consumption": False,
    "has_equity_freeze": False,
    "has_business_exception": True,    # → R1 grade D
    "has_admin_penalty": True,         # 二级
    "has_tax_arrears": False,
    "has_executive_risk": False,
    "has_financial_risk": False,
    "risk_count_total": 3,
    "updated_at": None,
}

DEMO_EVENTS = [
    {"id": 1, "uscc": "91E2ETST00000001", "name": "演示风险科技有限公司",
     "event_type": "legal_rep_change", "occurred_at": date.today(),
     "raw_json": {}},
    {"id": 2, "uscc": "91E2ETST00000001", "name": "演示风险科技有限公司",
     "event_type": "legal_rep_change", "occurred_at": date.today() - timedelta(days=30),
     "raw_json": {}},
    {"id": 3, "uscc": "91E2ETST00000001", "name": "演示风险科技有限公司",
     "event_type": "address_change", "occurred_at": date.today() - timedelta(days=10),
     "raw_json": {}},
    {"id": 4, "uscc": "91E2ETST00000001", "name": "演示风险科技有限公司",
     "event_type": "address_change", "occurred_at": date.today() - timedelta(days=60),
     "raw_json": {}},
    {"id": 5, "uscc": "91E2ETST00000001", "name": "演示风险科技有限公司",
     "event_type": "address_change", "occurred_at": date.today() - timedelta(days=90),
     "raw_json": {}},
    {"id": 6, "uscc": "91E2ETST00000001", "name": "演示风险科技有限公司",
     "event_type": "business_exception", "occurred_at": date.today() - timedelta(days=5),
     "raw_json": {}},
    {"id": 7, "uscc": "91E2ETST00000001", "name": "演示风险科技有限公司",
     "event_type": "admin_penalty", "occurred_at": date.today() - timedelta(days=15),
     "raw_json": {}},
]

DEMO_EDGES = []  # 本例不测股权穿透链路

DEMO_EXEC = [
    {"id": 1, "uscc": "91E2ETST00000001", "name": "张三", "role": "法代",
     "appointed_at": date.today() - timedelta(days=100), "is_active": True},
]


def seed(conn: duckdb.DuckDBPyConnection) -> None:
    """灌 mock 数据（幂等：先删后插）"""
    conn.execute("DELETE FROM firm_wide WHERE uscc = ?", [DEMO_FIRM["uscc"]])
    conn.execute("DELETE FROM event_wide WHERE uscc = ?", [DEMO_FIRM["uscc"]])
    conn.execute("DELETE FROM equity_edge WHERE target_uscc = ? OR holder_uscc = ?",
                 [DEMO_FIRM["uscc"], DEMO_FIRM["uscc"]])
    conn.execute("DELETE FROM executive WHERE uscc = ?", [DEMO_FIRM["uscc"]])

    conn.execute(
        """
        INSERT INTO firm_wide (uscc, name, legal_rep, controller_name, controller_stake,
                               controller_voting, ubo_name, ubo_stake,
                               reg_capital, business_status, company_type, industry,
                               employee_count, shareholder_count, executive_count,
                               has_business_exception, has_admin_penalty,
                               risk_count_total, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
        """,
        [DEMO_FIRM["uscc"], DEMO_FIRM["name"], DEMO_FIRM["legal_rep"],
         DEMO_FIRM["controller_name"], DEMO_FIRM["controller_stake"],
         DEMO_FIRM["controller_voting"], DEMO_FIRM["ubo_name"], DEMO_FIRM["ubo_stake"],
         DEMO_FIRM["reg_capital"], DEMO_FIRM["business_status"], DEMO_FIRM["company_type"],
         DEMO_FIRM["industry"], DEMO_FIRM["employee_count"], DEMO_FIRM["shareholder_count"],
         DEMO_FIRM["executive_count"], DEMO_FIRM["has_business_exception"],
         DEMO_FIRM["has_admin_penalty"], DEMO_FIRM["risk_count_total"]],
    )

    for ev in DEMO_EVENTS:
        conn.execute(
            """INSERT INTO event_wide (id, uscc, name, event_type, occurred_at, raw_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [ev["id"], ev["uscc"], ev["name"], ev["event_type"],
             ev["occurred_at"], json.dumps(ev.get("raw_json", {}), ensure_ascii=False)],
        )

    for ed in DEMO_EDGES:
        pass  # 本例无 edges

    for ex in DEMO_EXEC:
        conn.execute(
            """INSERT INTO executive (id, uscc, name, role, appointed_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [ex["id"], ex["uscc"], ex["name"], ex["role"],
             ex["appointed_at"], ex["is_active"]],
        )


def main() -> None:
    if not DB_PATH.exists():
        print(f"[error] DB not found at {DB_PATH}. Run bootstrap.py first.", file=sys.stderr)
        sys.exit(1)

    # 1. seed mock
    conn = duckdb.connect(str(DB_PATH))
    try:
        seed(conn)
        print(f"[e2e] Seeded mock data for uscc={DEMO_FIRM['uscc']}")
    finally:
        conn.close()

    # 2. skill reason.py（--uscc 模式）
    print("\n" + "=" * 60)
    print("SKILL reason.py --uscc")
    print("=" * 60)
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))
    import reason as skill_reason  # noqa: E402
    skill_result = skill_reason.reason(DEMO_FIRM["uscc"])
    print(json.dumps(skill_result, ensure_ascii=False, indent=2, default=str))

    # 3. 上层 Agent
    print("\n" + "=" * 60)
    print("Agent DueDiligenceAgent.run")
    print("=" * 60)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from agents.due_diligence import DueDiligenceAgent  # noqa: E402
    agent = DueDiligenceAgent()
    agent_result = agent.run(DEMO_FIRM["uscc"], fmt="json")
    print(json.dumps(agent_result, ensure_ascii=False, indent=2, default=str))

    # 4. 一致性对比
    print("\n" + "=" * 60)
    print("CONSISTENCY CHECK")
    print("=" * 60)
    skill_hits = {h["rule_id"] for h in skill_result["hits"]}
    agent_hits = {h["rule_id"] for h in agent_result["hits"]}
    print(f"skill  hits: {sorted(skill_hits)}")
    print(f"agent  hits: {sorted(agent_hits)}")
    print(f"grade  skill={skill_result['final_grade']}  agent={agent_result['final_grade']}")
    print(f"veto   skill={skill_result['veto']}  agent={agent_result['veto']}")
    match = skill_hits == agent_hits and skill_result["final_grade"] == agent_result["final_grade"]
    print(f"一致: {'✓ PASS' if match else '✗ FAIL'}")


if __name__ == "__main__":
    main()
