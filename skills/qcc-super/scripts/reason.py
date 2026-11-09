"""
qcc-super 尽调推理（测试版）

用途：作为 12 条推理规则的参考实现 / 测试夹具，验证 SKILL.md 中的推理链路
      能被上层尽调 Agent 复现。

注意：正式推理职责由上层尽调 Agent 承担，本脚本仅用于：
  · 规则的回归测试
  · Agent 输出结果的对照校验
  · 演示/教学用途

用法：
    python reason.py --uscc 91xxxxxxxxxxxxxxxx
    python reason.py --uscc 91xxxxxxxxxxxxxxxx --format json
    python reason.py --demo                          # 内置样例数据自测

输出：结构化推理结果（评级 / 规则命中 / 建议）
"""
import argparse
import json
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Any

try:
    import duckdb
except ImportError:
    print("[error] duckdb not installed. Run: python bootstrap.py", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "qcc.db"


# ---------- 规则数据结构 ----------

class RuleHit:
    """规则命中记录"""
    def __init__(self, rule_id: str, name: str, triggered: bool,
                 detail: dict[str, Any], delta: float = 0.0, veto: bool = False):
        self.rule_id = rule_id
        self.name = name
        self.triggered = triggered
        self.detail = detail
        self.delta = delta
        self.veto = veto

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "triggered": self.triggered,
            "detail": self.detail,
            "delta": self.delta,
            "veto": self.veto,
        }


# ---------- 数据加载 ----------

def _rows(conn, sql: str, params: list | None = None) -> list[dict]:
    r = conn.execute(sql, params or [])
    cols = [d[0] for d in r.description]
    return [dict(zip(cols, row)) for row in r.fetchall()]


def load_firm(conn, uscc: str) -> dict | None:
    rows = _rows(conn, "SELECT * FROM firm_wide WHERE uscc = ?", [uscc])
    return rows[0] if rows else None


def load_events(conn, uscc: str) -> list[dict]:
    return _rows(conn, "SELECT * FROM event_wide WHERE uscc = ?", [uscc])


def load_equity_edges(conn, uscc: str) -> list[dict]:
    return _rows(
        conn,
        "SELECT * FROM equity_edge WHERE holder_uscc = ? OR target_uscc = ?",
        [uscc, uscc],
    )


def load_executives(conn, uscc: str) -> list[dict]:
    return _rows(conn, "SELECT * FROM executive WHERE uscc = ?", [uscc])


# ---------- 12 条规则 ----------

def rule1_direct_risk(firm: dict) -> RuleHit:
    """规则1: 直接风险信号"""
    direct = {
        "has_dishonest": "失信",
        "has_judgment_debtor": "被执行",
        "has_restricted_consumption": "限高",
        "has_equity_freeze": "股权冻结",
        "has_business_exception": "经营异常",
    }
    secondary = {
        "has_admin_penalty": "行政处罚",
        "has_tax_arrears": "欠税公告",
        "has_tax_abnormal": "税务非正常户",
        "has_severe_illegality": "严重违法",
    }
    hits_d = [label for k, label in direct.items() if firm.get(k)]
    hits_c = [label for k, label in secondary.items() if firm.get(k)]
    hits_b = []
    if firm.get("has_business_exception"):
        hits_b.append("经营异常")
    if firm.get("has_restricted_consumption"):
        hits_b.append("限高")

    if hits_d:
        return RuleHit("R1", "直接风险", True,
                       {"grade": "D", "signals": hits_d})
    if hits_c:
        return RuleHit("R1", "直接风险", True,
                       {"grade": "C", "signals": hits_c})
    if hits_b:
        return RuleHit("R1", "直接风险", True,
                       {"grade": "B", "signals": hits_b})
    return RuleHit("R1", "直接风险", False, {"grade": "A"})


def rule2_bankruptcy_contagion(firm: dict, edges: list[dict]) -> RuleHit:
    """规则2: 关联破产传导 - 实控人控制的其他企业存在破产/清算"""
    controller = firm.get("controller_name")
    if not controller:
        return RuleHit("R2", "关联破产传导", False, {})
    # 找同实控人控制的其他企业（简化：通过 holder_uscc = controller 关联）
    other_targets = [e for e in edges if e.get("holder_uscc") == controller]
    risk_targets = [e for e in other_targets
                    if e.get("raw_json") and "破产" in str(e.get("raw_json"))]
    stake = float(firm.get("controller_stake") or 0)
    if risk_targets:
        return RuleHit("R2", "关联破产传导", True,
                       {"risk_count": len(risk_targets), "stake": stake},
                       delta=0.3 * stake)
    return RuleHit("R2", "关联破产传导", False, {})


def rule3_shell_company(firm: dict, events: list[dict]) -> RuleHit:
    """规则3: 壳公司识别 - 法代/地址频繁变更 + 无社保"""
    now = datetime.now()
    two_years_ago = date(now.year - 2, now.month, now.day)
    # 通过 event_wide 统计法代变更和地址变更
    legal_rep_changes = sum(1 for e in events
                            if e.get("event_type") == "legal_rep_change"
                            and e.get("occurred_at") >= two_years_ago)
    addr_changes = sum(1 for e in events
                       if e.get("event_type") == "address_change"
                       and e.get("occurred_at") >= two_years_ago)
    insured = firm.get("employee_count") or 0
    suspicious = (legal_rep_changes >= 3 and addr_changes >= 2 and insured == 0)
    return RuleHit("R3", "壳公司识别", suspicious,
                   {"legal_rep_changes": legal_rep_changes,
                    "addr_changes": addr_changes,
                    "insured": insured},
                   veto=suspicious)


def rule4_shareholder_risk(events: list[dict]) -> RuleHit:
    """规则4: 股东风险传导 - 大股东(>=20%)存在失信/被执行"""
    shareholder_risks = [e for e in events
                         if e.get("event_type") in ("dishonest", "judgment_debtor")
                         and (e.get("raw_json") or {}).get("holder_type") == "shareholder"]
    triggered = False
    total_delta = 0.0
    for e in shareholder_risks:
        ratio = float((e.get("raw_json") or {}).get("stake_ratio", 0))
        if ratio >= 0.20:
            triggered = True
            total_delta += 0.2 * ratio
    return RuleHit("R4", "股东风险传导", triggered,
                   {"shareholder_risks": len(shareholder_risks)},
                   delta=total_delta)


def rule5_equity_freeze(firm: dict, events: list[dict]) -> RuleHit:
    """规则5: 股权冻结影响"""
    freeze_events = [e for e in events if e.get("event_type") == "equity_freeze"]
    if not freeze_events and not firm.get("has_equity_freeze"):
        return RuleHit("R5", "股权冻结", False, {})
    total_ratio = sum(float(e.get("raw_json", {}).get("freeze_ratio", 0)) for e in freeze_events)
    return RuleHit("R5", "股权冻结", True,
                   {"freeze_count": len(freeze_events), "total_ratio": total_ratio},
                   delta=0.15 * total_ratio)


def rule6_executive_risk(events: list[dict], firm: dict) -> RuleHit:
    """规则6: 个人司法风险 - 高管存在失信/限高/限出境/股权冻结"""
    veto_signals = {"dishonest_exec", "restricted_consumption_exec",
                    "exit_restriction", "equity_freeze_exec"}
    hits = [e for e in events if e.get("event_type") in veto_signals]
    if hits or firm.get("has_executive_risk"):
        return RuleHit("R6", "个人司法风险", True,
                       {"signals": [e.get("event_type") for e in hits]},
                       veto=True)
    return RuleHit("R6", "个人司法风险", False, {})


