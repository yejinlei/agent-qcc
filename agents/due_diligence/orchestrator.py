"""尽调 Agent 编排器

职责：
  1. 从 DuckDB 加载企业数据（通过 qcc-super 的 schema）
  2. 交给 Reasoner 执行 12 条推理规则
  3. 渲染 markdown 报告 / 返回结构化 JSON
  4. 可选：写回 event_wide 作为审计记录（event_type='reasoning'）
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .reasoner import EnterpriseFacts, Reasoner, ReasoningResult


# skill 路径（qcc-super）
# orchestrator.py 位于 f:\src\agent-qcc\agents\due_diligence\
# parents[0] = due_diligence, parents[1] = agents, parents[2] = 项目根
SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "qcc-super"
DB_PATH = SKILL_ROOT / "data" / "qcc.db"


def _connect():
    try:
        import duckdb
    except ImportError as e:
        raise RuntimeError(
            "duckdb not installed. Run: python skills/qcc-super/scripts/bootstrap.py"
        ) from e
    if not DB_PATH.exists():
        raise RuntimeError(f"DB not found at {DB_PATH}. Run bootstrap.py first.")
    return duckdb.connect(str(DB_PATH), read_only=True)


def _rows(conn, sql: str, params: list | None = None) -> list[dict]:
    r = conn.execute(sql, params or [])
    cols = [d[0] for d in r.description]
    return [dict(zip(cols, row)) for row in r.fetchall()]


class DueDiligenceAgent:
    """尽调 Agent - 编排 数据加载 → 推理 → 报告"""

    def __init__(self, db_path: Path | str | None = None, reasoner: Reasoner | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.reasoner = reasoner or Reasoner()

    def load_facts(self, uscc: str) -> EnterpriseFacts:
        conn = _connect()
        try:
            firms = _rows(conn, "SELECT * FROM firm_wide WHERE uscc = ?", [uscc])
            if not firms:
                raise ValueError(f"firm_wide 无 uscc={uscc} 数据")
            firm = firms[0]

            events = _rows(conn, "SELECT * FROM event_wide WHERE uscc = ?", [uscc])
            edges = _rows(
                conn,
                "SELECT * FROM equity_edge WHERE holder_uscc = ? OR target_uscc = ?",
                [uscc, uscc],
            )
            executives = _rows(conn, "SELECT * FROM executive WHERE uscc = ?", [uscc])

            # 构建关联企业事件索引
            related = set()
            for e in edges:
                if e.get("holder_uscc") and e.get("holder_uscc") != uscc:
                    related.add(e["holder_uscc"])
                if e.get("target_uscc") and e.get("target_uscc") != uscc:
                    related.add(e["target_uscc"])

            related_events: dict[str, list[dict]] = {}
            if related:
                placeholders = ",".join("?" * len(related))
                all_evts = _rows(
                    conn,
                    f"SELECT * FROM event_wide WHERE uscc IN ({placeholders})",
                    list(related),
                )
                for ev in all_evts:
                    related_events.setdefault(ev["uscc"], []).append(ev)

            return EnterpriseFacts(
                firm=firm,
                events=events,
                equity_edges=edges,
                executives=executives,
                related_events=related_events,
            )
        finally:
            conn.close()

    def reason(self, uscc: str) -> ReasoningResult:
        facts = self.load_facts(uscc)
        return self.reasoner.run(facts)

    def reason_from_facts(self, facts: EnterpriseFacts) -> ReasoningResult:
        """跳过 DB 加载，直接从内存事实推理（便于测试和 LangGraph 集成）"""
        return self.reasoner.run(facts)

    def run(self, uscc: str, fmt: str = "markdown") -> str | dict:
        result = self.reason(uscc)
        if fmt == "json":
            return result.to_dict()
        return self.to_markdown(result)

    @staticmethod
    def to_markdown(result: ReasoningResult) -> str:
        lines = [
            f"# {result.subject.get('name', result.subject.get('uscc', '未知企业'))} 尽调报告",
            "",
            f"- **USCC**: {result.subject.get('uscc')}",
            f"- **综合评级**: {result.final_grade}",
            f"- **风险分**: {result.risk_score}/100",
            f"- **一票否决**: {'是' if result.veto else '否'}",
            f"- **建议**: {result.recommendation}",
            f"- **推理时间**: {result.generated_at}",
            "",
            "## 命中的推理规则",
        ]
        if result.hits:
            for h in result.hits:
                veto_tag = " [VETO]" if h.veto else ""
                delta_tag = f" (delta={h.delta:+.3f})" if h.delta else ""
                lines.append(f"- **{h.rule_id}** {h.name}{veto_tag}{delta_tag}")
                for k, v in h.detail.items():
                    lines.append(f"  - {k}: {v}")
        else:
            lines.append("- (无规则命中，评级 A)")

        lines.extend(["", "## 全部规则检查"])
        for r in result.all_rules:
            mark = "✓" if r.triggered else "·"
            lines.append(f"- {mark} {r.rule_id} {r.name}")

        lines.extend([
            "",
            "---",
            "本报告由 12 条推理规则自动生成，供尽调决策参考。",
            "如需本体论图推理（SHACL + RML），可通过 Semantica 消费 qcc-super 数据层。",
        ])
        return "\n".join(lines)


# ---------- CLI ----------

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="尽调 Agent CLI")
    p.add_argument("--uscc", required=True)
    p.add_argument("--db", help="DuckDB 路径，默认 skills/qcc-super/data/qcc.db")
    p.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = p.parse_args()

    agent = DueDiligenceAgent(db_path=args.db)
    try:
        out = agent.run(args.uscc, fmt=args.format)
    except (ValueError, RuntimeError) as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    if isinstance(out, dict):
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        print(out)


if __name__ == "__main__":
    main()
