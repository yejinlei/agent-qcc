# -*- coding: utf-8 -*-
"""元查询处理器：工具检索 / 自定义 SQL"""
import json
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "qcc.db"


def _connect():
    import duckdb
    if not DB_PATH.exists():
        raise RuntimeError(f"DuckDB not initialized: {DB_PATH}")
    return duckdb.connect(str(DB_PATH), read_only=True)


def _rows_to_dicts(cur) -> list:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def tool_search(args: dict, cmd: dict) -> dict:
    term = args.get("term", "")
    top = int(args.get("top") or 5)
    conn = _connect()
    try:
        # tool_catalog.description_clean + tool_name + title 模糊匹配
        sql = """
            SELECT id, server, tool_name, title, description_clean,
                   return_field_count, is_batch, efficiency
            FROM tool_catalog
            WHERE lower(description_clean) LIKE ?
               OR lower(tool_name) LIKE ?
               OR lower(title) LIKE ?
            ORDER BY efficiency DESC, return_field_count DESC
            LIMIT ?
        """
        cur = conn.execute(sql, [f"%{term.lower()}%", f"%{term.lower()}%", f"%{term.lower()}%", top])
        return {"term": term, "top": top, "tools": _rows_to_dicts(cur)}
    finally:
        conn.close()


def raw_sql(args: dict, cmd: dict) -> dict:
    sql = args.get("sql", "")
    if not isinstance(sql, str):
        return {"error": "empty sql"}
    sql = sql.strip()
    # 剥掉外层引号（CLI 分词会保留引号，多词 SQL 必须整体引起来）
    if len(sql) >= 2 and sql[0] == sql[-1] and sql[0] in ('"', "'"):
        sql = sql[1:-1]
    if not sql.strip():
        return {"error": "empty sql"}
    # 仅允许 SELECT
    if not sql.strip().lower().startswith("select"):
        return {"error": "only SELECT statements allowed"}
    conn = _connect()
    try:
        cur = conn.execute(sql)
        return {"sql": sql, "rows": _rows_to_dicts(cur)}
    finally:
        conn.close()