def rule7_systemic_risk(controller: str | None, edges: list[dict],
                        event_map: dict) -> RuleHit:
    """规则7: 系统性风险主体 - 实控人控制 N 家企业，其中 M 家有风险"""
    if not controller:
        return RuleHit("R7", "系统性风险主体", False, {})
    controlled = {e.get("target_uscc") for e in edges if e.get("holder_uscc") == controller}
    risk_types = {"dishonest", "judgment_debtor", "restricted_consumption",
                  "business_exception"}
    risky = {uscc for uscc in controlled
             if any(e.get("event_type") in risk_types for e in event_map.get(uscc, []))}
    if controlled:
        ratio = len(risky) / len(controlled)
        triggered = ratio >= 0.5
        return RuleHit("R7", "系统性风险主体", triggered,
                       {"controlled": len(controlled), "risky": len(risky),
                        "ratio": ratio},
                       delta=ratio * 100)
    return RuleHit("R7", "系统性风险主体", False, {})


def rule8_executive_overlap(executives: list[dict], event_map: dict) -> RuleHit:
    """规则8: 任职重叠风险 - 高管在其他风险企业任职"""
    risk_types = {"dishonest", "business_exception", "judgment_debtor"}
    overlaps = []
    for ex in executives:
        ex_name = ex.get("name")
        ex_uscc = ex.get("uscc")
        for other_uscc, evts in event_map.items():
            if other_uscc == ex_uscc:
                continue
            if any(e.get("event_type") in risk_types for e in evts):
                overlaps.append({"person": ex_name, "risk_uscc": other_uscc})
                break
    return RuleHit("R8", "任职重叠风险", bool(overlaps), {"overlaps": overlaps})


def rule9_control_decay(firm: dict) -> RuleHit:
    """规则9: 控制力衰减 - 持股 <50% 且非法代"""
    stake = float(firm.get("controller_stake") or 0)
    is_legal_rep = firm.get("legal_rep") == firm.get("controller_name")
    if stake < 0.5 and not is_legal_rep:
        return RuleHit("R9", "控制力衰减", True,
                       {"stake": stake, "is_legal_rep": is_legal_rep},
                       delta=-0.1)
    return RuleHit("R9", "控制力衰减", False, {})


def rule10_network_density(uscc: str, edges: list[dict], event_map: dict) -> RuleHit:
    """规则10: 关联网络密度"""
    related = {e.get("holder_uscc") for e in edges if e.get("target_uscc") == uscc}
    related |= {e.get("target_uscc") for e in edges if e.get("holder_uscc") == uscc}
    related.discard(uscc)
    risk_types = {"dishonest", "judgment_debtor", "restricted_consumption",
                  "business_exception"}
    risky_related = {u for u in related if any(
        e.get("event_type") in risk_types for e in event_map.get(u, []))}
    if related:
        ratio = len(risky_related) / len(related)
        return RuleHit("R10", "关联网络密度", ratio > 0,
                       {"related": len(related), "risky": len(risky_related),
                        "density": ratio},
                       delta=ratio)
    return RuleHit("R10", "关联网络密度", False, {})


def rule11_guarantee_contagion(edges: list[dict], event_map: dict) -> RuleHit:
    """规则11: 资金链断裂 - 担保的企业存在破产/被执行"""
    # 简化：担保关系通过 event_type='guarantee' 承载
    guarantee_events = [e for e in event_map.get("", []) if e.get("event_type") == "guarantee"]
    return RuleHit("R11", "资金链断裂", False,
                   {"note": "需担保事件数据支撑"})


def rule12_controller_change(events: list[dict]) -> RuleHit:
    """规则12: 实控人变更预警 - 1 年内法代变更 >=2 次"""
    now = datetime.now()
    one_year_ago = date(now.year - 1, now.month, now.day)
    recent_changes = [e for e in events
                      if e.get("event_type") == "legal_rep_change"
                      and e.get("occurred_at") and e.get("occurred_at") >= one_year_ago]
    if len(recent_changes) >= 2:
        return RuleHit("R12", "实控人变更预警", True,
                       {"recent_changes": len(recent_changes)},
                       delta=0.15)
    return RuleHit("R12", "实控人变更预警", False, {})


# ---------- 推理入口 ----------

