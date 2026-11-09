"""尽调推理引擎（正式实现）

职责：接收结构化企业数据（来自 qcc-super DuckDB 或 MCP 直连），执行 12 条
      风险推理规则，输出评级 / 风险分 / 建议 / 命中链路。

设计原则：
  1. 纯函数化 - 输入 dict，输出 dict，无外部 IO（便于单测和 LangGraph 集成）
  2. 规则可插拔 - 每条规则是独立函数，方便增删改
  3. 与 skill 测试版 reason.py 保持规则编号一致（R1~R12），便于交叉验证
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any, Callable

RuleFn = Callable[..., "RuleHit"]


def _as_dict(val: Any) -> dict:
    """DuckDB JSON 列可能返回 dict 或 JSON 字符串，统一转 dict"""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            import json
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


# ---------- 数据结构 ----------

@dataclass
class RuleHit:
    """单条规则命中记录"""
    rule_id: str
    name: str
    triggered: bool
    detail: dict = field(default_factory=dict)
    delta: float = 0.0
    veto: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReasoningResult:
    """推理结果"""
    subject: dict[str, Any]
    final_grade: str
    veto: bool
    risk_score: float
    hits: list[RuleHit]
    all_rules: list[RuleHit]
    recommendation: str
    generated_at: str

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "final_grade": self.final_grade,
            "veto": self.veto,
            "risk_score": self.risk_score,
            "hits": [h.to_dict() for h in self.hits],
            "all_rules": [h.to_dict() for h in self.all_rules],
            "recommendation": self.recommendation,
            "generated_at": self.generated_at,
        }


@dataclass
class EnterpriseFacts:
    """企业事实集合（推理引擎的输入）"""
    firm: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    equity_edges: list[dict[str, Any]] = field(default_factory=list)
    executives: list[dict[str, Any]] = field(default_factory=list)
    related_events: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


# ---------- 12 条推理规则 ----------

# 直接风险信号（一票否决级别）
DIRECT_RISK_MAP = {
    "has_dishonest": "失信",
    "has_judgment_debtor": "被执行",
    "has_restricted_consumption": "限高",
    "has_equity_freeze": "股权冻结",
    "has_business_exception": "经营异常",
}
# 次级风险信号
SECONDARY_RISK_MAP = {
    "has_admin_penalty": "行政处罚",
    "has_tax_arrears": "欠税公告",
    "has_tax_abnormal": "税务非正常户",
    "has_severe_illegality": "严重违法",
}
RISK_EVENT_TYPES = {"dishonest", "judgment_debtor", "restricted_consumption", "business_exception"}


def rule1_direct_risk(facts: EnterpriseFacts) -> RuleHit:
    firm = facts.firm
    direct_hits = [v for k, v in DIRECT_RISK_MAP.items() if firm.get(k)]
    secondary_hits = [v for k, v in SECONDARY_RISK_MAP.items() if firm.get(k)]

    if direct_hits:
        grade = "D"
        signals = direct_hits
    elif secondary_hits:
        grade = "C"
        signals = secondary_hits
    else:
        grade = "A"
        signals = []

    return RuleHit("R1", "直接风险", grade != "A",
                   {"grade": grade, "signals": signals})


def rule2_bankruptcy_contagion(facts: EnterpriseFacts) -> RuleHit:
    firm = facts.firm
    controller = firm.get("controller_name")
    if not controller:
        return RuleHit("R2", "关联破产传导", False, {})
    stake = float(firm.get("controller_stake") or 0)
    other_targets = [e for e in facts.equity_edges if e.get("holder_uscc") == controller]
    risk_targets = [
        e for e in other_targets
        if e.get("target_uscc") != firm.get("uscc")
        and any(
            ev.get("event_type") in ("bankruptcy", "clearance")
            for ev in facts.related_events.get(e.get("target_uscc"), [])
        )
    ]
    if risk_targets:
        return RuleHit("R2", "关联破产传导", True,
                       {"risk_count": len(risk_targets), "stake": stake},
                       delta=0.3 * stake)
    return RuleHit("R2", "关联破产传导", False, {})


def rule3_shell_company(facts: EnterpriseFacts) -> RuleHit:
    now = datetime.now()
    two_years_ago = date(now.year - 2, now.month, now.day)
    legal_rep_changes = sum(
        1 for e in facts.events
        if e.get("event_type") == "legal_rep_change"
        and e.get("occurred_at") and e.get("occurred_at") >= two_years_ago
    )
    addr_changes = sum(
        1 for e in facts.events
        if e.get("event_type") == "address_change"
        and e.get("occurred_at") and e.get("occurred_at") >= two_years_ago
    )
    insured = facts.firm.get("employee_count") or 0
    suspicious = legal_rep_changes >= 3 and addr_changes >= 2 and insured == 0
    return RuleHit("R3", "壳公司识别", suspicious,
                   {"legal_rep_changes": legal_rep_changes,
                    "addr_changes": addr_changes,
                    "insured": insured},
                   veto=suspicious)


def rule4_shareholder_risk(facts: EnterpriseFacts) -> RuleHit:
    shareholder_risks = [
        e for e in facts.events
        if e.get("event_type") in RISK_EVENT_TYPES
        and _as_dict(e.get("raw_json")).get("holder_type") == "shareholder"
    ]
    triggered = False
    total_delta = 0.0
    for e in shareholder_risks:
        ratio = float(_as_dict(e.get("raw_json")).get("stake_ratio", 0))
        if ratio >= 0.20:
            triggered = True
            total_delta += 0.2 * ratio
    return RuleHit("R4", "股东风险传导", triggered,
                   {"shareholder_risks": len(shareholder_risks)},
                   delta=total_delta)


def rule5_equity_freeze(facts: EnterpriseFacts) -> RuleHit:
    freeze_events = [e for e in facts.events if e.get("event_type") == "equity_freeze"]
    has_flag = facts.firm.get("has_equity_freeze")
    if not freeze_events and not has_flag:
        return RuleHit("R5", "股权冻结", False, {})
    total_ratio = sum(
        float(_as_dict(e.get("raw_json")).get("freeze_ratio", 0)) for e in freeze_events
    )
    return RuleHit("R5", "股权冻结", True,
                   {"freeze_count": len(freeze_events), "total_ratio": total_ratio},
                   delta=0.15 * total_ratio)


def rule6_executive_risk(facts: EnterpriseFacts) -> RuleHit:
    veto_signals = {"dishonest_exec", "restricted_consumption_exec",
                    "exit_restriction", "equity_freeze_exec"}
    hits = [e for e in facts.events if e.get("event_type") in veto_signals]
    if hits or facts.firm.get("has_executive_risk"):
        return RuleHit("R6", "个人司法风险", True,
                       {"signals": [e.get("event_type") for e in hits]},
                       veto=True)
    return RuleHit("R6", "个人司法风险", False, {})


def rule7_systemic_risk(facts: EnterpriseFacts) -> RuleHit:
    controller = facts.firm.get("controller_name")
    if not controller:
        return RuleHit("R7", "系统性风险主体", False, {})
    controlled = {
        e.get("target_uscc") for e in facts.equity_edges
        if e.get("holder_uscc") == controller and e.get("target_uscc")
    }
    controlled.discard(facts.firm.get("uscc"))
    risky = {
        uscc for uscc in controlled
        if any(ev.get("event_type") in RISK_EVENT_TYPES
               for ev in facts.related_events.get(uscc, []))
    }
    if controlled:
        ratio = len(risky) / len(controlled)
        return RuleHit("R7", "系统性风险主体", ratio >= 0.5,
                       {"controlled": len(controlled), "risky": len(risky), "ratio": ratio},
                       delta=ratio * 100)
    return RuleHit("R7", "系统性风险主体", False, {})


def rule8_executive_overlap(facts: EnterpriseFacts) -> RuleHit:
    overlaps = []
    seen = set()
    for ex in facts.executives:
        ex_name = ex.get("name")
        ex_uscc = ex.get("uscc")
        for other_uscc, evts in facts.related_events.items():
            if other_uscc == ex_uscc or (ex_name, other_uscc) in seen:
                continue
            if any(ev.get("event_type") in RISK_EVENT_TYPES for ev in evts):
                overlaps.append({"person": ex_name, "risk_uscc": other_uscc})
                seen.add((ex_name, other_uscc))
    return RuleHit("R8", "任职重叠风险", bool(overlaps), {"overlaps": overlaps})


def rule9_control_decay(facts: EnterpriseFacts) -> RuleHit:
    firm = facts.firm
    stake = float(firm.get("controller_stake") or 0)
    is_legal_rep = firm.get("legal_rep") == firm.get("controller_name")
    if stake < 0.5 and not is_legal_rep:
        return RuleHit("R9", "控制力衰减", True,
                       {"stake": stake, "is_legal_rep": is_legal_rep},
                       delta=-0.1)
    return RuleHit("R9", "控制力衰减", False, {})


def rule10_network_density(facts: EnterpriseFacts) -> RuleHit:
    uscc = facts.firm.get("uscc")
    related = {
        e.get("holder_uscc") for e in facts.equity_edges if e.get("target_uscc") == uscc
    } | {
        e.get("target_uscc") for e in facts.equity_edges if e.get("holder_uscc") == uscc
    }
    related.discard(uscc)
    related.discard(None)
    risky_related = {
        u for u in related
        if any(ev.get("event_type") in RISK_EVENT_TYPES
               for ev in facts.related_events.get(u, []))
    }
    if related:
        ratio = len(risky_related) / len(related)
        return RuleHit("R10", "关联网络密度", ratio > 0,
                       {"related": len(related), "risky": len(risky_related),
                        "density": ratio},
                       delta=ratio)
    return RuleHit("R10", "关联网络密度", False, {})


def rule11_guarantee_contagion(facts: EnterpriseFacts) -> RuleHit:
    """担保传导 - 简化实现，担保事件通过 event_type='guarantee' 承载"""
    guarantee_events = [
        e for e in facts.events if e.get("event_type") == "guarantee"
    ]
    bankrupt_debtors = {
        _as_dict(e.get("raw_json")).get("debtor_uscc")
        for e in guarantee_events
        if any(ev.get("event_type") in ("bankruptcy", "judgment_debtor")
               for ev in facts.related_events.get(
                   _as_dict(e.get("raw_json")).get("debtor_uscc"), []))
    }
    if bankrupt_debtors:
        return RuleHit("R11", "资金链断裂", True,
                       {"guarantee_count": len(guarantee_events),
                        "bankrupt_debtors": list(bankrupt_debtors)},
                       veto=True)
    return RuleHit("R11", "资金链断裂", False, {"guarantee_count": len(guarantee_events)})


def rule12_controller_change(facts: EnterpriseFacts) -> RuleHit:
    now = datetime.now()
    one_year_ago = date(now.year - 1, now.month, now.day)
    recent = [
        e for e in facts.events
        if e.get("event_type") == "legal_rep_change"
        and e.get("occurred_at") and e.get("occurred_at") >= one_year_ago
    ]
    if len(recent) >= 2:
        return RuleHit("R12", "实控人变更预警", True,
                       {"recent_changes": len(recent)},
                       delta=0.15)
    return RuleHit("R12", "实控人变更预警", False, {})


# 规则注册表（顺序即执行顺序）
RULES: list[tuple[str, RuleFn]] = [
    ("R1", rule1_direct_risk),
    ("R2", rule2_bankruptcy_contagion),
    ("R3", rule3_shell_company),
    ("R4", rule4_shareholder_risk),
    ("R5", rule5_equity_freeze),
    ("R6", rule6_executive_risk),
    ("R7", rule7_systemic_risk),
    ("R8", rule8_executive_overlap),
    ("R9", rule9_control_decay),
    ("R10", rule10_network_density),
    ("R11", rule11_guarantee_contagion),
    ("R12", rule12_controller_change),
]


# ---------- 推理引擎 ----------

def recommend(grade: str, veto: bool) -> str:
    if veto or grade == "D":
        return "拒绝 (Reject)"
    if grade == "C":
        return "条件准入 (Conditional Accept)"
    if grade == "B":
        return "谨慎准入 (Cautious Accept)"
    return "准入 (Accept)"


class Reasoner:
    """推理引擎 - 纯函数，无外部 IO"""

    def __init__(self, rules: list[tuple[str, RuleFn]] | None = None):
        self.rules = rules or RULES

    def run(self, facts: EnterpriseFacts) -> ReasoningResult:
        all_rules = [fn(facts) for _, fn in self.rules]
        hits = [h for h in all_rules if h.triggered]

        veto = any(h.veto for h in all_rules)

        # 基础评级：优先取 R1 结果，无 R1 时默认 A
        base_grade = "A"
        r1 = next((h for h in all_rules if h.rule_id == "R1"), None)
        if r1 and r1.triggered:
            base_grade = r1.detail.get("grade", base_grade)

        # veto 强制 D
        final_grade = "D" if veto else base_grade

        # 风险分：50 为基线，累加各规则 delta
        total_delta = sum(h.delta for h in hits)
        risk_score = max(0.0, min(100.0, 50 + total_delta * 100))

        return ReasoningResult(
            subject={
                "uscc": facts.firm.get("uscc"),
                "name": facts.firm.get("name"),
                "controller": facts.firm.get("controller_name"),
                "controller_stake": float(facts.firm.get("controller_stake") or 0),
            },
            final_grade=final_grade,
            veto=veto,
            risk_score=round(risk_score, 2),
            hits=hits,
            all_rules=all_rules,
            recommendation=recommend(final_grade, veto),
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )
