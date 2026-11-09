"""
qcc-super Agent 查询 CLI。

用途：给上层 Agent 提供对 DuckDB 数据库的原子查询接口，
     只做查询，不做推理（推理交给尽调 Agent）。

用法：
    python query.py profile --uscc 91xxxxxxxxxxxxxxxx
    python query.py events --uscc 91xxxxxxxxxxxxxxxx --type dishonest
    python query.py equity --uscc 91xxxxxxxxxxxxxxxx
    python query.py executives --uscc 91xxxxxxxxxxxxxxxx
    python query.py tool-schema --term "实控人"
    python query.py tool-list --server qcc-risk
    python query.py raw --uscc 91xxxxxxxxxxxxxxxx --table firm_wide
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("[error] duckdb not installed. Run: python bootstrap.py", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "qcc.db"


def open_db(readonly: bool = True) -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        print(f"[error] DB not found at {DB_PATH}. Run bootstrap.py first.", file=sys.stderr)
        sys.exit(1)
    return duckdb.connect(str(DB_PATH), read_only=readonly)


def rows_to_json(conn, sql: str, params: list | None = None):
    """执行 SQL 并输出 JSON 数组（不依赖 pandas/numpy）"""
    result = conn.execute(sql, params or [])
    # 多语句时只取第一条结果
    columns = [desc[0] for desc in result.description]
    records = [dict(zip(columns, row)) for row in result.fetchall()]
    return json.dumps(records, ensure_ascii=False, default=str, indent=2)


# ---------- 子命令 ----------

def cmd_profile(args):
    """企业画像：一次查询出 firm_wide 全部字段"""
    conn = open_db()
    try:
        print(rows_to_json(
            conn,
            "SELECT * FROM firm_wide WHERE uscc = ?",
            [args.uscc],
        ))
    finally:
        conn.close()


def cmd_events(args):
    """事件明细"""
    conn = open_db()
    try:
        if args.type:
            print(rows_to_json(
                conn,
                "SELECT * FROM event_wide WHERE uscc = ? AND event_type = ? ORDER BY occurred_at DESC LIMIT ?",
                [args.uscc, args.type, args.limit],
            ))
        else:
            print(rows_to_json(
                conn,
                "SELECT * FROM event_wide WHERE uscc = ? ORDER BY occurred_at DESC LIMIT ?",
                [args.uscc, args.limit],
            ))
    finally:
        conn.close()


def cmd_equity(args):
    """股权关系（用于 Agent 做股权穿透）"""
    conn = open_db()
    try:
        print(rows_to_json(
            conn,
            "SELECT * FROM equity_edge WHERE target_uscc = ? OR holder_uscc = ? ORDER BY updated_at DESC",
            [args.uscc, args.uscc],
        ))
    finally:
        conn.close()


def cmd_executives(args):
    """董监高"""
    conn = open_db()
    try:
        print(rows_to_json(
            conn,
            "SELECT * FROM executive WHERE uscc = ? ORDER BY appointed_at DESC",
            [args.uscc],
        ))
    finally:
        conn.close()


def cmd_tool_schema(args):
    """按关键词查工具（Agent 路由用）"""
    conn = open_db()
    try:
        term = args.term
        print(rows_to_json(
            conn,
            """
            SELECT server, tool_name, title, return_field_count, return_fields, is_batch, efficiency
            FROM tool_catalog
            WHERE title LIKE ? OR return_fields LIKE ? OR description_clean LIKE ?
            ORDER BY is_batch DESC, efficiency DESC
            LIMIT ?
            """,
            [f"%{term}%", f"%{term}%", f"%{term}%", args.top],
        ))
    finally:
        conn.close()


def cmd_tool_list(args):
    """按 server 列工具"""
    conn = open_db()
    try:
        print(rows_to_json(
            conn,
            "SELECT tool_name, title, return_field_count, is_batch FROM tool_catalog WHERE server = ? ORDER BY is_batch DESC, tool_name",
            [args.server],
        ))
    finally:
        conn.close()


def cmd_raw(args):
    """查询任意表的原始行"""
    conn = open_db()
    try:
        print(rows_to_json(
            conn,
            f"SELECT * FROM {args.table} WHERE uscc = ? LIMIT ?",
            [args.uscc, args.limit],
        ))
    finally:
        conn.close()


def cmd_sql(args):
    """直接执行自定义 SQL（Agent 高级用法）"""
    conn = open_db()
    try:
        print(rows_to_json(conn, args.sql))
    finally:
        conn.close()


# ---------- CLI ----------

def build_parser():
    p = argparse.ArgumentParser(description="qcc-super query CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("profile", help="企业画像（firm_wide 全字段）")
    sp.add_argument("--uscc", required=True)
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("events", help="事件明细")
    sp.add_argument("--uscc", required=True)
    sp.add_argument("--type", help="事件类型过滤（dishonest/penalty/...）")
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser("equity", help="股权关系")
    sp.add_argument("--uscc", required=True)
    sp.set_defaults(func=cmd_equity)

    sp = sub.add_parser("executives", help="董监高")
    sp.add_argument("--uscc", required=True)
    sp.set_defaults(func=cmd_executives)

    sp = sub.add_parser("tool-schema", help="按关键词查工具")
    sp.add_argument("--term", required=True)
    sp.add_argument("--top", type=int, default=10)
    sp.set_defaults(func=cmd_tool_schema)

    sp = sub.add_parser("tool-list", help="按 server 列工具")
    sp.add_argument("--server", required=True)
    sp.set_defaults(func=cmd_tool_list)

    sp = sub.add_parser("raw", help="查询任意表原始行")
    sp.add_argument("--uscc", required=True)
    sp.add_argument("--table", required=True, help="firm_wide / event_wide / equity_edge / executive")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_raw)

    sp = sub.add_parser("sql", help="直接执行 SQL（高级用法）")
    sp.add_argument("--sql", required=True)
    sp.set_defaults(func=cmd_sql)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