def reason(uscc: str, conn=None) -> dict:
    """执行全部 12 条规则，输出推理结果"""
    own_conn = False
    if conn is None:
        if not DB_PATH.exists():
            print(f"[error] DB not found at {DB_PATH}", file=sys.stderr)
            sys.exit(1)
        conn = duckdb.connect(str(DB_PATH), read_only=True)
        own_conn = True

    try:
        firm = load_firm(conn, uscc)
        if not firm:
            return {"uscc": uscc, "error": "firm_wide 无数据", "hits": []}

        events = load_events(conn, uscc)
        edges = load_equity_edges(conn, uscc)
        executives = load_executives(conn, uscc)

        # 构建关联企业事件索引（用于规则7/10/11）
        related_usccs = {e.get("holder_uscc") for e in edges} | {e.get("target_uscc") for e in edges}
        related_usccs.discard(uscc)
        related_usccs.discard(None)
        event_map: dict[str, list[dict]] = {}
        if related_usccs:
            placeholders = ",".join("?" * len(related_usccs))
            all_events = _rows(
                conn,
                f"SELECT * FROM event_wide WHERE uscc IN ({placeholders})",
                list(related_usccs),
            )
            for e in all_events:
                event_map.setdefault(e["uscc"], []).append(e)

        # 跑 12 条规则
        hits = [
            rule1_direct_risk(firm),
            rule2_bankruptcy_contagion(firm, edges),
            rule3_shell_company(firm, events),
            rule4_shareholder_risk(events),
            rule5_equity_freeze(firm, events),
            rule6_executive_risk(events, firm),
            rule7_systemic_risk(firm.get("controller_name"), edges, event_map),
            rule8_executive_overlap(executives, event_map),
            rule9_control_decay(firm),
            rule10_network_density(uscc, edges, event_map),
            rule11_guarantee_contagion(edges, event_map),
            rule12_controller_change(events),
        ]

        # 汇总
        veto = any(h.veto for h in hits)
        base_grade = "A"
        for h in hits:
            if h.rule_id == "R1" and h.triggered:
                base_grade = h.detail.get("grade", base_grade)
                break

        # veto 强制 D
        if veto:
            final_grade = "D"
        else:
            final_grade = base_grade

        total_delta = sum(h.delta for h in hits if h.triggered)
        risk_score = max(0.0, min(100.0, 50 + total_delta * 100))

        result = {
            "uscc": uscc,
            "name": firm.get("name"),
            "final_grade": final_grade,
            "veto": veto,
            "risk_score": round(risk_score, 2),
            "controller": firm.get("controller_name"),
            "controller_stake": float(firm.get("controller_stake") or 0),
            "hits": [h.to_dict() for h in hits if h.triggered],
            "all_rules": [h.to_dict() for h in hits],
            "recommendation": _recommendation(final_grade, veto),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        return result
    finally:
        if own_conn:
            conn.close()


def _recommendation(grade: str, veto: bool) -> str:
    if veto or grade == "D":
        return "拒绝 (Reject)"
    if grade == "C":
        return "条件准入 (Conditional Accept)"
    if grade == "B":
        return "谨慎准入 (Cautious Accept)"
    return "准入 (Accept)"


# ---------- Demo 数据 ----------

def _build_demo() -> dict:
    """内置样例：一家带若干风险信号的企业，用于测试 12 条规则"""
    return {
        "firm": {
            "uscc": "91TESTDEMO000001",
            "name": "演示科技有限公司",
            "legal_rep": "张三",
            "controller_name": "张三",
            "controller_stake": 0.65,
            "reg_capital": 10000000,
            "business_status": "存续",
            "employee_count": 0,
            "has_dishonest": False,
            "has_judgment_debtor": False,
            "has_restricted_consumption": False,
            "has_equity_freeze": False,
            "has_business_exception": True,   # 触发 R1 grade B
            "has_admin_penalty": True,        # 触发 R1 grade C
            "has_tax_violation": False,
            "has_executive_risk": False,
        },
        "events": [
            {"event_type": "legal_rep_change", "occurred_at": date.today(),
             "raw_json": {}},
            {"event_type": "legal_rep_change", "occurred_at": date.today(),
             "raw_json": {}},
            {"event_type": "address_change", "occurred_at": date.today(),
             "raw_json": {}},
            {"event_type": "address_change", "occurred_at": date.today(),
             "raw_json": {}},
            {"event_type": "business_exception", "occurred_at": date.today(),
             "raw_json": {}},
            {"event_type": "admin_penalty", "occurred_at": date.today(),
             "raw_json": {}},
        ],
        "edges": [],
        "executives": [{"uscc": "91TESTDEMO000001", "name": "张三", "role": "法代"}],
    }


def _reason_from_data(data: dict) -> dict:
    """在内存数据上跑 12 条规则（不查 DB），仅用于 demo"""
    firm = data["firm"]
    events = data["events"]
    edges = data["edges"]
    executives = data["executives"]

    hits = [
        rule1_direct_risk(firm),
        rule2_bankruptcy_contagion(firm, edges),
        rule3_shell_company(firm, events),
        rule4_shareholder_risk(events),
        rule5_equity_freeze(firm, events),
        rule6_executive_risk(events, firm),
        rule7_systemic_risk(firm.get("controller_name"), edges, {}),
        rule8_executive_overlap(executives, {}),
        rule9_control_decay(firm),
        rule10_network_density(firm["uscc"], edges, {}),
        rule11_guarantee_contagion(edges, {}),
        rule12_controller_change(events),
    ]

    veto = any(h.veto for h in hits)
    base_grade = "A"
    for h in hits:
        if h.rule_id == "R1" and h.triggered:
            base_grade = h.detail.get("grade", base_grade)
            break

    final_grade = "D" if veto else base_grade
    total_delta = sum(h.delta for h in hits if h.triggered)
    risk_score = max(0.0, min(100.0, 50 + total_delta * 100))

    return {
        "uscc": firm["uscc"],
        "name": firm["name"],
        "final_grade": final_grade,
        "veto": veto,
        "risk_score": round(risk_score, 2),
        "hits": [h.to_dict() for h in hits if h.triggered],
        "all_rules": [h.to_dict() for h in hits],
        "recommendation": _recommendation(final_grade, veto),
    }


# ---------- Markdown 报告 ----------

def to_markdown(result: dict) -> str:
    lines = []
    lines.append(f"# {result.get('name', result['uscc'])} 风险推理报告")
    lines.append("")
    lines.append(f"- **USCC**: {result['uscc']}")
    lines.append(f"- **综合评级**: {result['final_grade']}")
    lines.append(f"- **风险分**: {result['risk_score']}")
    lines.append(f"- **一票否决**: {'是' if result['veto'] else '否'}")
    lines.append(f"- **建议**: {result['recommendation']}")
    lines.append("")
    lines.append("## 命中的推理规则")
    if result["hits"]:
        for h in result["hits"]:
            veto_tag = " [VETO]" if h["veto"] else ""
            delta_tag = f" (delta={h['delta']:+.3f})" if h["delta"] else ""
            lines.append(f"- **{h['rule_id']}** {h['name']}{veto_tag}{delta_tag}")
            for k, v in h["detail"].items():
                lines.append(f"  - {k}: {v}")
    else:
        lines.append("- (无命中)")
    lines.append("")
    lines.append("## 全部规则检查")
    for r in result.get("all_rules", []):
        mark = "✓" if r["triggered"] else "·"
        lines.append(f"- {mark} {r['rule_id']} {r['name']}")
    lines.append("")
    return "\n".join(lines)


# ---------- CLI ----------

def main():
    p = argparse.ArgumentParser(description="qcc-super 尽调推理（测试版）")
    p.add_argument("--uscc", help="企业 USCC，执行时从 DB 抽数据")
    p.add_argument("--demo", action="store_true", help="用内置样例数据自测")
    p.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = p.parse_args()

    if args.demo:
        result = _reason_from_data(_build_demo())
    elif args.uscc:
        result = reason(args.uscc)
    else:
        p.error("需要提供 --uscc 或 --demo")

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(to_markdown(result))


if __name__ == "__main__":
    main()
