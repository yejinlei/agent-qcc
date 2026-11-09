# -*- coding: utf-8 -*-
"""核心查询处理器：从 DuckDB 大宽表读取数据"""
import json
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "qcc.db"


def _connect():
    import duckdb
    if not DB_PATH.exists():
        raise RuntimeError(f"DuckDB not initialized. Run bootstrap.py first. Path: {DB_PATH}")
    return duckdb.connect(str(DB_PATH), read_only=True)


def _rows_to_dicts(cur) -> list:
    """把 cursor 结果转成 dict 列表（无 pandas 依赖）。"""
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _resolve_target(conn, target: str) -> dict:
    """按 uscc 或 name 定位企业，返回一行 firm_wide。"""
    # 判断是否像 uscc（18 位）
    target_clean = target.strip()
    if len(target_clean) == 18 and all(c.isalnum() for c in target_clean):
        row = conn.execute("SELECT uscc, name FROM firm_wide WHERE uscc = ? LIMIT 1", [target_clean]).fetchone()
        if row:
            return {"uscc": row[0], "name": row[1]}
    # 走 name 匹配
    row = conn.execute("SELECT uscc, name FROM firm_wide WHERE name = ? LIMIT 1", [target_clean]).fetchone()
    if row:
        return {"uscc": row[0], "name": row[1]}
    # 模糊匹配
    row = conn.execute("SELECT uscc, name FROM firm_wide WHERE name LIKE ? LIMIT 1", [f"%{target_clean}%"]).fetchone()
    if row:
        return {"uscc": row[0], "name": row[1]}
    return None  # 未找到


def _empty_response(target: str, cmd: str, note: str) -> dict:
    """目标企业不在库中时，返回路由建议（提示 Agent 走 MCP 采集）。"""
    return {
        "ok": True,
        "found": False,
        "target": target,
        "note": note,
        "mcp_route": _suggest_mcp(cmd),
    }


def _suggest_mcp(cmd: str) -> list:
    """按命令类型给出 MCP 采集建议。"""
    m = {
        "qcc_profile":    ["qcc-company/get_company_registration_info", "qcc-company/get_company_profile"],
        "qcc_risk_scan":  ["qcc-risk/get_company_risk_scan"],
        "qcc_ubo":        ["qcc-company/get_actual_controller", "qcc-company/get_beneficial_owners"],
        "qcc_equity":     ["qcc-company/get_shareholder_info"],
        "qcc_executive":  ["qcc-executive/get_executive_info"],
        "qcc_events":     ["qcc-risk/get_company_risk_scan"],
        "qcc_financial":  ["qcc-company/get_financial_data"],
        "qcc_ipr":        ["qcc-ipr/get_patent_info", "qcc-ipr/get_trademark_info"],
    }
    return m.get(cmd, [])


# ---------- 处理器 ----------

def qcc_profile(args: dict, cmd: dict) -> dict:
    target = args.get("target")
    conn = _connect()
    try:
        hit = _resolve_target(conn, target)
        if not hit:
            return _empty_response(target, "qcc_profile", "企业不在 DuckDB，请先通过 MCP 采集入库")
        row = conn.execute("SELECT * FROM firm_wide WHERE uscc = ?", [hit["uscc"]]).fetchone()
        cols = [d[0] for d in conn.description]
        return {"found": True, "target": target, "uscc": hit["uscc"], "name": hit["name"], "data": dict(zip(cols, row))}
    finally:
        conn.close()


def qcc_risk_scan(args: dict, cmd: dict) -> dict:
    target = args.get("target")
    conn = _connect()
    try:
        hit = _resolve_target(conn, target)
        if not hit:
            return _empty_response(target, "qcc_risk_scan", "企业不在 DuckDB，请先通过 MCP 采集入库")
        cur = conn.execute(
            """
            SELECT uscc, name, risk_count_total,
              has_dishonest, has_restricted_consumption, has_judgment_debtor,
              has_equity_freeze, has_business_exception, has_admin_penalty,
              has_tax_arrears, has_tax_abnormal, has_severe_illegality,
              has_environmental_penalty, has_bankruptcy, has_property_auction,
              has_pre_litigation, has_equity_pledge, has_overdue_bill,
              has_customs_dishonest, has_related_risk, has_shareholder_risk,
              has_executive_risk, has_financial_risk
            FROM firm_wide WHERE uscc = ?
            """, [hit["uscc"]])
        data = _rows_to_dicts(cur)[0]
        cur2 = conn.execute("SELECT event_type, COUNT(*) AS n FROM event_wide WHERE uscc = ? GROUP BY event_type", [hit["uscc"]])
        data["event_breakdown"] = _rows_to_dicts(cur2)
        return {"found": True, "target": target, "uscc": hit["uscc"], "data": data}
    finally:
        conn.close()


