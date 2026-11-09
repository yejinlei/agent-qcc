"""把 firm_wide.raw_risk_scan 或 raw_*/risk_scan.json 拆行写入 event_wide。

用法：
    python skills/qcc-super/scripts/ingest_risk_scan.py                    # 处理所有有 raw_risk_scan 的企业
    python skills/qcc-super/scripts/ingest_risk_scan.py 浙江胶友科技有限公司  # 指定企业
    python skills/qcc-super/scripts/ingest_risk_scan.py --replay 浙江胶友科技有限公司  # 从 raw_*/risk_scan.json 重放

设计：
- 对每个企业生成 35 行 event_wide：
  · 一条 summary 行（event_type='risk_scan_summary'），承载 有记录/无记录计数
  · 每条 条目数>0 的风险因子生成一行明细（event_type=风险因子英文映射 / event_subtype=中文名）
- 幂等：先 DELETE 该企业的所有 event_wide 行，再 INSERT
"""
import json
import sys
from pathlib import Path

import duckdb

DB_PATH = "f:/src/agent-qcc/skills/qcc-super/data/qcc.db"
RAW_ROOT = Path("f:/src/agent-qcc/skills/qcc-super/data")

# 中文风险因子 → (event_type 英文, severity)
RISK_FACTOR_MAP = {
    "失信信息":        ("dishonest", "HIGH"),
    "被执行人":        ("judgment_debtor", "HIGH"),
    "限制高消费":      ("high_consumption_restriction", "HIGH"),
    "终本案件":        ("terminated_case", "HIGH"),
    "裁判文书":        ("court_document", "MEDIUM"),
    "立案信息":        ("case_filing", "MEDIUM"),
    "开庭公告":        ("hearing_notice", "LOW"),
    "法院公告":        ("court_notice", "LOW"),
    "送达公告":        ("service_notice", "LOW"),
    "破产重整":        ("bankruptcy_reorganization", "HIGH"),
    "行政处罚":        ("administrative_penalty", "MEDIUM"),
    "经营异常":        ("business_anomaly", "MEDIUM"),
    "税务违法":        ("tax_violation", "HIGH"),
    "股权出质":        ("equity_pledge", "MEDIUM"),
    "动产抵押":        ("mortgage", "LOW"),
    "知识产权出质":    ("ip_pledge", "LOW"),
    "股权冻结":        ("equity_freeze", "HIGH"),
    "司法协助":        ("judicial_assistance", "MEDIUM"),
    "股权冻结":        ("equity_freeze", "HIGH"),
    "清算信息":        ("liquidation", "HIGH"),
    "简易注销":        ("simplified_liquidation", "MEDIUM"),
    "严重违法":        ("serious_violation", "HIGH"),
    "抽查检查":        ("spot_check", "LOW"),
    "招投标":          ("bidding", "LOW"),
    "进出口信用":      ("import_export_credit", "MEDIUM"),
    "海关处罚":        ("customs_penalty", "MEDIUM"),
    "外汇处罚":        ("forex_penalty", "HIGH"),
    "反倾销":          ("anti_dumping", "HIGH"),
    "反补贴":          ("anti_subsidy", "HIGH"),
    "产品召回":        ("product_recall", "HIGH"),
    "商标异议":        ("trademark_objection", "LOW"),
    "专利纠纷":        ("patent_dispute", "MEDIUM"),
    "知识产权出质":    ("ip_pledge", "LOW"),
    "商标质押":        ("trademark_pledge", "LOW"),
    "股权质押":        ("equity_pledge", "MEDIUM"),
}


def extract_risk_scan(conn, name=None):
    """返回 [(uscc, name, risk_scan_dict), ...]"""
    rows = conn.execute(
        "SELECT uscc, name, raw_risk_scan FROM firm_wide WHERE raw_risk_scan IS NOT NULL"
    ).fetchall()
    out = []
    for uscc, nm, raw in rows:
        if name and nm != name:
            continue
        if isinstance(raw, str):
            try:
                d = json.loads(raw)
            except Exception:
                continue
        elif isinstance(raw, dict):
            d = raw
        else:
            continue
        if not d:
            continue
        out.append((uscc, nm, d))
    return out


def replay_from_raw(name):
    """从 data/raw_*/risk_scan.json 读；找到 name 对应的目录。"""
    result = []
    for raw_dir in RAW_ROOT.glob("raw_*"):
        p = raw_dir / "risk_scan.json"
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 匹配：JSON 里的「企业名称」包含 name，或目录名匹配
        if name in (d.get("企业名称", ""), raw_dir.name):
            result.append((d, raw_dir))
    return result


def upsert_events(conn, uscc, name, risk_scan):
    """把 risk_scan 拆行写入 event_wide。返回写入的行数。"""
    conn.execute("DELETE FROM event_wide WHERE uscc = ?", [uscc])

    factors = risk_scan.get("风险因子扫描", [])
    has_cnt = risk_scan.get("有记录因子数", 0)
    no_cnt = risk_scan.get("无记录因子数", 0)
    summary_text = risk_scan.get("摘要", "")

    # 1. summary 行
    conn.execute("""
        INSERT INTO event_wide (uscc, name, event_type, event_subtype, severity,
                                source_tool, source_server, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        uscc, name,
        "risk_scan_summary",
        summary_text or "risk_scan_summary",
        "HIGH" if has_cnt > 0 else "NONE",
        "get_company_risk_scan",
        "qcc-risk",
        json.dumps({"有记录因子数": has_cnt, "无记录因子数": no_cnt}, ensure_ascii=False),
    ])

    # 2. 明细行（只对 条目数>0 的风险因子建）
    n_detail = 0
    for f in factors:
        n = f.get("条目数", 0)
        if not n:
            continue
        factor_cn = f.get("风险因子", "")
        etype, severity = RISK_FACTOR_MAP.get(factor_cn, (factor_cn.lower(), "MEDIUM"))
        conn.execute("""
            INSERT INTO event_wide (uscc, name, event_type, event_subtype, severity,
                                    source_tool, source_server, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            uscc, name,
            etype,
            factor_cn,
            severity,
            f.get("明细工具", "get_company_risk_scan"),
            "qcc-risk",
            json.dumps(f, ensure_ascii=False),
        ])
        n_detail += 1

    conn.commit()
    return 1 + n_detail


def main():
    replay = "--replay" in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith("--")]

    conn = duckdb.connect(DB_PATH)

    if replay:
        if not names:
            print("错误：--replay 需要指定企业名")
            sys.exit(1)
        pairs = replay_from_raw(names[0])
        if not pairs:
            print(f"未找到 raw_*/risk_scan.json 与「{names[0]}」匹配的目录")
            sys.exit(1)
        for d, raw_dir in pairs:
            # 从 firm_wide 取 uscc/name
            name = d.get("企业名称") or names[0]
            row = conn.execute(
                "SELECT uscc, name FROM firm_wide WHERE name = ?", [name]
            ).fetchone()
            if not row:
                print(f"firm_wide 无记录，跳过：{name}")
                continue
            uscc, nm = row
            n = upsert_events(conn, uscc, nm, d)
            print(f"[replay] {nm} ({uscc}) ← {raw_dir}: 写入 {n} 行 event_wide")
    else:
        targets = extract_risk_scan(conn, names[0] if names else None)
        if not targets:
            print("firm_wide 中没有 raw_risk_scan 非空的企业")
            sys.exit(0)
        for uscc, nm, d in targets:
            n = upsert_events(conn, uscc, nm, d)
            factors = d.get("风险因子扫描", [])
            has = sum(1 for f in factors if f.get("条目数", 0) > 0)
            print(f"[ingest] {nm} ({uscc}): 因子 {len(factors)} 项 / 有记录 {has} 项 / 写入 event_wide {n} 行")

    # 打印结果
    print("\n=== event_wide 分布 ===")
    rows = conn.execute("""
        SELECT uscc, event_type, COUNT(*) AS n
        FROM event_wide
        GROUP BY uscc, event_type
        ORDER BY uscc, event_type
    """).fetchall()
    for uscc, etype, n in rows:
        print(f"  {uscc}  {etype:32s} {n}")
    conn.close()


if __name__ == "__main__":
    main()