def qcc_ubo(args: dict, cmd: dict) -> dict:
    target = args.get("target")
    threshold = float(args.get("threshold") or 0.25)
    conn = _connect()
    try:
        hit = _resolve_target(conn, target)
        if not hit:
            return _empty_response(target, "qcc_ubo", "企业不在 DuckDB，请先通过 MCP 采集入库")
        cur = conn.execute(
            """
            SELECT uscc, controller_name, controller_type, controller_stake,
                   controller_voting, ubo_name, ubo_stake, shareholder_count
            FROM firm_wide WHERE uscc = ?
            """, [hit["uscc"]])
        controller = _rows_to_dicts(cur)[0]
        cur2 = conn.execute(
            "SELECT holder_uscc, holder_name, holder_type, stake_ratio FROM equity_edge WHERE target_uscc = ? AND stake_ratio >= ? ORDER BY stake_ratio DESC",
            [hit["uscc"], threshold])
        ubo_candidates = _rows_to_dicts(cur2)
        return {
            "found": True, "target": target, "uscc": hit["uscc"],
            "threshold": threshold,
            "controller": controller,
            "ubo_candidates": ubo_candidates,
        }
    finally:
        conn.close()


def qcc_equity(args: dict, cmd: dict) -> dict:
    target = args.get("target")
    conn = _connect()
    try:
        hit = _resolve_target(conn, target)
        if not hit:
            return _empty_response(target, "qcc_equity", "企业不在 DuckDB，请先通过 MCP 采集入库")
        cur = conn.execute(
            "SELECT holder_uscc, holder_name, holder_type, stake_ratio, subscribed_capital, paid_in_capital, investment_date, is_direct FROM equity_edge WHERE target_uscc = ? ORDER BY stake_ratio DESC",
            [hit["uscc"]])
        outgoing = _rows_to_dicts(cur)
        cur2 = conn.execute(
            "SELECT target_uscc, target_name, stake_ratio, is_direct FROM equity_edge WHERE holder_uscc = ? ORDER BY stake_ratio DESC",
            [hit["uscc"]])
        investments = _rows_to_dicts(cur2)
        return {"found": True, "target": target, "uscc": hit["uscc"], "shareholders": outgoing, "external_investments": investments}
    finally:
        conn.close()


def qcc_executive(args: dict, cmd: dict) -> dict:
    target = args.get("target")
    conn = _connect()
    try:
        hit = _resolve_target(conn, target)
        if not hit:
            return _empty_response(target, "qcc_executive", "企业不在 DuckDB，请先通过 MCP 采集入库")
        cur = conn.execute(
            "SELECT name, role, appointed_at, resigned_at, is_active FROM executive WHERE uscc = ? ORDER BY role, appointed_at DESC",
            [hit["uscc"]])
        return {"found": True, "target": target, "uscc": hit["uscc"], "executives": _rows_to_dicts(cur)}
    finally:
        conn.close()


def qcc_events(args: dict, cmd: dict) -> dict:
    target = args.get("target")
    etype = args.get("type")
    limit = int(args.get("limit") or 100)
    conn = _connect()
    try:
        hit = _resolve_target(conn, target)
        if not hit:
            return _empty_response(target, "qcc_events", "企业不在 DuckDB，请先通过 MCP 采集入库")
        sql = "SELECT * FROM event_wide WHERE uscc = ?"
        params = [hit["uscc"]]
        if etype:
            sql += " AND event_type = ?"
            params.append(etype)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        return {"found": True, "target": target, "uscc": hit["uscc"], "type": etype, "events": _rows_to_dicts(cur)}
    finally:
        conn.close()


def qcc_financial(args: dict, cmd: dict) -> dict:
    target = args.get("target")
    conn = _connect()
    try:
        hit = _resolve_target(conn, target)
        if not hit:
            return _empty_response(target, "qcc_financial", "企业不在 DuckDB，请先通过 MCP 采集入库")
        cur = conn.execute(
            """
            SELECT uscc, name, revenue, net_profit, total_assets, total_liabilities,
                   owner_equity, current_ratio, debt_to_asset_ratio, gross_margin,
                   net_margin, revenue_yoy, asset_yoy
            FROM firm_wide WHERE uscc = ?
            """, [hit["uscc"]])
        return {"found": True, "target": target, "uscc": hit["uscc"], "data": _rows_to_dicts(cur)[0]}
    finally:
        conn.close()


def qcc_ipr(args: dict, cmd: dict) -> dict:
    target = args.get("target")
    conn = _connect()
    try:
        hit = _resolve_target(conn, target)
        if not hit:
            return _empty_response(target, "qcc_ipr", "企业不在 DuckDB，请先通过 MCP 采集入库")
        cur = conn.execute(
            "SELECT uscc, name, patent_count, trademark_count, software_copyright_count, work_copyright_count, icp_license_count FROM firm_wide WHERE uscc = ?",
            [hit["uscc"]])
        return {"found": True, "target": target, "uscc": hit["uscc"], "data": _rows_to_dicts(cur)[0]}
    finally:
        conn.close()
