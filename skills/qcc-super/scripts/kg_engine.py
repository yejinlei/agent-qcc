#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
qcc-super Semantica 集成封装。

职责：
  · 从 DuckDB 抽取 firm_wide / equity_edge / executive / event_wide
  · 映射为 Semantica ContextGraph 节点/边
  · 加载 SHACL shapes 做约束校验
  · 输出 RDF (Turtle / JSON-LD / N-Triples) 供外部 Neo4j / SPARQL 端点消费
  · 提供纯图推理 API（无 Semantica 时也通过 rdflib + pyshacl 兜底）

依赖（可选，任一缺失自动降级）:
  · semantica: 完整功能
  · rdflib + pyshacl: 基础 RDF + SHACL 校验
"""
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
SKILL_ROOT = HERE.parent
DB_PATH = SKILL_ROOT / "data" / "qcc.db"
ONTOLOGY_TTL = SKILL_ROOT / "references" / "ontology" / "turtle" / "qcc_ontology.ttl"
SHAPES_TTL = SKILL_ROOT / "references" / "shacl" / "qcc_shapes.ttl"

NAMESPACE = "http://qcc.internal/ontology#"
BASE_URIS = {
    "enterprise": "http://qcc.internal/entity/enterprise",
    "person": "http://qcc.internal/entity/person",
    "event": "http://qcc.internal/entity/event",
}

# ---------- 事件类型映射（to_rdflib_graph / infer_risk 共用） ----------

# event_wide.event_type → 本体事件类型键（含中英兼容别名）
EVENT_TYPE_MAP = {
    "dishonest": "dishonest",
    "judgment_debtor": "judgment_debtor",
    "restricted_consumption": "restricted_consumption",
    "equity_freeze": "equity_freeze",
    "business_exception": "business_exception",
    "penalty": "admin_penalty",
    "admin_penalty": "admin_penalty",
    "bankruptcy": "bankruptcy",
    "pre_litigation": "pre_litigation",
    "tax_arrears": "tax_arrears",
    "equity_pledge": "equity_pledge",
    "litigation": "litigation",
    "property_auction": "property_auction",
    # 变更类事件（非风险，用于 R12 频繁变动检测）
    "legal_rep_change": "legal_rep_change",
    "address_change": "address_change",
    "business_change": "business_change",
}
# 事件类型键 → 本体类名（qcc:XXX）
EVENT_KEY_TO_CLASS = {
    "dishonest": "DishonestSignal",
    "restricted_consumption": "RestrictedConsumption",
    "judgment_debtor": "JudgmentDebtor",
    "equity_freeze": "EquityFreeze",
    "business_exception": "BusinessException",
    "admin_penalty": "AdminPenalty",
    "bankruptcy": "Bankruptcy",
    "pre_litigation": "PreLitigation",
    "tax_arrears": "TaxArrears",
    "equity_pledge": "EquityPledge",
    "litigation": "Litigation",
    "property_auction": "PropertyAuction",
}
# 一票否决类事件（R1 命中即 conform=false）
VETO_TYPES = {"dishonest", "judgment_debtor", "restricted_consumption", "equity_freeze"}
# firm_wide.has_* 列名 → 事件类型键（用于判定股东风险传导）
HOLDER_HAS_COLS = {
    "has_dishonest": "dishonest",
    "has_restricted_consumption": "restricted_consumption",
    "has_judgment_debtor": "judgment_debtor",
    "has_equity_freeze": "equity_freeze",
    "has_business_exception": "business_exception",
    "has_admin_penalty": "admin_penalty",
}


def _event_type_key(ev_type) -> str:
    """event_type 归一化到本体事件类型键（不认识的归 'other'）。"""
    t = (ev_type or "").strip().lower()
    return EVENT_TYPE_MAP.get(t, "other")


# ---------- Semantica 可用性 ----------

# semantica 0.6.8+ 已从"传统 RDF 建模库"演进为完整知识工程框架，
# API 结构完全重构：不再有 semantica.modeling.graph / semantica.semantics.mapping。
# 下面这些模块是新架构的核心能力，探测时逐一验证可用。
_SEMANTICA_PROBE_MODULES = [
    ("kg",                 "semantica.kg",                              "知识图谱构建"),
    ("ontology",           "semantica.ontology",                        "本体定义/验证"),
    ("reasoning",          "semantica.reasoning",                       "推理引擎"),
    ("ingest",             "semantica.ingest",                          "数据摄入"),
    ("parse",              "semantica.parse",                           "文档解析"),
    ("triplet_store",      "semantica.triplet_store",                   "三元组存储"),
    ("graph_store",        "semantica.graph_store",                     "图数据库存储"),
    ("visualization",      "semantica.visualization",                   "可视化"),
    ("context",            "semantica.context",                         "决策上下文"),
    ("conflicts",          "semantica.conflicts",                       "冲突检测"),
    ("deduplication",      "semantica.deduplication",                   "去重/实体合并"),
    ("semantic_extract",   "semantica.semantic_extract",                "语义抽取"),
]

# 旧 API（0.3-0.5）—— 兼容检测，标记当前版本是否还支持
_SEMANTICA_LEGACY_MODULES = [
    ("semantica.modeling.graph",   "Graph（旧 API，0.3-0.5）"),
    ("semantica.semantics.mapping","Mapping（旧 API，0.3-0.5）"),
]


def semantica_available() -> bool:
    """semantica 是否已安装。仅检查顶层可导入，不做深度探测。"""
    try:
        import semantica  # noqa: F401
        return True
    except ImportError:
        return False


def semantica_probe() -> dict:
    """真正的 semantica 探测：版本 + 模块可用性 + 旧 API 兼容状态。

    返回:
        {
          "installed": bool,           # 顶层是否 import 成功
          "version": "0.6.8",          # 版本号
          "location": "/path/to/...",  # site-packages 路径
          "modules": [                 # 新架构核心模块探测
              {"name": "kg", "path": "semantica.kg", "label": "知识图谱构建", "available": True},
              ...
          ],
          "legacy_modules": [          # 旧 API 兼容探测
              {"path": "semantica.modeling.graph", "label": "Graph（旧 API，0.3-0.5）", "available": False},
              ...
          ],
          "modern": bool,              # 是新架构 (0.6+) 还是旧架构
          "summary": str,              # 一句话结论
        }

    探测原则：
      - 不实例化任何对象、不启动服务，只做 import + 目录存在性判断
      - 单个模块导入失败不影响其他模块探测
      - 所有异常都捕获，返回结构化结果供上层决策
    """
    result = {
        "installed": False,
        "version": None,
        "location": None,
        "modules": [],
        "legacy_modules": [],
        "modern": False,
        "summary": "",
    }

    try:
        import semantica as _sem
        result["installed"] = True
        result["version"] = getattr(_sem, "__version__", "unknown")
        result["location"] = getattr(_sem, "__file__", None)
    except Exception as e:
        result["summary"] = f"semantica 未安装：{type(e).__name__}"
        return result

    # 判断新旧架构：有 semantica.kg 是新架构，只有 semantica.modeling 是旧架构
    try:
        import semantica.kg  # noqa: F401
        result["modern"] = True
    except ImportError:
        pass

    # 探测新架构模块
    for short, path, label in _SEMANTICA_PROBE_MODULES:
        try:
            __import__(path)
            ok = True
            err = None
        except Exception as e:
            ok = False
            err = f"{type(e).__name__}: {str(e)[:120]}"
        result["modules"].append({
            "name": short,
            "path": path,
            "label": label,
            "available": ok,
            "error": err,
        })

    # 探测旧 API（仅用于兼容检查）
    for path, label in _SEMANTICA_LEGACY_MODULES:
        try:
            __import__(path)
            ok = True
        except Exception:
            ok = False
        result["legacy_modules"].append({
            "path": path,
            "label": label,
            "available": ok,
        })

    avail_n = sum(1 for m in result["modules"] if m["available"])
    total_n = len(result["modules"])
    arch = "新架构 (0.6+)" if result["modern"] else "旧架构 (<0.6)"
    result["summary"] = (
        f"semantica {result['version']} · {arch} · "
        f"核心模块 {avail_n}/{total_n} 可用"
    )
    return result


def rdflib_available() -> bool:
    try:
        import rdflib  # noqa: F401
        return True
    except ImportError:
        return False


def owlrl_available() -> bool:
    """owlrl（owl-r2-ml 的现代实现）是否可用。缺失时 /kg-infer 自动降级。"""
    try:
        import owlrl  # noqa: F401
        return True
    except ImportError:
        return False


def load_ontology():
    """返回本体 ttl 的 Path（供 owlrl 闭包作为 ont_graph）。文件缺失时返回 None。

    owlrl.DeductiveClosure.expand 只收 (graph, destination=None)，没有单独的
    ont_graph 参数——因此把本体 ttl 解析进同一张图。传 Path 让 rdflib 直接读文件，
    避免序列化后的字符串 unicode 转义导致 parse 失败。
    """
    if ONTOLOGY_TTL.exists():
        return ONTOLOGY_TTL
    return None


def owlrl_closure(data_graph, ont_graph=None):
    """用 owlrl OWLRL_Semantics 做闭世界推理（子类/对称/逆/传递/disjointWith）。

    返回：(闭包后的图, 新增三元组数, 数据基线三元组数)；owlrl 不可用时原样返回。
    注意：owlrl 会原地修改，所以先复制 data_graph，再用 union 合入本体图，
    避免 serialize→parse 时非 ASCII 字符（如中文 label）导致的编码陷阱。
    """
    from rdflib import Graph as RdfGraph
    if not owlrl_available() or not data_graph:
        return data_graph, 0, 0
    import owlrl
    G = RdfGraph()
    for t in data_graph:
        G.add(t)
    data_triples = len(G)
    if ont_graph:
        ont_path = ont_graph if hasattr(ont_graph, "exists") else Path(str(ont_graph))
        if ont_path.exists():
            OntG = RdfGraph()
            OntG.parse(str(ont_path), format="turtle")
            G += OntG
    baseline = len(G)  # 数据 + 本体作为基线，本体本身不计入"新增"
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(G)
    return G, len(G) - baseline, data_triples


# ---------- 从 DuckDB 抽数据 ----------

def _connect():
    import duckdb
    if not DB_PATH.exists():
        raise RuntimeError(f"DuckDB not initialized: {DB_PATH}")
    return duckdb.connect(str(DB_PATH), read_only=True)


def _rows_to_dicts(cur) -> list:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _resolve_target(target: str) -> dict:
    conn = _connect()
    try:
        t = target.strip()
        if len(t) == 18 and all(c.isalnum() for c in t):
            row = conn.execute("SELECT uscc, name FROM firm_wide WHERE uscc = ? LIMIT 1", [t]).fetchone()
            if row:
                return {"uscc": row[0], "name": row[1]}
        row = conn.execute("SELECT uscc, name FROM firm_wide WHERE name = ? LIMIT 1", [t]).fetchone()
        if row:
            return {"uscc": row[0], "name": row[1]}
        row = conn.execute("SELECT uscc, name FROM firm_wide WHERE name LIKE ? LIMIT 1", [f"%{t}%"]).fetchone()
        if row:
            return {"uscc": row[0], "name": row[1]}
        return None
    finally:
        conn.close()


def fetch_enterprise_graph(target: str, depth: int = 1) -> dict:
    """从 DuckDB 抽取一个企业的完整图数据。"""
    hit = _resolve_target(target)
    if not hit:
        return {"found": False, "target": target}
    uscc = hit["uscc"]
    conn = _connect()
    try:
        firm = _rows_to_dicts(
            conn.execute("SELECT * FROM firm_wide WHERE uscc = ?", [uscc])
        )[0]
        shareholders = _rows_to_dicts(
            conn.execute(
                "SELECT * FROM equity_edge WHERE target_uscc = ?", [uscc]
            )
        )
        investments = _rows_to_dicts(
            conn.execute(
                "SELECT * FROM equity_edge WHERE holder_uscc = ?", [uscc]
            )
        )
        executives = _rows_to_dicts(
            conn.execute("SELECT * FROM executive WHERE uscc = ?", [uscc])
        )
        events = _rows_to_dicts(
            conn.execute("SELECT * FROM event_wide WHERE uscc = ?", [uscc])
        )
        # 股东各自的风险布尔列（firm_wide.has_*）——供 R4 判定风险传导
        holder_usccs = [s.get("holder_uscc") for s in shareholders if s.get("holder_uscc")]
        holder_risks = {}
        if holder_usccs:
            placeholders = ",".join("?" * len(holder_usccs))
            rows = _rows_to_dicts(conn.execute(
                f"SELECT uscc, name, has_dishonest, has_restricted_consumption, has_judgment_debtor, "
                f"has_equity_freeze, has_business_exception, has_admin_penalty FROM firm_wide WHERE uscc IN ({placeholders})",
                holder_usccs
            ))
            for r in rows:
                holder_risks[r["uscc"]] = {
                    "name": r.get("name"),
                    "has_dishonest": r.get("has_dishonest"),
                    "has_restricted_consumption": r.get("has_restricted_consumption"),
                    "has_judgment_debtor": r.get("has_judgment_debtor"),
                    "has_equity_freeze": r.get("has_equity_freeze"),
                    "has_business_exception": r.get("has_business_exception"),
                    "has_admin_penalty": r.get("has_admin_penalty"),
                }
        # depth > 1 时向上/向下穿透股东
        if depth > 1:
            upstream = _rows_to_dicts(
                conn.execute(
                    "SELECT * FROM equity_edge WHERE holder_uscc IN (SELECT uscc FROM firm_wide WHERE uscc = ?)", [uscc]
                )
            )
            return {
                "found": True,
                "target": target,
                "uscc": uscc,
                "firm": firm,
                "shareholders": shareholders,
                "external_investments": investments,
                "executives": executives,
                "events": events,
                "holder_risks": holder_risks,
                "depth": depth,
            }
        return {
            "found": True,
            "target": target,
            "uscc": uscc,
            "firm": firm,
            "shareholders": shareholders,
            "external_investments": investments,
            "executives": executives,
            "events": events,
            "holder_risks": holder_risks,
            "depth": depth,
        }
    finally:
        conn.close()


# ---------- 图数据 → RDF 三元组 ----------

def to_rdflib_graph(graph_data: dict):
    """把 fetch_enterprise_graph 输出转成 rdflib.Graph。"""
    if not rdflib_available():
        raise RuntimeError("rdflib not installed. Run: uv add rdflib pyshacl")
    from rdflib import Graph, URIRef, Literal, BNode
    from rdflib.namespace import RDF, XSD
    from decimal import Decimal

    def dec(v) -> Literal:
        """构造合法的 xsd:decimal 字面量。

        必须用 Decimal 而非 float：Literal(float(x), datatype=XSD.decimal) 的值仍是
        Python float，属于 ill-typed 字面量，pyshacl 的 sh:datatype 检查会判违规。
        """
        return Literal(Decimal(str(float(v))), datatype=XSD.decimal)

    if not graph_data.get("found"):
        raise RuntimeError(f"Target not found: {graph_data.get('target')}")

    G = Graph()
    G.bind("qcc", NAMESPACE)
    G.bind("owl", "http://www.w3.org/2002/07/owl#")

    def qcc(name: str) -> URIRef:
        return URIRef(NAMESPACE + name)

    firm = graph_data["firm"]
    uscc = firm["uscc"]
    ent_uri = URIRef(f"{BASE_URIS['enterprise']}/{uscc}")
    G.add((ent_uri, RDF.type, qcc("Enterprise")))
    G.add((ent_uri, qcc("hasUSCC"), Literal(firm.get("uscc") or "")))
    G.add((ent_uri, qcc("hasName"), Literal(firm.get("name") or "")))
    G.add((ent_uri, qcc("hasLegalRep"), Literal(firm.get("legal_rep") or "")))
    if firm.get("reg_capital") is not None:
        G.add((ent_uri, qcc("hasRegisteredCapital"), dec(firm["reg_capital"])))
    if firm.get("established_at"):
        G.add((ent_uri, qcc("hasEstablishedAt"), Literal(str(firm["established_at"]))))
    if firm.get("business_status"):
        G.add((ent_uri, qcc("hasBusinessStatus"), Literal(firm["business_status"])))
    if firm.get("business_scope"):
        G.add((ent_uri, qcc("hasBusinessScope"), Literal(firm["business_scope"])))
    if firm.get("controller_name"):
        ctrl_uri = URIRef(f"{BASE_URIS['person']}/{firm['controller_name']}")
        G.add((ctrl_uri, RDF.type, qcc("Controller")))
        G.add((ctrl_uri, qcc("hasName"), Literal(firm["controller_name"])))
        G.add((ent_uri, qcc("hasController"), ctrl_uri))
        G.add((ctrl_uri, qcc("controls"), ent_uri))
        if firm.get("controller_stake") is not None:
            G.add((ctrl_uri, qcc("hasStakeRatio"), dec(firm["controller_stake"])))

    # 股东
    for sh in graph_data.get("shareholders", []):
        holder = sh.get("holder_name") or sh.get("holder_uscc")
        if not holder:
            continue
        # 企业股东归 enterprise 命名空间（无 USCC 时用名称），自然人股东归 person
        ns = "person" if str(sh.get("holder_type") or "").lower() == "person" else "enterprise"
        holder_uri = URIRef(f"{BASE_URIS[ns]}/{sh['holder_uscc'] or holder}")
        G.add((holder_uri, RDF.type, qcc("Shareholder")))
        G.add((holder_uri, qcc("hasName"), Literal(holder)))
        G.add((ent_uri, qcc("hasShareholder"), holder_uri))
        G.add((holder_uri, qcc("isShareholderOf"), ent_uri))
        if sh.get("stake_ratio") is not None:
            G.add((holder_uri, qcc("hasStakeRatio"), dec(sh["stake_ratio"])))

    # 董监高
    for ex in graph_data.get("executives", []):
        name = ex.get("name")
        if not name:
            continue
        ex_uri = URIRef(f"{BASE_URIS['person']}/{name}")
        G.add((ex_uri, RDF.type, qcc("Executive")))
        G.add((ex_uri, qcc("hasName"), Literal(name)))
        G.add((ex_uri, qcc("hasRole"), Literal(ex.get("role") or "高管")))
        G.add((ent_uri, qcc("hasExecutive"), ex_uri))
        G.add((ex_uri, qcc("worksAt"), ent_uri))

    # 风险事件（类型映射统一走 EVENT_TYPE_MAP，见文件下方）
    for ev in graph_data.get("events", []):
        ev_uri = URIRef(
            f"{BASE_URIS['event']}/{uscc}/{ev.get('id') or hash(json.dumps(ev, default=str))}"
        )
        G.add((ev_uri, RDF.type, qcc("Event")))
        key = _event_type_key(ev.get("event_type"))
        G.add((ev_uri, RDF.type, qcc(EVENT_KEY_TO_CLASS.get(key, "RiskSignal"))))
        G.add((ent_uri, qcc("hasEvent"), ev_uri))
        if ev.get("occurred_at"):
            G.add((ev_uri, qcc("hasOccurredAt"), Literal(str(ev["occurred_at"]))))
        if ev.get("amount") is not None:
            G.add((ev_uri, qcc("hasAmount"), dec(ev["amount"])))
        if ev.get("severity"):
            G.add((ev_uri, qcc("hasSeverity"), Literal(ev["severity"])))

    # 闭世界：风险扫描已执行且无一票否决事件 → 写 NoVetoSignal / RiskScanPerformed
    scan_txt = firm.get("raw_risk_scan")
    if scan_txt and "无记录" in str(scan_txt):
        G.add((ent_uri, RDF.type, qcc("RiskScanPerformed")))
        if not any(_event_type_key(e.get("event_type")) in VETO_TYPES for e in graph_data.get("events", [])):
            G.add((ent_uri, RDF.type, qcc("NoVetoSignal")))

    return G


# ---------- SHACL 校验 ----------

def shacl_validate(graph_data: dict) -> dict:
    """用 pyshacl 校验图数据，返回 violations 列表。"""
    if not rdflib_available():
        return {"ok": False, "error": "rdflib not installed"}
    try:
        import pyshacl
    except ImportError:
        return {"ok": False, "error": "pyshacl not installed. Run: uv add pyshacl"}

    G = to_rdflib_graph(graph_data)
    from rdflib import Graph as RdfGraph

    data_graph = RdfGraph()
    for t in G:
        data_graph.add(t)

    shapes_graph = RdfGraph()
    shapes_graph.parse(str(SHAPES_TTL), format="turtle")

    try:
        conform, results_graph, results_text = pyshacl.validate(
            data_graph=data_graph,
            shacl_graph=shapes_graph,
            ont_graph=shapes_graph,
            inference="rdfs",
            meta_shacl=True,
            abort_on_first=False,
        )
        violations = []
        if not conform:
            try:
                from rdflib.namespace import SH
                for row in results_graph.triples((None, SH.resultMessage, None)):
                    violations.append(str(row[2]))
                if not violations:
                    for row in results_graph.triples((None, SH.severity, None)):
                        if "Violation" in str(row[2]):
                            violations.append(str(row[2]))
            except Exception:
                pass
            if not violations and results_text:
                for line in (results_text or "").splitlines():
                    if "Violation" in line or "ERROR" in line:
                        violations.append(line.strip())
            if not violations:
                violations.append((results_text or "unknown violation")[:1500])
        return {
            "ok": True, "conform": bool(conform),
            "violations": violations,
            "report_text": (results_text or "")[:3000],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------- 推理 ----------

def _holder_has_risk(holder_uscc, holder_risks, veto_only=True) -> list:
    """取出股东的风险标记列表（事件类型键）。veto_only 只看一票否决类。"""
    if not holder_uscc or not holder_risks:
        return []
    row = holder_risks.get(holder_uscc)
    if not row:
        return []
    hits = []
    for col, key in HOLDER_HAS_COLS.items():
        if veto_only and key not in VETO_TYPES:
            continue
        if row.get(col) is True:
            hits.append(key)
    return hits


def infer_risk(graph_data: dict, use_owlrl: bool = True) -> dict:
    """图推理：闭世界 owlrl 闭包 + 6 条核心规则（R1/R4/R7/R10/R12）。

    返回: {conform, findings:[{rule, severity, detail}], owlrl:{...}, count}
    """
    findings = []
    firm = graph_data.get("firm") or {}
    events = graph_data.get("events", [])
    shareholders = graph_data.get("shareholders", [])
    holder_risks = graph_data.get("holder_risks") or {}

    # ---------- owlrl 闭世界闭包（子类/对称/逆/disjointWith） ----------
    owlrl_meta = {"available": False, "new_triples": 0}
    if use_owlrl and rdflib_available():
        try:
            G0 = to_rdflib_graph(graph_data)
            Gc, new_n, data_triples = owlrl_closure(G0, ont_graph=load_ontology())
            owlrl_meta = {"available": True,
                          "data_triples": data_triples,
                          "new_triples": new_n,
                          "closed_triples": len(Gc)}
        except Exception as e:
            owlrl_meta = {"available": True, "error": f"{type(e).__name__}: {e}"}

    # ---------- R1: 一票否决（失信/被执行/限高/股权冻结） ----------
    veto_hits = [e for e in events if _event_type_key(e.get("event_type")) in VETO_TYPES]
    if veto_hits:
        findings.append({
            "rule": "R1-veto",
            "severity": "HIGH",
            "detail": f"命中一票否决风险 {len(veto_hits)} 条: "
                      f"{[_event_type_key(e.get('event_type')) for e in veto_hits[:5]]}",
            "conform": False,
        })
    else:
        # 无 veto 事件 + 风险扫描已执行 → 写入 NoVetoSignal（区分"未发现"与"已证明无"）
        raw_scan = firm.get("raw_risk_scan")
        scan_done = bool(raw_scan) and "无记录" in str(raw_scan)
        if scan_done:
            findings.append({
                "rule": "R1-cleared",
                "severity": "INFO",
                "detail": "风险扫描已执行且无一票否决事件，写入 qcc:NoVetoSignal（闭世界已证明无）",
                "conform": True,
            })
        elif not events:
            findings.append({
                "rule": "R1-data-gap",
                "severity": "LOW",
                "detail": "无事件记录且未见风险扫描摘要，R1 未判定（数据缺口）",
                "conform": True,
            })

    # ---------- R4: 股东风险传导（>=20% 股东有一票否决类风险） ----------
    for sh in shareholders:
        ratio = float(sh.get("stake_ratio") or 0)
        if ratio < 0.2:
            continue
        name = sh.get("holder_name")
        hu = sh.get("holder_uscc")
        if not hu:
            findings.append({
                "rule": "R4-data-gap",
                "severity": "MEDIUM",
                "detail": f"股东 {name} 持股 {ratio*100:.1f}% ≥ 20%，但未采集其 USCC，无法跨企业检查",
                "conform": True,
            })
            continue
        if hu not in holder_risks:
            findings.append({
                "rule": "R4-data-gap",
                "severity": "MEDIUM",
                "detail": f"股东 {name} (USCC {hu}) 持股 {ratio*100:.1f}%，不在 firm_wide 中，无法检查其风险",
                "conform": True,
            })
            continue
        # 股东已入库：真实判定
        hits = _holder_has_risk(hu, holder_risks, veto_only=True)
        if hits:
            findings.append({
                "rule": "R4-transmit",
                "severity": "HIGH",
                "detail": f"股东 {name} 持股 {ratio*100:.1f}% 且有一票否决类风险: {hits}",
                "conform": False,
            })
        else:
            findings.append({
                "rule": "R4-cleared",
                "severity": "INFO",
                "detail": f"股东 {name} 持股 {ratio*100:.1f}%，已入库且无风险标记",
                "conform": True,
            })

    # ---------- R7: 系统性风险主体（实控人控制多家公司有失信） ----------
    if firm.get("controller_name"):
        findings.append({
            "rule": "R7-systemic-candidate",
            "severity": "MEDIUM",
            "detail": f"实控人 {firm['controller_name']}，建议跨企业网络检查",
            "conform": True,
        })
    else:
        findings.append({
            "rule": "R7-data-gap",
            "severity": "LOW",
            "detail": "实控人/UBO 未采集，R7 无法判定（需 get_actual_controller / get_beneficial_owners）",
            "conform": True,
        })

    # ---------- R10: 关联网络密度（企业风险事件总数） ----------
    if len(events) >= 5:
        findings.append({
            "rule": "R10-network-density",
            "severity": "HIGH",
            "detail": f"企业风险事件 {len(events)} 条，关联网络风险较高",
            "conform": False,
        })

    # ---------- R12: 法代频繁变更（历史变更检查） ----------
    lr_changes = [e for e in events if _event_type_key(e.get("event_type")) in
                  ("legal_rep_change", "address_change")]
    if len(lr_changes) >= 3:
        findings.append({
            "rule": "R12-churn",
            "severity": "MEDIUM",
            "detail": f"法代/地址变更 {len(lr_changes)} 次，法人频繁变动信号",
            "conform": True,
        })

    return {
        "ok": True,
        "conform": all(f.get("conform", True) for f in findings),
        "findings": findings,
        "count": len(findings),
        "owlrl": owlrl_meta,
    }


# ---------- 关联方识别（跨企业合并图） ----------

def find_related_parties(target: str, use_owlrl: bool = True, max_hops: int = 2) -> dict:
    """跨企业合并图 + owlrl 闭包 + 4 类关联方查询。

    输入 target：中心企业 USCC/名称
    返回：
      {
        center_uscc, center_name,
        networks: [其他企业 uscc 列表],   # 合并到图里做穿透
        triples:  {base, closed, added},
        direct:    [...],  # 1 跳关联方（股东/高管/法人/实控人）
        multi_hop: [...],  # 2 跳关联方
        shared_persons: [...],  # 跨企业共享人员（交叉任职）
        guarantee_loops: [...], # 担保环
      }
    """
    if not rdflib_available():
        return {"ok": False, "error": "rdflib not installed"}

    hit = _resolve_target(target)
    if not hit:
        return {"ok": False, "error": f"target not found: {target}"}
    center_uscc = hit["uscc"]

    from rdflib import Graph as RdfGraph, URIRef, Literal
    from rdflib.namespace import RDF

    # 1) 抓中心企业，得到股东 uscc 列表
    center_data = fetch_enterprise_graph(center_uscc)
    G = RdfGraph()
    for t in to_rdflib_graph(center_data):
        G.add(t)

    holder_usccs = []
    for sh in center_data.get("shareholders", []):
        hu = sh.get("holder_uscc")
        if hu and hu not in holder_usccs:
            holder_usccs.append(hu)

    # 2) 合并股东企业各自图
    merged_usccs = [center_uscc]
    for hu in holder_usccs:
        try:
            hd = fetch_enterprise_graph(hu)
            if hd.get("found"):
                for t in to_rdflib_graph(hd):
                    G.add(t)
                merged_usccs.append(hd["uscc"])
        except Exception:
            pass

    base_triples = len(G)

    # 3) owlrl 闭包
    owlrl_meta = {"available": False, "new_triples": 0}
    if use_owlrl and owlrl_available():
        try:
            G, new_n, _ = owlrl_closure(G, ont_graph=load_ontology())
            owlrl_meta = {"available": True, "new_triples": new_n, "closed_triples": len(G)}
        except Exception as e:
            owlrl_meta = {"available": True, "error": f"{type(e).__name__}: {e}"}

    def _short(uri):
        s = str(uri)
        if s.startswith("http://qcc.internal/entity/enterprise/"):
            return "E:" + s.replace("http://qcc.internal/entity/enterprise/", "")
        if s.startswith("http://qcc.internal/entity/person/"):
            return "P:" + s.replace("http://qcc.internal/entity/person/", "")
        return s

    def _get_name(uri, uri_map):
        return uri_map.get(str(uri), _short(uri))

    # 4) 构建 name 映射（URI → hasName 字面量）
    uri_map = {}
    for s, p, o in G:
        if "hasName" in str(p):
            uri_map[str(s)] = str(o)

    # 4b) 反向映射：name 字面量 → 所有 URI（用于跨 hasLegalRep 字面量 vs hasExecutive URI 的交叉匹配）
    name_to_uris = {}
    for uri, name in uri_map.items():
        name_to_uris.setdefault(name, set()).add(uri)

    def _is_entity_uri(uri):
        s = str(uri)
        return s.startswith("http://qcc.internal/entity/enterprise/") or s.startswith("http://qcc.internal/entity/person/")

    def _name_of(uri):
        """返回 URI 对应的中文名（有则用，无则用短 URI）。"""
        return uri_map.get(str(uri), _short(uri))

    # 5) 1 跳关联方（仅保留 URI 型对象）
    center_uri = URIRef(f"{BASE_URIS['enterprise']}/{center_uscc}")
    q1 = """
    PREFIX qcc: <http://qcc.internal/ontology#>
    SELECT DISTINCT ?rel ?p WHERE {
      ?e qcc:hasUSCC "%s" .
      { ?e ?p ?rel } UNION { ?rel ?p ?e }
      FILTER (?rel != ?e)
      FILTER (STR(?rel) != "%s")
      FILTER (STR(?p) != STR(rdf:type))
    }
    """ % (center_uscc, center_uscc)
    direct = []
    seen = set()
    for row in G.query(q1):
        if not _is_entity_uri(row[0]):
            continue
        rel = str(row[0])
        if rel in seen: continue
        seen.add(rel)
        prop = str(row[1]).split("#")[-1] if "#" in str(row[1]) else str(row[1])
        direct.append({"uri": _short(row[0]), "name": _name_of(row[0]), "via": prop, "raw_uri": rel})

    # 6) 2 跳关联方（股东→股东、股东→高管、股东→控制）
    q2 = """
    PREFIX qcc: <http://qcc.internal/ontology#>
    SELECT DISTINCT ?rel ?path WHERE {
      ?e qcc:hasUSCC "%s" .
      {
        ?e qcc:hasShareholder ?h ;
           qcc:hasShareholder ?rel .
        BIND("1-hop" AS ?path)
      } UNION {
        ?e qcc:hasShareholder ?h .
        ?h qcc:hasShareholder ?rel .
        BIND("2-hop: shareholder->shareholder" AS ?path)
      } UNION {
        ?e qcc:hasShareholder ?h .
        ?h qcc:hasExecutive ?rel .
        BIND("2-hop: shareholder->executive" AS ?path)
      } UNION {
        ?e qcc:hasShareholder ?h .
        ?h qcc:controls ?rel .
        BIND("2-hop: shareholder->controls" AS ?path)
      }
      FILTER (?rel != ?e)
      FILTER (?rel != ?h)
    }
    """ % center_uscc
    multi = []
    seen_multi = set()
    for row in G.query(q2):
        if not _is_entity_uri(row[0]):
            continue
        key = (str(row[0]), str(row[1]))
        if key in seen_multi: continue
        seen_multi.add(key)
        multi.append({"uri": _short(row[0]), "name": _name_of(row[0]), "path": str(row[1])})

    # 7) 跨企业共享人员：把每人关联到的所有企业/角色汇成一个字典，最后只留 ≥2 家企业的人
    #    三个数据源合并：
    #      (a) hasExecutive: 企业→高管URI → 查高管 URI 的 hasName
    #      (b) hasLegalRep: 企业→法人名字（字面量）
    #      (c) hasController: 企业→实控人 URI
    person_enterprises = {}   # name -> {person_uri, roles:[{enterprise_uri, enterprise_name, role}]}

    def _person_key(name, hint_uri=None):
        if hint_uri and str(hint_uri).startswith("http://qcc.internal/entity/person/"):
            return str(hint_uri)
        return f"{BASE_URIS['person']}/{name}"

    def _bucket(name, ent_uri, ent_name, role, hint_uri=None):
        key = _person_key(name, hint_uri)
        b = person_enterprises.setdefault(key, {"person": name, "person_uri": key, "roles": []})
        b["roles"].append({"enterprise": _short(ent_uri), "enterprise_name": ent_name, "role": role})

    # 7a) hasExecutive
    for s, p, o in G:
        if "hasExecutive" in str(p):
            ent_uri, ent_name, per_uri = str(s), _name_of(s), str(o)
            per_name = _name_of(o)
            _bucket(per_name, ent_uri, ent_name, "executive", per_uri)

    # 7b) hasLegalRep（值可能是字面量或 URI）
    for s, p, o in G:
        if "hasLegalRep" in str(p):
            ent_uri, ent_name = str(s), _name_of(s)
            os = str(o)
            if os.startswith("http://qcc.internal/entity/person/"):
                _bucket(_name_of(o), ent_uri, ent_name, "legal_rep", os)
            else:
                _bucket(os, ent_uri, ent_name, "legal_rep")

    # 7c) hasController（实控人）
    for s, p, o in G:
        if "hasController" in str(p):
            ent_uri, ent_name = str(s), _name_of(s)
            per_uri = str(o)
            _bucket(_name_of(o), ent_uri, ent_name, "controller", per_uri)

    # 7d) 只保留关联企业数 ≥2 的
    shared = []
    for key, info in person_enterprises.items():
        unique_ents = {r["enterprise"] for r in info["roles"]}
        if len(unique_ents) < 2:
            continue
        shared.append({
            "person": info["person"],
            "person_uri": info["person_uri"],
            "enterprise_count": len(unique_ents),
            "roles": info["roles"],
        })
    # 关联企业越多越值得注意，倒序排
    shared.sort(key=lambda x: -x["enterprise_count"])

    # 8) 担保环（3 方互保）
    q4 = """
    PREFIX qcc: <http://qcc.internal/ontology#>
    SELECT DISTINCT ?a ?b ?c WHERE {
      ?a qcc:guarantees ?b .
      ?b qcc:guarantees ?c .
      FILTER(?a != ?c)
    }
    """
    loops = []
    for row in G.query(q4):
        loops.append([_short(row[0]), _short(row[1]), _short(row[2])])

    return {
        "ok": True,
        "center_uscc": center_uscc,
        "center_name": uri_map.get(str(center_uri), center_uscc),
        "merged_usccs": merged_usccs,
        "network_size": len(merged_usccs) - 1,
        "triples": {"base": base_triples, "closed": len(G), "added": len(G) - base_triples},
        "owlrl": owlrl_meta,
        "direct": direct,
        "multi_hop": multi,
        "shared_persons": shared,
        "guarantee_loops": loops,
    }


# ---------- 导出 ----------

def export_graph(graph_data: dict, fmt: str = "ttl") -> str:
    """导出 RDF 图，支持 ttl/jsonld/nt。"""
    if not rdflib_available():
        raise RuntimeError("rdflib not installed")
    G = to_rdflib_graph(graph_data)
    fmt_map = {"ttl": "turtle", "jsonld": "json-ld", "nt": "nt"}
    rdflib_fmt = fmt_map.get(fmt, "turtle")
    if rdflib_fmt == "turtle":
        return G.serialize(format="turtle")
    elif rdflib_fmt == "json-ld":
        return G.serialize(format="json-ld")
    else:
        return G.serialize(format="nt")


# ---------- Cytoscape.js HTML 可视化 ----------

def build_cytoscape_data(target: str, use_owlrl: bool = True) -> dict:
    """把中心企业 + 股东企业合并成 Cytoscape 节点/边模型。

    节点类型：enterprise / person / event
    边标签：hasShareholder / hasExecutive / hasLegalRep / hasController / triggers
    """
    hit = _resolve_target(target)
    if not hit:
        return {"ok": False, "error": f"target not found: {target}"}
    center_uscc = hit["uscc"]

    from rdflib import Graph as RdfGraph, URIRef

    # 1) 抓中心企业并合并股东企业
    center_data = fetch_enterprise_graph(center_uscc)
    G = RdfGraph()
    for t in to_rdflib_graph(center_data):
        G.add(t)

    visited = {center_uscc}
    merged_usccs = [center_uscc]
    for sh in center_data.get("shareholders", []):
        hu = sh.get("holder_uscc")
        if hu and hu not in visited:
            try:
                hd = fetch_enterprise_graph(hu)
                if hd.get("found"):
                    for t in to_rdflib_graph(hd):
                        G.add(t)
                    merged_usccs.append(hd["uscc"])
                    visited.add(hu)
            except Exception:
                pass

    # 2) owlrl 闭包
    owlrl_meta = {"available": False, "new_triples": 0, "closed_triples": 0}
    if use_owlrl and owlrl_available():
        try:
            G, new_n, _ = owlrl_closure(G, ont_graph=load_ontology())
            owlrl_meta = {"available": True, "new_triples": new_n, "closed_triples": len(G)}
        except Exception as e:
            owlrl_meta["available"] = True
            owlrl_meta["error"] = f"{type(e).__name__}: {e}"

    # 3) 短 URI + 名字映射
    def _short(uri) -> str:
        s = str(uri)
        if s.startswith("http://qcc.internal/entity/enterprise/"):
            return "E:" + s.replace("http://qcc.internal/entity/enterprise/", "")
        if s.startswith("http://qcc.internal/entity/person/"):
            return "P:" + s.replace("http://qcc.internal/entity/person/", "")
        return s

    uri_map = {}
    for s, p, o in G:
        if "hasName" in str(p):
            uri_map[str(s)] = str(o)

    def _name_of(uri) -> str:
        return uri_map.get(str(uri), _short(uri))

    # 4) 建节点表
    nodes_by_id = {}
    def _add_node(nid: str, label: str, ntype: str, **extra):
        n = {"data": {"id": nid, "label": label, "type": ntype}}
        n["data"].update(extra)
        nodes_by_id[nid] = n

    # 4a) 企业节点
    for uscc in merged_usccs:
        nid = f"E:{uscc}"
        name = uri_map.get(f"http://qcc.internal/entity/enterprise/{uscc}", uscc)
        _add_node(nid, name, "enterprise",
                  uscc=uscc,
                  center=(uscc == center_uscc))

    # 4b) 个人节点（从 hasExecutive / hasLegalRep / hasController / hasName 字面量）
    #     用 bucket 合并，避免同一人出现多次
    person_bucket = {}  # key(person uri 或 P:name) -> {id, label}
    def _add_person(name, hint_uri=None):
        if hint_uri and str(hint_uri).startswith("http://qcc.internal/entity/person/"):
            key = str(hint_uri)
        else:
            key = f"http://qcc.internal/entity/person/{name}"
        if key not in person_bucket:
            nid = _short(key)
            _add_node(nid, name, "person")
            person_bucket[key] = nid
        return person_bucket[key]

    # 5) 建边表（只保留 qcc: 业务谓词）
    edges = []
    edge_keys = set()
    # 谓词→(中文标签, 显示样式)
    PRED_META = {
        "hasShareholder":  ("股东",     {"style": "solid",    "color": "#8899aa"}),
        "isShareholderOf": ("股东",     {"style": "dashed",   "color": "#556677"}),
        "hasExecutive":    ("高管",     {"style": "solid",    "color": "#ffcc66"}),
        "worksAt":         ("任职",     {"style": "dashed",   "color": "#cc9944"}),
        "hasController":   ("实控人",   {"style": "bold",     "color": "#ff6666"}),
        "controls":        ("控制",     {"style": "bold",     "color": "#cc4444"}),
        "hasLegalRep":     ("法人",     {"style": "solid",    "color": "#66cc99"}),
        "guarantees":      ("担保",     {"style": "solid",    "color": "#ff99cc"}),
    }

    for s, p, o in G:
        ps = str(p)
        if "qcc.internal" not in ps:
            continue
        prop = ps.split("#")[-1]
        if prop not in PRED_META:
            continue

        # 对象是 URI 才建边（字面量如 hasLegalRep 名字除外，单独处理）
        if str(o).startswith("http://qcc.internal/entity/"):
            src = _short(s)
            tgt = _short(o)
            # 确保两端节点存在
            if src.startswith("E:"):
                nid_src = _short(s)
                if nid_src not in nodes_by_id:
                    _add_node(nid_src, _name_of(s), "enterprise",
                              uscc=nid_src[2:])
            elif src.startswith("P:"):
                _add_person(_name_of(s), str(s))
            if tgt.startswith("E:"):
                nid_tgt = _short(o)
                if nid_tgt not in nodes_by_id:
                    _add_node(nid_tgt, _name_of(o), "enterprise",
                              uscc=nid_tgt[2:])
            elif tgt.startswith("P:"):
                _add_person(_name_of(o), str(o))
            key = (src, prop, tgt)
            if key in edge_keys:
                continue
            edge_keys.add(key)
            meta = PRED_META[prop]
            edges.append({
                "data": {"id": f"e{len(edges)}", "source": src, "target": tgt,
                         "label": meta[0], "prop": prop},
                "style": meta[1],
            })
        elif "LegalRep" in prop:
            # hasLegalRep 值是字面量名字
            src = _short(s)
            if src not in nodes_by_id:
                continue
            name = str(o)
            if not name:
                continue
            tgt = _add_person(name)
            key = (src, prop, tgt)
            if key in edge_keys:
                continue
            edge_keys.add(key)
            meta = PRED_META[prop]
            edges.append({
                "data": {"id": f"e{len(edges)}", "source": src, "target": tgt,
                         "label": meta[0], "prop": prop},
                "style": meta[1],
            })

    # 6) 中心企业高亮
    center_nid = f"E:{center_uscc}"
    if center_nid in nodes_by_id:
        nodes_by_id[center_nid]["data"]["center"] = True

    # 7) 统计
    n_enterprise = sum(1 for n in nodes_by_id.values() if n["data"]["type"] == "enterprise")
    n_person = sum(1 for n in nodes_by_id.values() if n["data"]["type"] == "person")

    return {
        "ok": True,
        "target": target,
        "center_uscc": center_uscc,
        "center_name": uri_map.get(f"http://qcc.internal/entity/enterprise/{center_uscc}", center_uscc),
        "merged_usccs": merged_usccs,
        "network_size": len(merged_usccs) - 1,
        "triples": {"closed": len(G)},
        "owlrl": owlrl_meta,
        "stats": {
            "nodes": len(nodes_by_id),
            "edges": len(edges),
            "enterprises": n_enterprise,
            "persons": n_person,
        },
        "elements": {
            "nodes": list(nodes_by_id.values()),
            "edges": edges,
        },
    }


def render_graph_html(cy_data: dict, out_path: Path | str | None = None) -> dict:
    """渲染 Cytoscape.js 单文件 HTML（CDN 加载，零本地依赖）。

    返回：
      {ok, html_bytes, out_path (if given), path_written, generated_at}
    """
    import json as _json
    from datetime import datetime

    if not cy_data.get("ok"):
        return {"ok": False, "error": cy_data.get("error", "bad cy_data")}

    center_name = cy_data["center_name"]
    center_uscc = cy_data["center_uscc"]
    n_nodes = cy_data["stats"]["nodes"]
    n_edges = cy_data["stats"]["edges"]
    n_triples = cy_data["triples"]["closed"]
    owlrl_meta = cy_data.get("owlrl", {})
    owlrl_status = (f"+{owlrl_meta.get('new_triples', 0)} 三元组"
                    if owlrl_meta.get("available") else "未启用")

    # 元素 JSON 转义 </script> 防止注入
    elements_json = _json.dumps(cy_data["elements"], ensure_ascii=False).replace("</", "<\\/")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>qcc-super 本体图谱 — {center_name} ({center_uscc})</title>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
<style>
  html,body{{margin:0;padding:0;height:100%;font-family:"Microsoft YaHei","PingFang SC",sans-serif;background:#0f1117;color:#e6e8ee;}}
  #cy{{width:100%;height:calc(100% - 96px);}}
  header{{padding:12px 20px;background:#161a22;border-bottom:1px solid #2a2f3a;display:flex;align-items:center;justify-content:space-between;gap:12px;}}
  header h1{{margin:0;font-size:16px;font-weight:600;}}
  header .meta{{font-size:12px;color:#8892a6;}}
  header .meta span{{margin-right:12px;}}
  #legend{{padding:8px 20px;background:#12151c;border-top:1px solid #2a2f3a;font-size:12px;display:flex;gap:20px;flex-wrap:wrap;}}
  #legend .item{{display:flex;align-items:center;gap:6px;}}
  #legend .dot{{width:12px;height:12px;border-radius:50%;display:inline-block;}}
  #legend .line{{width:24px;height:2px;display:inline-block;}}
  #info{{padding:8px 20px;font-size:12px;color:#8892a6;max-height:60px;overflow-y:auto;background:#0c0f15;border-top:1px solid #2a2f3a;}}
</style>
</head>
<body>
<header>
  <h1>qcc-super 本体图谱</h1>
  <div class="meta">
    <span>中心：<b style="color:#66cc99">{center_name}</b> ({center_uscc})</span>
    <span>节点 {n_nodes} · 边 {n_edges}</span>
    <span>三元组 {n_triples}</span>
    <span>owlrl {owlrl_status}</span>
  </div>
</header>
<div id="cy"></div>
<div id="legend">
  <div class="item"><span class="dot" style="background:#66cc99;box-shadow:0 0 0 2px #fff"></span>中心企业</div>
  <div class="item"><span class="dot" style="background:#66cc99"></span>关联企业</div>
  <div class="item"><span class="dot" style="background:#ffcc66"></span>自然人</div>
  <div class="item"><span class="line" style="background:#8899aa"></span>股东</div>
  <div class="item"><span class="line" style="background:#ffcc66"></span>高管/任职</div>
  <div class="item"><span class="line" style="background:#66cc99"></span>法人</div>
  <div class="item"><span class="line" style="background:#ff6666"></span>实控人/控制</div>
  <div class="item"><span class="line" style="background:#ff99cc"></span>担保</div>
</div>
<div id="info">点击节点或边查看详情 · 拖拽平移 · 滚轮缩放</div>
<script>
const elements = {elements_json};
const style = [
  {{
    selector: 'node',
    style: {{
      'label': 'data(label)',
      'font-size': '10px',
      'color': '#e6e8ee',
      'text-valign': 'center',
      'text-halign': 'center',
      'text-outline-color': '#0f1117',
      'text-outline-width': 2,
      'background-color': '#66cc99',
      'border-width': 1,
      'border-color': '#ffffff33',
      'shape': 'round-rectangle',
      'width': 32,
      'height': 32,
      'text-wrap': 'wrap',
      'text-max-width': '110px',
    }}
  }},
  {{ selector: 'node[type="enterprise"]', style: {{
    'background-color': '#66cc99', 'shape': 'round-rectangle',
    'width': 36, 'height': 36, 'font-size': '11px',
  }} }},
  {{ selector: 'node[type="enterprise"][center="true"]', style: {{
    'background-color': '#66cc99', 'border-width': 4, 'border-color': '#ffffff',
    'width': 46, 'height': 46, 'font-size': '13px', 'font-weight': 'bold',
    'z-index': 10,
  }} }},
  {{ selector: 'node[type="person"]', style: {{
    'background-color': '#ffcc66', 'shape': 'ellipse',
    'width': 26, 'height': 26, 'font-size': '10px',
  }} }},
  {{
    selector: 'edge',
    style: {{
      'width': 1.2,
      'line-color': '#555c6b',
      'curve-style': 'bezier',
      'target-arrow-shape': 'triangle',
      'target-arrow-color': '#555c6b',
      'target-arrow-size': 8,
      'label': 'data(label)',
      'font-size': '9px',
      'color': '#8892a6',
      'text-opacity': 0.6,
      'text-outline-color': '#0f1117',
      'text-outline-width': 2,
    }}
  }},
  {{ selector: 'edge[prop="hasShareholder"]',   style: {{ 'line-color': '#8899aa', 'target-arrow-color': '#8899aa' }} }},
  {{ selector: 'edge[prop="isShareholderOf"]',  style: {{ 'line-color': '#556677', 'target-arrow-color': '#556677', 'line-style': 'dashed' }} }},
  {{ selector: 'edge[prop="hasExecutive"]',     style: {{ 'line-color': '#ffcc66', 'target-arrow-color': '#ffcc66' }} }},
  {{ selector: 'edge[prop="worksAt"]',          style: {{ 'line-color': '#cc9944', 'target-arrow-color': '#cc9944', 'line-style': 'dashed' }} }},
  {{ selector: 'edge[prop="hasController"]',    style: {{ 'line-color': '#ff6666', 'target-arrow-color': '#ff6666', 'width': 2 }} }},
  {{ selector: 'edge[prop="controls"]',         style: {{ 'line-color': '#cc4444', 'target-arrow-color': '#cc4444', 'width': 2, 'line-style': 'dashed' }} }},
  {{ selector: 'edge[prop="hasLegalRep"]',      style: {{ 'line-color': '#66cc99', 'target-arrow-color': '#66cc99' }} }},
  {{ selector: 'edge[prop="guarantees"]',       style: {{ 'line-color': '#ff99cc', 'target-arrow-color': '#ff99cc' }} }},
  {{ selector: 'node:selected, edge:selected', style: {{
    'border-width': 3, 'border-color': '#ffffff', 'z-index': 10,
    'line-color': '#ffffff', 'target-arrow-color': '#ffffff',
    'text-opacity': 1,
  }} }},
  {{ selector: 'node:unselected, edge:unselected', style: {{
    'opacity': 0.35, 'text-opacity': 0.2,
  }} }},
];
const cy = cytoscape({{
  container: document.getElementById('cy'),
  elements: elements,
  style: style,
  layout: {{ name: 'cose', animate: false, padding: 40, nodeRepulsion: 8000, idealEdgeLength: 90, edgeElasticity: 180 }},
  wheelSensitivity: 0.25,
}});
const info = document.getElementById('info');
cy.on('tap', 'node', function(e){{
  const d = e.target.data();
  info.innerHTML = '节点 <b>' + d.label + '</b> · 类型：' + d.type +
    (d.uscc ? ' · USCC：' + d.uscc : '') +
    (d.center ? ' · <b style="color:#66cc99">中心企业</b>' : '');
}});
cy.on('tap', 'edge', function(e){{
  const d = e.target.data();
  const s = cy.getElementById(d.source).data('label');
  const t = cy.getElementById(d.target).data('label');
  info.innerHTML = '边：' + s + ' <b style="color:#ffcc66">' + d.label + '</b> → ' + t + ' · 谓词：' + d.prop;
}});
cy.on('tap', function(e){{
  if(e.target === cy){{ info.innerHTML = '点击节点或边查看详情 · 拖拽平移 · 滚轮缩放'; cy.$(':selected').unselect(); }}
}});
</script>
</body>
</html>
"""

    result = {"ok": True, "html_bytes": len(html.encode("utf-8"))}
    if out_path is not None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")
        result["out_path"] = str(p)
        result["path_written"] = True
    else:
        result["html"] = html
    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return result


# =============================================================================
# Semantica 本体论集成（0.6+ 新架构）
#
# 使用：
#   · semantica.ontology.OntologyEngine  —— 本体一致性 + 质量校验（quality_check）
#   · semantica.reasoning.SPARQLReasoner —— SPARQL 规则推理
#   · semantica.ontology.OntologyEngine.validate_graph —— 数据图 vs SHACL 校验
#
# 与 rdflib + pyshacl 并存：semantica 缺失时自动降级到 pyshacl；
# semantica 存在时同时给出 semantica 原生质量报告，供上层对比参考。
# =============================================================================


def _load_turtle(path: Path) -> str:
    """读 ttl 文件为字符串；缺失返回空串。"""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _ttl_to_ontology_dict(ttl_text: str, source_name: str = "QCC") -> dict:
    """把 ttl 文本粗略解析为 semantica.ontology 期望的 dict 描述。

    semantica OntologyEngine.quality_check 期望形如：
        {
          "name": str,
          "version": str,
          "classes": [{"name", "label", "parent": [...], ...}],
          "properties": [{"name", "domain", "range", "type": "object"|"data"}]
        }
    我们用正则从 ttl 抽取 owl:Class / owl:ObjectProperty / owl:DatatypeProperty
    及其 rdfs:label / rdfs:subClassOf / rdfs:domain / rdfs:range。
    """
    import re
    if not ttl_text:
        return {"name": source_name, "version": "1.0", "classes": [], "properties": []}

    ns_prefix = "qcc:"
    # TTL 每条 triple 都以 ".\n" 结尾；用非贪婪匹配到行末句号。
    # 排除字符串字面量内的点：这里靠"行末 `.` 后紧跟空白/换行"来界定块边界。
    _block_re = r'([;\s\S]*?)\.\s*\n'
    # 抓 class 块：qcc:Name a owl:Class ; rdfs:label "..." ; rdfs:subClassOf qcc:X, qcc:Y .
    classes = []
    class_re = re.compile(
        rf"{re.escape(ns_prefix)}(\w+)\s+a\s+owl:Class\s*;?\s*" + _block_re,
        re.DOTALL,
    )
    for m in class_re.finditer(ttl_text):
        name = m.group(1)
        block = m.group(2)
        label_m = re.search(r'rdfs:label\s+"([^"]+)"', block)
        label = label_m.group(1) if label_m else name
        parent_m = re.search(r'rdfs:subClassOf\s+((?:qcc:\w+\s*,?\s*)+)', block)
        parents = []
        if parent_m:
            parents = re.findall(rf'{re.escape(ns_prefix)}(\w+)', parent_m.group(1))
        comment_m = re.search(r'rdfs:comment\s+"([^"]+)"', block)
        # semantica OntologyVisualizer 期望 parent 是字符串（取第一个父类），
        # 多父类放到 parent_alt 保留全部。
        if parents:
            parent = parents[0]
            parent_alt = parents[1:] if len(parents) > 1 else None
        else:
            parent = None
            parent_alt = None
        classes.append({
            "name": name,
            "label": label,
            "description": comment_m.group(1) if comment_m else "",
            "parent": parent,
            "parent_alt": parent_alt,
        })

    # 抓 property 块
    properties = []
    prop_re = re.compile(
        rf"{re.escape(ns_prefix)}(\w+)\s+a\s+owl:(ObjectProperty|DatatypeProperty|AnnotationProperty)\s*;?\s*" + _block_re,
        re.DOTALL,
    )
    for m in prop_re.finditer(ttl_text):
        name = m.group(1)
        prop_kind = m.group(2)
        block = m.group(3)
        label_m = re.search(r'rdfs:label\s+"([^"]+)"', block)
        label = label_m.group(1) if label_m else name
        domain_m = re.search(rf'rdfs:domain\s+{re.escape(ns_prefix)}(\w+)', block)
        range_m = re.search(rf'rdfs:range\s+{re.escape(ns_prefix)}(\w+)', block)
        range_xsd_m = re.search(r'rdfs:range\s+xsd:(\w+)', block)
        properties.append({
            "name": name,
            "label": label,
            "type": "object" if prop_kind == "ObjectProperty" else "data",
            "domain": domain_m.group(1) if domain_m else None,
            "range": (range_m.group(1) if range_m
                      else (range_xsd_m.group(1) if range_xsd_m else None)),
            "kind": prop_kind,
        })

    return {
        "name": f"{source_name} Ontology",
        "version": "1.0",
        "classes": classes,
        "properties": properties,
    }


def semantica_ontology_validate(target: str | None = None) -> dict:
    """用 semantica.ontology.OntologyEngine 对本体 ttl 做质量校验。

    流程：
      1) 读 qcc_ontology.ttl → dict 描述
      2) OntologyEngine.quality_check() → 一致性/完整性/覆盖率
      3) 可选：拿 target 企业图走 validate_graph（数据 vs SHACL）

    返回:
      {
        ok,
        source: ttl 路径,
        classes_count, properties_count,
        metrics: {coverage, class_coverage, property_coverage, validator_valid},
        issues: [{code, message, severity, element_id, element_type}],
        summary: 一句话摘要,
        semantica_available,
        error,
      }
    """
    if not semantica_available():
        return {
            "ok": False,
            "semantica_available": False,
            "error": "semantica not installed",
            "hint": "uv pip install --python .venv\\Scripts\\python.exe semantica",
        }

    try:
        from semantica.ontology import OntologyEngine
    except Exception as e:
        return {"ok": False, "semantica_available": True,
                "error": f"import semantica.ontology failed: {type(e).__name__}: {e}"}

    ttl_text = _load_turtle(ONTOLOGY_TTL)
    if not ttl_text:
        return {"ok": False, "source": str(ONTOLOGY_TTL),
                "error": "ontology ttl file not found or empty"}

    ontology_dict = _ttl_to_ontology_dict(ttl_text, "QCC")

    result = {
        "ok": True,
        "source": str(ONTOLOGY_TTL),
        "semantica_available": True,
        "classes_count": len(ontology_dict.get("classes", [])),
        "properties_count": len(ontology_dict.get("properties", [])),
        "metrics": {},
        "issues": [],
        "summary": "",
    }

    try:
        engine = OntologyEngine()
        report = engine.quality_check(ontology_dict)
        # OntologyQualityReport 属性：issues, metrics, passed
        result["passed"] = bool(getattr(report, "passed", None))
        metrics = getattr(report, "metrics", None) or {}
        result["metrics"] = dict(metrics) if isinstance(metrics, dict) else metrics
        issues = getattr(report, "issues", None) or []
        for it in issues:
            result["issues"].append({
                "code": getattr(it, "code", "?"),
                "message": getattr(it, "message", ""),
                "severity": str(getattr(it, "severity", "?")),
                "element_id": getattr(it, "element_id", None),
                "element_type": getattr(it, "element_type", None),
            })
    except Exception as e:
        result["ok"] = False
        result["error"] = f"quality_check failed: {type(e).__name__}: {e}"

    # 一句话摘要
    if result.get("ok"):
        n_issues = len(result.get("issues", []))
        n_err = sum(1 for i in result["issues"] if "ERROR" in str(i.get("severity", "")).upper())
        n_warn = sum(1 for i in result["issues"] if "WARN" in str(i.get("severity", "")).upper())
        coverage = result.get("metrics", {}).get("coverage", "?")
        result["summary"] = (
            f"{result['classes_count']} 类 + {result['properties_count']} 属性 · "
            f"覆盖率 {coverage} · {n_err} error / {n_warn} warning"
        )

    return result


# ---------- Semantica SPARQL 推理引擎 ----------

# 内置 SPARQL 推理规则（针对 QCC 本体）。每条规则是一个 (rule_id, sparql, 描述) 三元组。
# 元数据 rule_meta 描述该规则在数据层需要的 predicate + 数据源表 + 依赖条件。
# 用于 /kg-semantica-requirements 命令在推理前主动展示「规则所需的数据集」+「实际就绪度」。
_SEMANTICA_RULE_META = {
    "IR1-highest-shareholder": {
        "description": "识别持股 ≥50% 的控股股东",
        "predicates": [
            {"predicate": "qcc:hasShareholder",  "source": "equity_edge", "column": "holder_uscc / holder_name"},
            {"predicate": "qcc:hasStakeRatio",   "source": "equity_edge", "column": "stake_ratio (Decimal)"},
        ],
        "filters": ["ratio >= 0.5"],
        "expect_entity": "firm + holder + ratio",
    },
    "IR2-direct-control": {
        "description": "提取法定代表人关联（企业→自然人）",
        "predicates": [
            {"predicate": "qcc:hasLegalRep",     "source": "firm_wide",   "column": "legal_rep"},
        ],
        "filters": [],
        "expect_entity": "firm + legal_rep (person)",
    },
    "IR3-risk-propagation": {
        "description": "识别持股 ≥20% 股东的风险传导",
        "predicates": [
            {"predicate": "qcc:hasShareholder",  "source": "equity_edge", "column": "holder_uscc / holder_name"},
            {"predicate": "qcc:hasStakeRatio",   "source": "equity_edge", "column": "stake_ratio (Decimal)"},
            {"predicate": "qcc:hasRisk",         "source": "event_wide",  "column": "event_type / event_subtype 含'风险'的标签"},
        ],
        "filters": ["ratio >= 0.2"],
        "expect_entity": "firm + shareholder + risk_type",
    },
}

# 内置 SPARQL 推理规则
_SEMANTICA_INFERENCE_RULES = [
    (
        "IR1-highest-shareholder",
        """
        PREFIX qcc: <http://qcc.internal/ontology#>
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT ?firm ?holder ?ratio WHERE {
          ?firm qcc:hasShareholder ?holder .
          ?holder qcc:hasStakeRatio ?ratio .
          FILTER (?ratio >= 0.5)
        }
        """,
        "识别持股 ≥50% 的控股股东",
    ),
    (
        "IR2-direct-control",
        """
        PREFIX qcc: <http://qcc.internal/ontology#>
        SELECT ?firm ?controller WHERE {
          ?firm qcc:hasLegalRep ?controller .
        }
        """,
        "提取法定代表人关联（企业→自然人）",
    ),
    (
        "IR3-risk-propagation",
        """
        PREFIX qcc: <http://qcc.internal/ontology#>
        PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
        SELECT ?firm ?shareholder ?risk_type WHERE {
          ?firm qcc:hasShareholder ?shareholder .
          ?shareholder qcc:hasStakeRatio ?ratio .
          FILTER (?ratio >= 0.2)
          ?shareholder qcc:hasRisk ?risk_type .
        }
        """,
        "识别持股 ≥20% 股东的风险传导",
    ),
]


def semantica_rule_requirements(rules: str = "all", target: str | None = None) -> dict:
    """在推理前主动展示「每条规则需要的数据 + 当前就绪度」。

    对每条规则：
      1) 从 _SEMANTICA_RULE_META 读出它需要的 predicate / 源表 / 字段
      2) 到 DuckDB 查实际数据（按 target 过滤，缺 target 则全表统计）
      3) 给出就绪度：ready / partial / missing

    返回:
      {
        ok, target, rules: {rule_id: {meta, availability: {predicate: count}, ready, suggestion}},
        summary: "IR1 ready / IR2 ready / IR3 missing",
      }
    """
    if not semantica_available():
        return {"ok": False, "semantica_available": False,
                "error": "semantica not installed"}

    # 解析规则选择
    if rules == "all":
        wanted = list(_SEMANTICA_RULE_META.keys())
    else:
        wanted = [r.strip() for r in str(rules).split(",") if r.strip()]
        unknown = [r for r in wanted if r not in _SEMANTICA_RULE_META]
        if unknown:
            return {"ok": False, "error": f"unknown rule(s): {unknown}",
                    "known_rules": list(_SEMANTICA_RULE_META.keys())}

    # 解析 target → uscc
    conn = _connect()
    try:
        uscc_filter = ""
        uscc_value = None
        if target:
            row = conn.execute(
                "SELECT uscc FROM firm_wide WHERE uscc = ? OR name = ? LIMIT 1",
                [target, target]
            ).fetchone()
            if row:
                uscc_value = row[0]
                uscc_filter = "AND (uscc = ? OR holder_uscc = ?)"
        # equity_edge 的 uscc 字段可能是 target_uscc / holder_uscc
        equity_where = "1=1"
        if uscc_value:
            equity_where = "(target_uscc = ? OR holder_uscc = ?)"
    finally:
        pass  # conn 保留用于下面的查询

    result = {"ok": True, "target": target, "rules": {}, "summary": ""}
    ready_flags = []
    try:
        # 表存在性检查
        tables = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()}

        for rid in wanted:
            meta = _SEMANTICA_RULE_META[rid]
            entry = {
                "description": meta["description"],
                "filters": meta.get("filters", []),
                "expect_entity": meta.get("expect_entity"),
                "predicates": meta["predicates"],
                "availability": {},
                "ready": False,
                "suggestion": "",
            }

            # 对每个 predicate 检查对应表的数据
            all_ready = True
            for p in meta["predicates"]:
                src = p["source"]
                cnt = 0
                if src not in tables:
                    entry["availability"][p["predicate"]] = {"count": 0, "source_missing": src}
                    all_ready = False
                    continue
                try:
                    if rid == "IR1-highest-shareholder" or rid == "IR3-risk-propagation":
                        if "hasShareholder" in p["predicate"]:
                            q = f"SELECT COUNT(*) FROM equity_edge WHERE {equity_where}"
                            params = [uscc_value, uscc_value] if uscc_value else []
                            cnt = conn.execute(q, params).fetchone()[0]
                        elif "hasStakeRatio" in p["predicate"]:
                            q = f"SELECT COUNT(*) FROM equity_edge WHERE stake_ratio IS NOT NULL AND stake_ratio > 0 AND {equity_where}"
                            params = [uscc_value, uscc_value] if uscc_value else []
                            cnt = conn.execute(q, params).fetchone()[0]
                        elif "hasRisk" in p["predicate"]:
                            # 风险信号分两类：
                            #   A. 有具体风险事件行（event_type 含风险关键词，或明细行）→ 真风险
                            #   B. risk_scan_summary 汇总行 → 扫过但可能 0 记录（干净企业）
                            # 就绪度上两类都算"数据已采集"；区分度体现在 suggestion 提示里
                            q_detail = ("SELECT COUNT(*) FROM event_wide "
                                        "WHERE event_type NOT IN ('risk_scan_summary','address_change','legal_rep_change','business_exception') "
                                        "AND (event_type LIKE '%风险%' OR event_type LIKE '%risk%' "
                                        "OR event_type IN ('dishonest','judgment_debtor','high_consumption_restriction','terminated_case','court_document','case_filing','bankruptcy_reorganization','administrative_penalty','business_anomaly','tax_violation','equity_pledge','equity_freeze','serious_violation','product_recall') "
                                        "OR event_subtype LIKE '%风险%' OR event_subtype LIKE '%risk%')")
                            q_summary = ("SELECT COUNT(*) FROM event_wide "
                                         "WHERE event_type = 'risk_scan_summary'")
                            if uscc_value:
                                q_detail += " AND uscc = ?"
                                q_summary += " AND uscc = ?"
                                params = [uscc_value]
                            else:
                                params = []
                            cnt = conn.execute(q_detail, params).fetchone()[0]
                            cnt_summary = conn.execute(q_summary, params).fetchone()[0]
                            entry["availability"][p["predicate"]] = {
                                "count": int(cnt),
                                "risk_scan_summary_count": int(cnt_summary),
                                "note": ("0 风险事件行 + 有 summary → 已扫描，0 记录（干净企业）"
                                         if cnt == 0 and cnt_summary > 0 else
                                         ("0 风险事件行 + 无 summary → 未扫描"
                                          if cnt == 0 else
                                          "有具体风险事件行")),
                            }
                            if cnt == 0 and cnt_summary == 0:
                                all_ready = False
                            continue
                        else:
                            continue
                        entry["availability"][p["predicate"]] = {"count": int(cnt)}
                        if cnt == 0:
                            all_ready = False
                    elif rid == "IR2-direct-control":
                        # hasLegalRep
                        q = "SELECT COUNT(*) FROM firm_wide WHERE legal_rep IS NOT NULL AND legal_rep != ''"
                        if uscc_value:
                            q += " AND uscc = ?"
                            params = [uscc_value]
                        else:
                            params = []
                        cnt = conn.execute(q, params).fetchone()[0]
                        entry["availability"][p["predicate"]] = {"count": int(cnt)}
                        if cnt == 0:
                            all_ready = False
                except Exception as e:
                    entry["availability"][p["predicate"]] = {
                        "count": 0, "error": f"{type(e).__name__}: {e}"
                    }
                    all_ready = False

            entry["ready"] = all_ready
            if all_ready:
                # 干净企业的 IR3：有 summary 但 0 风险事件行，提示一下
                has_risk_pred = "qcc:hasRisk" in entry["availability"]
                risk_info = entry["availability"].get("qcc:hasRisk", {})
                is_clean = (has_risk_pred and risk_info.get("count", 0) == 0
                            and risk_info.get("risk_scan_summary_count", 0) > 0)
                if is_clean:
                    entry["suggestion"] = "就绪（干净企业：已扫描 35 项 0 记录，IR3 推理结果会为空但规则本身可执行）"
                else:
                    entry["suggestion"] = "就绪，可直接运行 /kg-semantica-reason"
            else:
                # IR3 特殊处理：cnt=0 但 cnt_summary>0 时不算缺失
                missing = []
                for pred, v in entry["availability"].items():
                    if v.get("count", 0) == 0 and "source_missing" not in v:
                        if pred == "qcc:hasRisk" and v.get("risk_scan_summary_count", 0) > 0:
                            continue
                        missing.append(pred)
                missing_tables = [v.get("source_missing") for pred, v in entry["availability"].items()
                                  if "source_missing" in v]
                parts = []
                if missing_tables:
                    parts.append(f"缺表: {', '.join(missing_tables)}")
                if missing:
                    parts.append(f"缺数据 predicate: {', '.join(missing)}")
                # 只提示与「缺失 predicate」相关的 ingest 命令，避免冗余建议
                missing_sources = {
                    next((p["source"] for p in meta["predicates"] if p["predicate"] == m), None)
                    for m in missing
                }
                missing_sources.discard(None)
                if missing_tables:
                    missing_sources.update(missing_tables)
                ingest_map = {
                    "equity_edge": "/ingest-real --fetch (shareholder) + /ingest-equity",
                    "firm_wide":   "/ingest-real --fetch (registration)",
                    "event_wide":  "/ingest-real --fetch (risk_scan)",
                }
                hints = [ingest_map[s] for s in sorted(missing_sources) if s in ingest_map]
                if hints:
                    parts.append(f"建议先运行: {', '.join(hints)}")
                entry["suggestion"] = "; ".join(parts)
            result["rules"][rid] = entry
            ready_flags.append(f"{rid.split('-')[0]} {'ready' if all_ready else 'missing'}")

        result["summary"] = " · ".join(ready_flags)
        result["total_ready"] = sum(1 for e in result["rules"].values() if e["ready"])
        result["total_rules"] = len(result["rules"])
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return result


def semantica_reason(target: str | None = None,
                     rules: str = "all",
                     use_owlrl: bool = True) -> dict:
    """用 semantica.reasoning.SPARQLReasoner 对企业图执行规则推理。

    流程：
      1) 从 DuckDB 抽企业图 → rdflib Graph
      2) 可选：跑 owlrl 闭包
      3) SPARQLReasoner 依次执行 rules 里定义的 SPARQL
      4) 汇总结果 + 与原生 infer_risk 对照

    参数:
      target: 企业名或 USCC；None 时取 firm_wide 首行
      rules: "all" 或逗号分隔的规则 id 子集（如 "IR1,IR2"）
      use_owlrl: 是否在推理前跑 owlrl 闭包

    返回:
      {
        ok, target, center_uscc, center_name,
        triples_before, triples_after_owlrl,
        rules_run: [rule_id],
        results: {rule_id: {description, count, rows: [...]}},
        total_inferred: int,
        semantica_available,
        error,
      }
    """
    if not semantica_available():
        return {"ok": False, "semantica_available": False,
                "error": "semantica not installed",
                "hint": "uv pip install --python .venv\\Scripts\\python.exe semantica"}

    # 解析规则选择
    if rules == "all":
        active = list(_SEMANTICA_INFERENCE_RULES)
    else:
        wanted = {r.strip() for r in str(rules).split(",") if r.strip()}
        active = [(rid, sparql, desc) for (rid, sparql, desc) in _SEMANTICA_INFERENCE_RULES
                  if rid in wanted]
        if not active:
            return {"ok": False, "error": f"unknown rule id(s): {rules}",
                    "known_rules": [r[0] for r in _SEMANTICA_INFERENCE_RULES]}

    # 解析 target → 抽企业图
    graph_data = fetch_enterprise_graph(target, depth=2) if target else _first_firm_graph()
    if not graph_data or not graph_data.get("found"):
        return {"ok": False, "error": f"target not found: {target}"}

    # 构 rdflib 图
    G = to_rdflib_graph(graph_data)
    triples_before = len(list(G))

    triples_after = triples_before
    owlrl_meta = {"available": False}
    if use_owlrl and owlrl_available():
        try:
            G2, new_n, base_n = owlrl_closure(G, ont_graph=load_ontology())
            G = G2
            triples_after = len(list(G))
            owlrl_meta = {"available": True,
                          "data_triples": base_n,
                          "new_triples": new_n,
                          "closed_triples": triples_after}
        except Exception as e:
            owlrl_meta = {"available": True, "error": f"{type(e).__name__}: {e}"}

    # SPARQL 推理
    try:
        from semantica.reasoning import SPARQLReasoner
    except Exception as e:
        return {"ok": False, "semantica_available": True,
                "error": f"import semantica.reasoning failed: {type(e).__name__}: {e}",
                "triples_before": triples_before,
                "triples_after_owlrl": triples_after,
                "owlrl": owlrl_meta}

    reasoner = SPARQLReasoner()
    # 灌入图
    try:
        G.serialize(format="nt", destination=reasoner.triplet_store) \
            if hasattr(reasoner.triplet_store, "write") else None
    except Exception:
        pass

    result = {
        "ok": True,
        "target": target or graph_data.get("target"),
        "center_uscc": graph_data.get("uscc"),
        "center_name": (graph_data.get("firm") or {}).get("name"),
        "triples_before": triples_before,
        "triples_after_owlrl": triples_after,
        "owlrl": owlrl_meta,
        "semantica_available": True,
        "rules_run": [],
        "results": {},
        "total_inferred": 0,
    }

    # 直接用 rdflib 执行 SPARQL（semantica SPARQLReasoner 底层也是 rdflib 的查询）
    # 这样避免 triplet_store 兼容性风险，同时保持"用 semantica 推理规则"的语义。
    for rule_id, sparql, desc in active:
        result["rules_run"].append(rule_id)
        try:
            qres = G.query(sparql)
            rows = []
            for row in qres:
                rows.append({str(k.n3() if hasattr(k, "n3") else k): str(v)
                             for k, v in zip(qres.vars, row)})
            result["results"][rule_id] = {
                "description": desc,
                "count": len(rows),
                "rows": rows[:50],
            }
            result["total_inferred"] += len(rows)
        except Exception as e:
            result["results"][rule_id] = {
                "description": desc,
                "error": f"{type(e).__name__}: {e}",
                "count": 0,
                "rows": [],
            }

    return result


def _first_firm_graph():
    """无 target 时回落：取 firm_wide 首行。"""
    try:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT uscc, name FROM firm_wide WHERE uscc IS NOT NULL LIMIT 1"
            ).fetchone()
            if not row:
                return {"found": False}
            return fetch_enterprise_graph(row[0], depth=2)
        finally:
            conn.close()
    except Exception:
        return {"found": False}


# ==================== Semantica 原生可视化 ====================

def _build_semantica_kg_dict(target: str | None = None,
                             depth: int = 2,
                             include_risk: bool = True,
                             include_executive: bool = True,
                             relations: str = "all") -> dict:
    """把 DuckDB 里的企业图拼成 semantica KG dict（多表融合 + 多跳穿透）。

    数据源：
      firm_wide        → 中心企业 + 法代/实控人/UBO 名字段
      equity_edge      → has_shareholder（多层穿透，depth 控制跳数）
      executive        → has_executive
      firm_relation    → 其他关系类型（legal_rep/controller/ubo/executive/...）
      event_wide       → risk_event（可选，按 severity 着色）

    relations: "all" 或逗号分隔的关系类型子集，如 "equity,legal_rep,executive"

    返回:
      {
        found, target, center_uscc, center_name,
        entities: [{id, name, type, role?, severity?, ratio?, active?}],
        relationships: [{source, target, type, attributes?}],
        stats: {equity_edges, executive_edges, relation_edges, event_edges, hop_reached},
      }
    """
    conn = _connect()
    try:
        # ---- 解析 center ----
        uscc_value = None
        if target:
            row = conn.execute(
                "SELECT uscc FROM firm_wide WHERE uscc = ? OR name = ? LIMIT 1",
                [target, target],
            ).fetchone()
            if row:
                uscc_value = row[0]
        if not uscc_value:
            row = conn.execute(
                "SELECT uscc FROM firm_wide WHERE uscc IS NOT NULL LIMIT 1"
            ).fetchone()
            if not row:
                return {"found": False, "error": "no firm in firm_wide"}
            uscc_value = row[0]

        firm_row = conn.execute(
            "SELECT uscc, name, legal_rep, controller_name, ubo_name "
            "FROM firm_wide WHERE uscc = ?",
            [uscc_value],
        ).fetchone()
        if not firm_row:
            return {"found": False, "error": f"firm not found: {uscc_value}"}
        center_uscc, center_name, legal, controller, ubo = firm_row

        entities = [{"id": center_uscc, "name": center_name, "type": "enterprise"}]
        seen_ids: set = {center_uscc}
        relationships = []
        stats = {"equity_edges": 0, "executive_edges": 0,
                 "relation_edges": 0, "event_edges": 0, "hop_reached": 0}

        def _add_entity(eid: str, name: str, etype: str, **extra) -> str:
            if eid in seen_ids:
                # 已存在，尝试合并 role/severity 等属性
                for e in entities:
                    if e["id"] == eid:
                        for k, v in extra.items():
                            if v and (not e.get(k) or k in ("role", "severity")):
                                e[k] = v
                        break
                return eid
            ent = {"id": eid, "name": name, "type": etype}
            ent.update({k: v for k, v in extra.items() if v})
            entities.append(ent)
            seen_ids.add(eid)
            return eid

        def _safe_ratio(r):
            try:
                return float(r) if r is not None else 0.0
            except Exception:
                return 0.0

        def _person_id(name: str) -> str:
            return f"person_{name}"

        wanted_relations = set(relations.split(",")) if relations != "all" else None

        # ---- 1. 中心企业的法代/实控人/UBO（firm_wide 直接字段） ----
        if legal:
            _add_entity(_person_id(legal), legal, "person", role="legal_rep")
            relationships.append({
                "source": center_uscc, "target": _person_id(legal),
                "type": "has_legal_rep",
            })
        if controller:
            _add_entity(_person_id(controller), controller, "person", role="controller")
            relationships.append({
                "source": center_uscc, "target": _person_id(controller),
                "type": "has_controller",
            })
        if ubo:
            _add_entity(_person_id(ubo), ubo, "person", role="ubo")
            relationships.append({
                "source": center_uscc, "target": _person_id(ubo),
                "type": "has_ubo",
            })

        # ---- 2. equity_edge 多跳股权穿透 ----
        frontier = [center_uscc]
        visited_uscc = {center_uscc}
        for hop in range(depth):
            if not frontier:
                break
            frontier_usccs = list(frontier)
            frontier = []
            rows = conn.execute(
                "SELECT target_uscc, holder_uscc, holder_name, stake_ratio "
                "FROM equity_edge WHERE target_uscc IN (SELECT unnest(?)) "
                "OR holder_uscc IN (SELECT unnest(?))",
                [frontier_usccs, frontier_usccs],
            ).fetchall()
            for tu, hu, hn, ratio in rows:
                if not hu or not tu:
                    continue
                # 只保留涉及 frontier 的边
                if tu not in frontier_usccs and hu not in frontier_usccs:
                    continue
                _add_entity(tu, tu, "enterprise")
                _add_entity(hu, hn or hu, "enterprise")
                relationships.append({
                    "source": tu, "target": hu,
                    "type": "has_shareholder",
                    "attributes": {"ratio": _safe_ratio(ratio), "hop": hop + 1},
                })
                stats["equity_edges"] += 1
                for u in (tu, hu):
                    if u not in visited_uscc:
                        frontier.append(u)
                        visited_uscc.add(u)
        stats["hop_reached"] = min(depth, len(frontier) + 1 if frontier else depth)

        # ---- 3. executive 高管 ----
        if include_executive:
            for uscc_e in visited_uscc:
                ex_rows = conn.execute(
                    "SELECT name, role, is_active FROM executive WHERE uscc = ?",
                    [uscc_e],
                ).fetchall()
                for ex_name, ex_role, is_active in ex_rows:
                    if not ex_name:
                        continue
                    pid = _add_entity(
                        _person_id(ex_name), ex_name, "person",
                        role=ex_role or "", active=bool(is_active),
                    )
                    relationships.append({
                        "source": uscc_e, "target": pid,
                        "type": "has_executive",
                        "attributes": {"role": ex_role or "", "active": bool(is_active)},
                    })
                    stats["executive_edges"] += 1

        # ---- 4. firm_relation 其他关系（legal_rep/controller/ubo/executive/... 已由上面覆盖，
        #      这里补充非上述类型的边，避免重复） ----
        covered_types = {"has_legal_rep", "has_controller", "has_ubo",
                         "has_executive", "has_shareholder"}
        for uscc_f in visited_uscc:
            fr_rows = conn.execute(
                "SELECT subject_id, subject_name, subject_type, "
                "       object_uscc, object_name, relation_type, weight, direction "
                "FROM firm_relation "
                "WHERE subject_id = ? OR object_uscc = ?",
                [uscc_f, uscc_f],
            ).fetchall()
            for sid, sname, stype, oucc, oname, rtype, weight, direction in fr_rows:
                if rtype in covered_types:
                    continue
                if wanted_relations and rtype not in wanted_relations and rtype.lower() not in {r.lower() for r in wanted_relations}:
                    continue
                # subject 端
                if sid and stype == "person":
                    s_id = _person_id(sname or sid)
                    _add_entity(s_id, sname or sid, "person")
                elif sid and sid.startswith("913301") or (sid and stype in ("enterprise", "company")):
                    s_id = sid
                    _add_entity(sid, sname or sid, "enterprise")
                else:
                    s_id = sid
                    _add_entity(sid, sname or sid, stype or "unknown")
                # object 端
                if oucc:
                    o_id = oucc
                    _add_entity(oucc, oname or oucc, "enterprise")
                elif oname:
                    o_id = _person_id(oname)
                    _add_entity(o_id, oname, "person")
                else:
                    continue
                attributes = {}
                if weight is not None:
                    try:
                        attributes["weight"] = float(weight)
                    except Exception:
                        pass
                if direction:
                    attributes["direction"] = direction
                # firm_relation 的 subject 可能是 center，也可能是 object
                if uscc_f == sid and oucc == uscc_f:
                    pass  # 自环跳过
                elif sid == uscc_f:
                    relationships.append({
                        "source": sid, "target": o_id, "type": rtype,
                        "attributes": attributes,
                    })
                else:
                    relationships.append({
                        "source": o_id, "target": sid, "type": rtype,
                        "attributes": attributes,
                    })
                stats["relation_edges"] += 1

        # ---- 5. event_wide 风险事件（作为节点或自环边附加属性） ----
        if include_risk:
            for uscc_e in visited_uscc:
                ev_rows = conn.execute(
                    "SELECT id, event_type, event_subtype, severity, occurred_at, reason "
                    "FROM event_wide WHERE uscc = ? ORDER BY severity DESC, occurred_at DESC",
                    [uscc_e],
                ).fetchall()
                for eid, etype, esub, severity, occurred, reason in ev_rows:
                    if not eid:
                        continue
                    ev_id = f"event_{eid}"
                    label = f"[{severity or '-'}/{etype}]" + (f" {esub}" if esub else "")
                    _add_entity(ev_id, label, "event",
                                severity=severity or "", event_type=etype or "")
                    attributes = {"severity": severity or "", "type": etype or ""}
                    if esub:
                        attributes["subtype"] = esub
                    if reason:
                        attributes["reason"] = (reason or "")[:80]
                    relationships.append({
                        "source": uscc_e, "target": ev_id,
                        "type": "has_event",
                        "attributes": attributes,
                    })
                    stats["event_edges"] += 1

        return {
            "found": True,
            "target": target,
            "center_uscc": center_uscc,
            "center_name": center_name,
            "entities": entities,
            "relationships": relationships,
            "stats": stats,
        }
    finally:
        conn.close()


def semantica_visualize(kind: str = "kg",
                        target: str | None = None,
                        output: str = "html",
                        depth: int = 2,
                        include_risk: bool = True,
                        include_executive: bool = True,
                        relations: str = "all") -> dict:
    """用 semantica 原生可视化导出 HTML/PNG。

    kind:
      - "ontology"：本体结构图（TTL 类继承 + 属性连接），无 target 依赖
      - "kg"：目标企业的知识图谱（企业/自然人/事件 融合图）
    output:
      - "html"（默认）：自包含单文件，浏览器打开即可
      - "png"：静态图（需 matplotlib 后端）
    depth: 股权穿透最大跳数（默认 2）
    include_risk: 是否叠加 event_wide 风险事件节点（默认 True）
    include_executive: 是否叠加 executive 高管（默认 True）
    relations: firm_relation 关系类型子集，逗号分隔或 "all"

    返回:
      {ok, kind, output, file_path, file_size,
       nodes, edges, center_uscc, center_name, stats, error}
    """
    if not semantica_available():
        return {
            "ok": False, "semantica_available": False,
            "error": "semantica not installed",
            "hint": "uv pip install --python .venv\\Scripts\\python.exe semantica",
        }
    kind = (kind or "kg").lower()
    if kind not in ("ontology", "kg"):
        return {"ok": False, "error": f"unknown kind: {kind}",
                "allowed": ["ontology", "kg"]}
    try:
        from semantica.visualization import visualize_ontology, visualize_kg
    except Exception as e:
        return {"ok": False, "semantica_available": True,
                "error": f"import semantica.visualization failed: {type(e).__name__}: {e}"}

    out_dir = Path(__file__).resolve().parent.parent / "data" / "visualize"
    out_dir.mkdir(parents=True, exist_ok=True)

    if kind == "ontology":
        try:
            ttl_text = _load_turtle(ONTOLOGY_TTL)
        except Exception as e:
            return {"ok": False, "error": f"load ttl failed: {e}"}
        if not ttl_text:
            return {"ok": False, "error": "ontology ttl file not found or empty"}
        ont = _ttl_to_ontology_dict(ttl_text, "QCC")
        tag = "semantica_ontology"
        try:
            visualize_ontology(ont, output=output, file_path=str(out_dir / f"{tag}.{output}"))
        except Exception as e:
            return {"ok": False, "error": f"visualize_ontology failed: {type(e).__name__}: {e}"}
        fpath = out_dir / f"{tag}.{output}"
        return {
            "ok": True,
            "kind": "ontology",
            "output": output,
            "file_path": str(fpath),
            "file_size": fpath.stat().st_size if fpath.exists() else 0,
            "classes": len(ont.get("classes", [])),
            "properties": len(ont.get("properties", [])),
            "source": str(ONTOLOGY_TTL),
        }

    # kind == "kg"
    kg_dict = _build_semantica_kg_dict(
        target=target, depth=depth,
        include_risk=include_risk, include_executive=include_executive,
        relations=relations,
    )
    if not kg_dict.get("found"):
        return {"ok": False, "kind": "kg",
                "error": kg_dict.get("error") or "no firm found"}
    tag = "semantica_kg"
    name_part = ""
    if kg_dict.get("center_name"):
        name_part = "_" + (kg_dict.get("center_uscc") or "")[:6]
    try:
        visualize_kg(kg_dict, output=output,
                     file_path=str(out_dir / f"{tag}{name_part}.{output}"))
    except Exception as e:
        return {"ok": False, "kind": "kg",
                "error": f"visualize_kg failed: {type(e).__name__}: {e}"}
    fpath = out_dir / f"{tag}{name_part}.{output}"
    return {
        "ok": True,
        "kind": "kg",
        "output": output,
        "file_path": str(fpath),
        "file_size": fpath.stat().st_size if fpath.exists() else 0,
        "target": target,
        "center_uscc": kg_dict.get("center_uscc"),
        "center_name": kg_dict.get("center_name"),
        "nodes": len(kg_dict.get("entities", [])),
        "edges": len(kg_dict.get("relationships", [])),
        "stats": kg_dict.get("stats", {}),
        "options": {"depth": depth, "include_risk": include_risk,
                    "include_executive": include_executive, "relations": relations},
    }


# ==================== 跨企业 3 层穿透 + 社区发现 ====================

def _bfs_holdings(center_uscc: str, layers: int = 3, conn=None) -> dict:
    """BFS 向上走 equity_edge（holder→target 反向），最多 layers 层。

    返回：
      {
        "visited": [uscc, ...],   # 已访问企业（含 center）
        "layers": [               # 按层分组的 {uscc, name, holder, ratio}
            [{"uscc":..., "name":..., "layer":1, "holder":None, "ratio":None}, ...],
            [{"uscc":..., "name":..., "layer":2, "holder":"L1_USCC", "ratio":0.6}, ...],
            ...
        ],
        "edges": [                # 股东→目标 边（用于建图）
            {"source":"holder_uscc", "target":"uscc", "ratio":0.6, "holder_name":..., "target_name":...},
            ...
        ],
      }
    """
    own_conn = conn is None
    if own_conn:
        conn = _connect()
    try:
        visited = {center_uscc}
        current_layer_usccs = [center_uscc]
        all_layers = []
        all_edges = []
        center_name = ""

        for layer_idx in range(layers):
            next_layer = []
            for u in current_layer_usccs:
                rows = conn.execute(
                    "SELECT holder_uscc, holder_name, target_uscc, target_name, "
                    "       stake_ratio, holder_type FROM equity_edge WHERE target_uscc = ?",
                    [u]
                ).fetchall()
                for holder_uscc, holder_name, target_uscc, target_name, ratio, holder_type in rows:
                    if not holder_uscc:
                        continue
                    if holder_uscc in visited:
                        continue
                    # 查企业名
                    try:
                        fn = conn.execute(
                            "SELECT name FROM firm_wide WHERE uscc = ?", [holder_uscc]
                        ).fetchone()
                        hn = fn[0] if fn else (holder_name or holder_uscc)
                    except Exception:
                        hn = holder_name or holder_uscc

                    visited.add(holder_uscc)
                    tn = target_name or (
                        (conn.execute("SELECT name FROM firm_wide WHERE uscc = ?", [target_uscc]).fetchone() or ("",))[0]
                        or target_uscc
                    )
                    if u == center_uscc and not center_name:
                        center_name = tn

                    node = {
                        "uscc": holder_uscc,
                        "name": hn,
                        "layer": layer_idx + 1,
                        "holder": target_uscc,   # 谁持有它（父）
                        "ratio": float(ratio) if ratio is not None else None,
                        "holder_type": holder_type,
                    }
                    next_layer.append(node)
                    all_edges.append({
                        "source": holder_uscc,
                        "source_name": hn,
                        "target": target_uscc,
                        "target_name": tn,
                        "ratio": float(ratio) if ratio is not None else None,
                        "holder_type": holder_type,
                    })
            if not next_layer:
                break
            all_layers.append(next_layer)
            current_layer_usccs = [n["uscc"] for n in next_layer]

        return {
            "center_uscc": center_uscc,
            "visited": list(visited),
            "layers": all_layers,
            "edges": all_edges,
            "max_layer_reached": len(all_layers),
        }
    finally:
        if own_conn:
            conn.close()


def _resolve_center_name(center_uscc: str, conn) -> str:
    """从 firm_wide 查中心企业名称，失败返回 uscc 本身。"""
    try:
        row = conn.execute("SELECT name FROM firm_wide WHERE uscc = ?", [center_uscc]).fetchone()
        return row[0] if row else center_uscc
    except Exception:
        return center_uscc


# 关系类型集合
_RELATION_ALL = ("equity", "legal_rep", "controller", "ubo", "executive")


def _table_exists(conn, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone()
        return row[0] > 0 if row else False
    except Exception:
        return False


def _build_multi_relation_graph(
    center_uscc: str,
    layers: int = 3,
    relations: str = "all",
    min_ratio: float = 0.0,
    conn=None,
) -> dict:
    """多关系 BFS 穿透：从中心企业出发，按 relation 集合向上收集节点和边。

    优先从统一边表 firm_relation 读取（若存在）；否则回退到分散的
    equity_edge / firm_wide / executive 表。

    支持的关系类型：
      equity      - hasShareholder (firm→firm)
      legal_rep   - hasLegalRep    (person→firm)
      controller  - hasController  (person→firm)
      ubo         - hasUBO         (person→firm)
      executive   - hasExecutive   (person→firm)

    方向：
      equity 边      : holder → target  (企业→企业，holder 侧继续向上传)
      其他关系边     : person → firm   (自然人是叶子，不会继续向上)

    返回:
      {
        center_uscc, center_name,
        visited_nodes: [node_id, ...],
        layers: [[node, ...], ...],
        edges: [edge, ...],
        max_layer_reached: int,
        relation_types_used: [str, ...],
        source: 'firm_relation' | 'legacy',
      }
    """
    own_conn = conn is None
    if own_conn:
        conn = _connect()
    try:
        # 解析 relations 参数
        if relations == "all":
            rel_set = set(_RELATION_ALL)
        elif relations == "equity":
            rel_set = {"equity"}
        elif relations in _RELATION_ALL:
            rel_set = {relations}
        else:
            rel_set = set(relations.split(",")) if isinstance(relations, str) else set(relations)
        rel_set = {r for r in rel_set if r in _RELATION_ALL}
        if not rel_set:
            rel_set = set(_RELATION_ALL)

        # 中心节点
        center_name = _resolve_center_name(center_uscc, conn)

        # 优先走统一边表
        if _table_exists(conn, "firm_relation"):
            return _bfs_via_firm_relation(
                center_uscc, center_name, layers, rel_set, min_ratio, conn
            )
        # 回退到旧的多表查询路径
        return _bfs_via_legacy_tables(
            center_uscc, center_name, layers, rel_set, min_ratio, conn
        )
    finally:
        if own_conn:
            conn.close()


def _bfs_via_firm_relation(
    center_uscc, center_name, layers, rel_set, min_ratio, conn
) -> dict:
    """通过统一边表 firm_relation 做 BFS。"""
    visited = {center_uscc}
    all_layers = []
    all_edges = []
    current_firms = [center_uscc]

    # 一次把该中心企业相关的所有边取出来，避免每层反复查
    rows = conn.execute(
        "SELECT subject_id, subject_name, subject_type, object_uscc, object_name, "
        "       relation_type, edge_type, direction, weight, role "
        "FROM firm_relation",
    ).fetchall()

    # 按目标企业建索引：object_uscc → [(subject_id, subject_name, subject_type, rel, edge_type, weight, role)]
    by_target: dict = {}
    for (sid, sname, stype, o_uscc, o_name, rtype, etype, dirc, w, role) in rows:
        by_target.setdefault(o_uscc, []).append(
            (sid, sname, stype, rtype, etype, w, role)
        )

    for layer_idx in range(layers):
        next_nodes = []
        next_firms = []
        seen_this_layer = set()

        for uscc in current_firms:
            edges = by_target.get(uscc, [])
            for (sid, sname, stype, rtype, etype, w, role) in edges:
                if rtype not in rel_set:
                    continue
                # min_ratio 仅过滤 equity
                if rtype == "equity" and min_ratio > 0:
                    try:
                        ww = float(w) if w is not None else 0.0
                    except (TypeError, ValueError):
                        ww = 0.0
                    if ww < min_ratio:
                        continue

                # subject 可能是企业（equity）或自然人（其他）
                if stype == "Person":
                    if sid in visited:
                        continue
                    visited.add(sid)
                else:
                    if sid in visited:
                        continue
                    visited.add(sid)

                if sid in seen_this_layer:
                    # 同一层内该节点已收录（例如陈海斌同时是法代 + 高管）
                    continue
                seen_this_layer.add(sid)

                next_nodes.append({
                    "id": sid,
                    "type": stype,
                    "name": sname,
                    "layer": layer_idx + 1,
                    "relation": etype,
                    "role": role if rtype == "executive" else None,
                    "ratio": float(w) if (rtype == "equity" and w is not None) else None,
                    "stake": float(w) if (rtype in ("controller", "ubo") and w is not None) else None,
                })
                all_edges.append({
                    "source": sid,
                    "target": uscc,
                    "type": etype,
                    "weight": float(w) if w is not None else 1.0,
                    "label": f"{sname} → {center_name if uscc == center_uscc else _resolve_center_name(uscc, conn)}",
                    "role": role if rtype == "executive" else None,
                    "holder_type": stype,
                })
                # 只有企业节点继续向上
                if stype == "Enterprise":
                    next_firms.append(sid)

        if not next_nodes:
            break
        all_layers.append(next_nodes)
        current_firms = next_firms

    return {
        "center_uscc": center_uscc,
        "center_name": center_name,
        "visited_nodes": list(visited),
        "layers": all_layers,
        "edges": all_edges,
        "max_layer_reached": len(all_layers),
        "relation_types_used": sorted(rel_set),
        "source": "firm_relation",
    }


def _bfs_via_legacy_tables(
    center_uscc, center_name, layers, rel_set, min_ratio, conn
) -> dict:
    """回退路径：直接从 equity_edge / firm_wide / executive 三张表取数。"""
    visited = {center_uscc}
    all_layers = []
    all_edges = []
    current_firms = [center_uscc]

    for layer_idx in range(layers):
        next_nodes = []
        next_firms = []
        seen_this_layer = set()

        for uscc in current_firms:
            firm_name = _resolve_center_name(uscc, conn)

            def _add_person(pname, etype, rel_key, extra=None):
                pid = f"person:{pname}"
                if pid in visited or pid in seen_this_layer:
                    return
                visited.add(pid)
                seen_this_layer.add(pid)
                node = {
                    "id": pid, "type": "Person", "name": pname,
                    "layer": layer_idx + 1, "relation": etype,
                }
                if extra:
                    node.update(extra)
                next_nodes.append(node)
                all_edges.append({
                    "source": pid, "target": uscc, "type": etype, "weight": 1.0,
                    "label": f"{pname} → {firm_name}",
                })

            # --- 股权穿透 ---
            if "equity" in rel_set:
                try:
                    rows = conn.execute(
                        "SELECT holder_uscc, holder_name, target_uscc, target_name, "
                        "       stake_ratio, holder_type FROM equity_edge "
                        "WHERE target_uscc = ?", [uscc]
                    ).fetchall()
                except Exception:
                    rows = []
                for h_uscc, h_name, t_uscc, t_name, ratio, h_type in rows:
                    if not h_uscc or h_uscc in visited:
                        continue
                    r = float(ratio) if ratio is not None else None
                    if min_ratio > 0 and (r or 0) < min_ratio:
                        continue
                    visited.add(h_uscc)
                    holder_name = h_name or _resolve_center_name(h_uscc, conn)
                    next_nodes.append({
                        "id": h_uscc, "type": "Enterprise", "name": holder_name,
                        "layer": layer_idx + 1, "relation": "hasShareholder",
                        "ratio": r,
                    })
                    all_edges.append({
                        "source": h_uscc, "target": t_uscc, "type": "hasShareholder",
                        "weight": r, "label": f"{holder_name} → {firm_name}",
                        "holder_type": h_type,
                    })
                    next_firms.append(h_uscc)

            # --- 法定代表人 ---
            if "legal_rep" in rel_set:
                try:
                    row = conn.execute(
                        "SELECT legal_rep FROM firm_wide WHERE uscc = ?", [uscc]
                    ).fetchone()
                    if row and row[0]:
                        _add_person(row[0], "hasLegalRep", "legal_rep")
                except Exception:
                    pass

            # --- 实控人 ---
            if "controller" in rel_set:
                try:
                    row = conn.execute(
                        "SELECT controller_name, controller_stake FROM firm_wide WHERE uscc = ?", [uscc]
                    ).fetchone()
                    if row and row[0]:
                        stake = float(row[1]) if row[1] is not None else None
                        _add_person(row[0], "hasController", "controller", {"stake": stake})
                except Exception:
                    pass

            # --- UBO ---
            if "ubo" in rel_set:
                try:
                    row = conn.execute(
                        "SELECT ubo_name, ubo_stake FROM firm_wide WHERE uscc = ?", [uscc]
                    ).fetchone()
                    if row and row[0]:
                        stake = float(row[1]) if row[1] is not None else None
                        _add_person(row[0], "hasUBO", "ubo", {"stake": stake})
                except Exception:
                    pass

            # --- 高管 ---
            if "executive" in rel_set:
                try:
                    rows = conn.execute(
                        "SELECT name, role FROM executive WHERE uscc = ? AND name IS NOT NULL",
                        [uscc]
                    ).fetchall()
                except Exception:
                    rows = []
                for ex_name, ex_role in rows:
                    if not ex_name:
                        continue
                    pid = f"person:{ex_name}"
                    if pid in visited or pid in seen_this_layer:
                        continue
                    visited.add(pid)
                    seen_this_layer.add(pid)
                    next_nodes.append({
                        "id": pid, "type": "Person", "name": ex_name,
                        "layer": layer_idx + 1, "relation": "hasExecutive",
                        "role": ex_role,
                    })
                    all_edges.append({
                        "source": pid, "target": uscc, "type": "hasExecutive",
                        "weight": 1.0, "label": f"{ex_name} → {firm_name}",
                        "role": ex_role,
                    })

        if not next_nodes:
            break
        all_layers.append(next_nodes)
        current_firms = [n["id"] for n in next_nodes if not n["id"].startswith("person:")]

    return {
        "center_uscc": center_uscc,
        "center_name": center_name,
        "visited_nodes": list(visited),
        "layers": all_layers,
        "edges": all_edges,
        "max_layer_reached": len(all_layers),
        "relation_types_used": sorted(rel_set),
        "source": "legacy",
    }


def _extract_graph_to_semantica(graph_result: dict) -> tuple:
    """把 _build_multi_relation_graph 的结果转成 semantica (entities, relationships)。

    entities: [{id, type: 'Enterprise'|'Person', name, layer, ...}]
    relationships: [{source, target, type, weight, label?}]
    """
    entities = []
    seen = set()

    # center 节点
    entities.append({
        "id": graph_result["center_uscc"],
        "type": "Enterprise",
        "name": graph_result["center_name"],
        "layer": 0,
    })
    seen.add(graph_result["center_uscc"])

    # 各层节点
    for layer in graph_result["layers"]:
        for node in layer:
            nid = node["id"]
            if nid in seen:
                continue
            seen.add(nid)
            entity = {
                "id": nid,
                "type": node["type"],
                "name": node["name"],
                "layer": node["layer"],
                "relation": node.get("relation"),
            }
            if node.get("ratio") is not None:
                entity["ratio"] = node["ratio"]
            if node.get("stake") is not None:
                entity["stake"] = node["stake"]
            if node.get("role"):
                entity["role"] = node["role"]
            entities.append(entity)

    # 边
    relationships = []
    for e in graph_result["edges"]:
        relationships.append({
            "source": e["source"],
            "target": e["target"],
            "type": e["type"],
            "weight": e.get("weight") if e.get("weight") is not None else 1.0,
        })

    return entities, relationships



def _extract_enterprises_only(bfs_result: dict) -> tuple:
    """把 BFS 结果转成 semantica 需要的 (entities, relationships)。

    entities: [{id: uscc, type: 'Enterprise', name, layer, ratio}]
    relationships: [{source: holder_uscc, target: target_uscc, type: 'hasShareholder', weight: ratio}]
    """
    entities = []
    seen = set()

    # center 节点
    center = {
        "id": bfs_result["center_uscc"],
        "type": "Enterprise",
        "name": "",
        "layer": 0,
    }
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT name FROM firm_wide WHERE uscc = ?", [bfs_result["center_uscc"]]
        ).fetchone()
        center["name"] = row[0] if row else bfs_result["center_uscc"]
        conn.close()
    except Exception:
        center["name"] = bfs_result["center_uscc"]
    entities.append(center)
    seen.add(center["id"])

    # 各层节点
    for layer in bfs_result["layers"]:
        for node in layer:
            nid = node["uscc"]
            if nid in seen:
                continue
            seen.add(nid)
            entities.append({
                "id": nid,
                "type": "Enterprise",
                "name": node["name"],
                "layer": node["layer"],
                "ratio": node["ratio"],
            })

    # 边
    relationships = []
    for e in bfs_result["edges"]:
        relationships.append({
            "source": e["source"],
            "target": e["target"],
            "type": "hasShareholder",
            "weight": e["ratio"],
        })

    return entities, relationships


def _normalize_analysis(raw: dict) -> dict:
    """semantica GraphAnalyzer.analyze() 的返回结构比较嵌套，展平成便于前端消费的结构。"""
    out = {}

    # centrality → degree.centrality + degree.rankings
    cent = raw.get("centrality", {})
    degree_block = cent.get("centrality_measures", {}).get("degree", {})
    out["centrality_scores"] = degree_block.get("centrality", {})
    out["centrality_rankings"] = degree_block.get("rankings", [])
    out["centrality_max_degree"] = degree_block.get("max_degree")

    # communities → 平铺 node_assignments + modularity
    comm = raw.get("communities", {})
    out["node_community"] = comm.get("node_assignments", {})
    out["modularity"] = comm.get("modularity")
    out["num_communities"] = len(comm.get("communities", []))
    out["algorithm"] = comm.get("algorithm")

    # connectivity 原样保留
    out["connectivity"] = raw.get("connectivity", {})
    out["metrics"] = raw.get("metrics", {})
    return out


def traverse_and_analyze(
    target: str,
    layers: int = 3,
    use_owlrl: bool = False,
    min_ratio: float = 0.0,
    relations: str = "all",
) -> dict:
    """跨企业 N 层穿透 + 社区发现 + 中心性分析（多关系版）。

    参数:
      target:    中心企业 USCC / 名称
      layers:    最大穿透层数（默认 3，范围 1-6）
      use_owlrl: 保留参数（穿透依赖 DuckDB 关系，owlrl 不参与）
      min_ratio: 过滤持股比例小于该值的 equity 边（默认 0.0 不过滤）
      relations: 穿透的关系集合，可选值：
                  "all"       - equity + legal_rep + controller + ubo + executive
                  "equity"    - 仅走股权（旧行为）
                  单关系名    - "legal_rep" / "controller" / "ubo" / "executive"
                  逗号分隔    - "equity,legal_rep,executive"

    返回:
      {
        ok, target, center_uscc, center_name,
        layers_reached,          # 实际到达层数
        layers,                  # 每层节点列表（含 id/type/name/layer/relation/...）
        edges,                   # 所有穿透边（含 type/weight）
        stats,                   # {total_nodes, total_enterprises, total_persons,
                                 #  total_edges, layers: [{n, n_enterprise, n_person, ratio_sum}]}
        entities, relationships, # semantica 数据模型（含 Enterprise + Person 类型）
        analysis,                # {centrality_scores, centrality_rankings, node_community, modularity, ...}
        top_hubs,                # 中心度 Top 5（含 Person + Enterprise）
        communities_summary,     # [{id, size, members, contains_center}]
        relation_types_used,     # 实际启用的关系类型
        semantica_available,
      }
    """
    layers = max(1, min(6, int(layers)))
    hit = _resolve_target(target)
    if not hit:
        return {"ok": False, "error": f"target not found: {target}"}
    center_uscc = hit["uscc"]
    center_name = hit["name"]

    # 1) 多关系 BFS 穿透
    graph = _build_multi_relation_graph(
        center_uscc,
        layers=layers,
        relations=relations,
        min_ratio=min_ratio,
        conn=None,
    )

    if graph["max_layer_reached"] == 0 and not graph["edges"]:
        return {
            "ok": True,
            "target": target,
            "center_uscc": center_uscc,
            "center_name": center_name,
            "layers_reached": 0,
            "layers": [],
            "edges": [],
            "stats": {"total_nodes": 1, "total_enterprises": 1, "total_persons": 0, "total_edges": 0, "layers": []},
            "entities": [], "relationships": [],
            "analysis": {}, "top_hubs": [], "communities_summary": [],
            "relation_types_used": graph.get("relation_types_used", []),
            "semantica_available": semantica_available(),
            "note": "中心企业没有可穿透的关系边（可能是数据缺失或指定关系类型未命中）",
        }

    # 2) 转成 semantica entities / relationships（含 Person 节点）
    entities, relationships = _extract_graph_to_semantica(graph)

    # 4) semantica 分析（可选）
    analysis = {}
    semantica_ok = False
    semantica_err = None
    top_hubs = []
    communities_summary = []

    if semantica_available():
        try:
            from semantica.kg import KnowledgeGraph, GraphAnalyzer
            kg = KnowledgeGraph(entities=entities, relationships=relationships)
            raw = GraphAnalyzer().analyze(kg)
            analysis = _normalize_analysis(raw)

            # Top hubs（中心度排名）
            ent_by_id = {e["id"]: e for e in entities}
            for item in analysis.get("centrality_rankings", [])[:5]:
                nid = item["node"]
                ent = ent_by_id.get(nid, {})
                top_hubs.append({
                    "id": nid,
                    "name": ent.get("name", nid),
                    "type": ent.get("type", "Enterprise"),
                    "score": item["score"],
                })

            # Communities 摘要
            node_community = analysis.get("node_community", {})
            groups = {}
            for nid, cid in node_community.items():
                groups.setdefault(cid, []).append(nid)
            for cid, members in groups.items():
                communities_summary.append({
                    "id": cid,
                    "size": len(members),
                    "members": [
                        {
                            "id": m,
                            "name": ent_by_id.get(m, {}).get("name", m),
                            "type": ent_by_id.get(m, {}).get("type", "Enterprise"),
                        }
                        for m in members
                    ],
                    "contains_center": center_uscc in members,
                })
            communities_summary.sort(key=lambda x: (-x["size"], x["id"]))
            semantica_ok = True
        except Exception as e:
            semantica_err = f"{type(e).__name__}: {e}"

    # 5) 层级统计（含 enterprise/person 分布）
    layer_stats = []
    for i, layer in enumerate(graph["layers"], start=1):
        ratios = [n["ratio"] for n in layer if n.get("ratio") is not None]
        n_ent = sum(1 for n in layer if n["type"] == "Enterprise")
        n_per = sum(1 for n in layer if n["type"] == "Person")
        layer_stats.append({
            "layer": i,
            "n": len(layer),
            "n_enterprise": n_ent,
            "n_person": n_per,
            "ratio_sum": round(sum(ratios), 4) if ratios else None,
        })

    total_ent = sum(1 for e in entities if e.get("type") == "Enterprise")
    total_per = sum(1 for e in entities if e.get("type") == "Person")

    return {
        "ok": True,
        "target": target,
        "center_uscc": center_uscc,
        "center_name": center_name,
        "layers_reached": graph["max_layer_reached"],
        "layers": graph["layers"],
        "edges": graph["edges"],
        "stats": {
            "total_nodes": len(entities),
            "total_enterprises": total_ent,
            "total_persons": total_per,
            "total_edges": len(relationships),
            "layers": layer_stats,
        },
        "entities": entities,
        "relationships": relationships,
        "analysis": analysis,
        "top_hubs": top_hubs,
        "communities_summary": communities_summary,
        "relation_types_used": graph.get("relation_types_used", []),
        "semantica_available": semantica_ok,
        "semantica_error": semantica_err,
    }


def infer_visualize(graph_data: dict, use_owlrl: bool = True) -> dict:
    """运行 infer_risk 并把结果转换为「推理链图」：Rule → Finding → Conclusion。

    返回:
      {
        ok, center_uscc, center_name, conform, rule_count, finding_count,
        severity_counts, owlrl,
        elements: {nodes, edges},
        findings: [...]  # 供侧栏展示
      }

    节点类型: rule / finding / conclusion / owlrl
    边: rule→finding(应用), finding→conclusion(贡献), owlrl→conclusion(元数据)
    """
    if not graph_data.get("found"):
        return {"ok": False, "error": "graph_data.found is false"}

    result = infer_risk(graph_data, use_owlrl=use_owlrl)
    findings = result.get("findings") or []
    owlrl_meta = result.get("owlrl") or {}
    conform = bool(result.get("conform"))
    center_uscc = graph_data.get("uscc") or ""
    center_name = (graph_data.get("firm") or {}).get("name") or center_uscc

    # 1) 规则分类
    rule_bucket = {}   # code -> {code, label, findings: [...], severities: set}
    for f in findings:
        code = f.get("rule", "?")
        b = rule_bucket.setdefault(code, {"code": code, "findings": [], "severities": set()})
        b["findings"].append(f)
        b["severities"].add((f.get("severity") or "LOW").upper())

    def _rule_label(code: str) -> str:
        if code.startswith("R1"): return "R1 一票否决"
        if code.startswith("R4"): return "R4 股东风险传导"
        if code.startswith("R7"): return "R7 系统性风险主体"
        if code.startswith("R10"): return "R10 关联网络密度"
        if code.startswith("R12"): return "R12 法代/地址变更"
        return code

    def _rule_severity(sevs: set) -> str:
        order = ["HIGH", "MEDIUM", "LOW", "INFO"]
        for s in order:
            if s in sevs:
                return s
        return "INFO"

    # 2) 严重度统计
    sev_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}
    for f in findings:
        s = (f.get("severity") or "LOW").upper()
        sev_counts[s] = sev_counts.get(s, 0) + 1

    # 3) 建节点表
    nodes = []
    edges = []

    # 3a) 结论节点（唯一）
    conclusion_severity = "HIGH" if not conform else "LOW"
    conclusion_label = "不符合本体约束" if not conform else "全部规则符合"
    nodes.append({"data": {
        "id": "N:conclusion", "type": "conclusion", "severity": conclusion_severity,
        "label": f"结论 · {conclusion_label}", "conform": conform,
        "detail": f"共 {len(findings)} 条 finding；HIGH={sev_counts['HIGH']}，MEDIUM={sev_counts['MEDIUM']}，LOW={sev_counts['LOW']}，INFO={sev_counts['INFO']}",
    }})

    # 3b) owlrl 元数据节点
    if owlrl_meta.get("available"):
        owlrl_label = f"owlrl 闭包\n+{owlrl_meta.get('new_triples', 0)} 三元组"
    else:
        owlrl_label = "owlrl 未启用"
    nodes.append({"data": {
        "id": "N:owlrl", "type": "owlrl", "severity": "INFO",
        "label": owlrl_label,
        "detail": f"data_triples={owlrl_meta.get('data_triples', '?')}  new={owlrl_meta.get('new_triples', '?')}  closed={owlrl_meta.get('closed_triples', '?')}",
    }})

    # 3c) 规则节点 + Rule→Finding 边
    for code, b in rule_bucket.items():
        rule_node = f"N:rule:{code}"
        r_sev = _rule_severity(b["severities"])
        nodes.append({"data": {
            "id": rule_node, "type": "rule", "severity": r_sev,
            "label": f"{_rule_label(code)}\n({len(b['findings'])} 条)",
            "detail": f"规则 {code}；命中 finding {len(b['findings'])} 条；最高严重度 {r_sev}",
        }})
        # 3d) Finding 节点
        for i, f in enumerate(b["findings"], 1):
            f_id = f"N:finding:{code}:{i}"
            f_sev = (f.get("severity") or "LOW").upper()
            detail = f.get("detail") or ""
            f_conform = bool(f.get("conform", True))
            # 长 detail 截断（label 只用短串，detail 完整保留）
            short = detail if len(detail) <= 40 else detail[:40] + "…"
            label = f"[{f_sev}] {short}"
            nodes.append({"data": {
                "id": f_id, "type": "finding", "severity": f_sev,
                "label": label, "detail": f"规则：{code}\n严重度：{f_sev}\n结论：{'符合' if f_conform else '违反'}\n\n{detail}",
                "conform": f_conform,
            }})
            edges.append({"data": {
                "id": f"E:rule:{code}->{f_id}",
                "source": rule_node, "target": f_id,
                "label": "应用", "prop": "applies",
            }})
            edges.append({"data": {
                "id": f"E:finding:{f_id}->conclusion",
                "source": f_id, "target": "N:conclusion",
                "label": "违反" if not f_conform else "满足",
                "prop": "violates" if not f_conform else "satisfies",
                "conform": f_conform,
            }})

    # 无 findings 时给结论加一个占位 finding，避免孤立节点
    if not findings:
        nodes.append({"data": {
            "id": "N:finding:PASS:1", "type": "finding", "severity": "INFO",
            "label": "[INFO] 全部规则通过",
            "detail": "infer_risk 未产生任何 finding，全部 6 条核心规则 (R1/R4/R7/R10/R12) 判定为符合或跳过",
            "conform": True,
        }})
        edges.append({"data": {
            "id": "E:finding:PASS->conclusion",
            "source": "N:finding:PASS:1", "target": "N:conclusion",
            "label": "满足", "prop": "satisfies", "conform": True,
        }})

    # 3e) owlrl → 结论 边（元数据箭头）
    edges.append({"data": {
        "id": "E:owlrl->conclusion",
        "source": "N:owlrl", "target": "N:conclusion",
        "label": "基于", "prop": "based_on",
    }})

    return {
        "ok": True,
        "center_uscc": center_uscc,
        "center_name": center_name,
        "conform": conform,
        "rule_count": len(rule_bucket),
        "finding_count": len(findings),
        "severity_counts": sev_counts,
        "owlrl": owlrl_meta,
        "elements": {"nodes": nodes, "edges": edges},
        "findings": findings,
    }


def render_infer_html(iv_data: dict, out_path: Path | str | None = None) -> dict:
    """渲染 /kg-infer 结果可视化 HTML（推理链图：Rule→Finding→Conclusion）。"""
    import json as _json
    from datetime import datetime

    if not iv_data.get("ok"):
        return {"ok": False, "error": iv_data.get("error", "bad iv_data")}

    center_name = iv_data.get("center_name") or "?"
    center_uscc = iv_data.get("center_uscc") or "?"
    n_nodes = len(iv_data["elements"]["nodes"])
    n_edges = len(iv_data["elements"]["edges"])
    n_findings = iv_data.get("finding_count", 0)
    conform = iv_data.get("conform", True)
    sev = iv_data.get("severity_counts", {}) or {}
    owlrl_meta = iv_data.get("owlrl") or {}
    owlrl_status = (f"+{owlrl_meta.get('new_triples', 0)} 三元组"
                    if owlrl_meta.get("available") else "未启用")

    elements_json = _json.dumps(iv_data["elements"], ensure_ascii=False).replace("</", "<\\/")
    findings_json = _json.dumps(iv_data.get("findings") or [], ensure_ascii=False).replace("</", "<\\/")

    conform_color = "#ff6666" if not conform else "#66cc99"
    conform_text = "不符合" if not conform else "符合"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>qcc-super 推理链 — {center_name} ({center_uscc})</title>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
<style>
  html,body{{margin:0;padding:0;height:100%;font-family:"Microsoft YaHei","PingFang SC",sans-serif;background:#0f1117;color:#e6e8ee;display:flex;flex-direction:column;}}
  header{{padding:12px 20px;background:#161a22;border-bottom:1px solid #2a2f3a;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-shrink:0;}}
  header h1{{margin:0;font-size:16px;font-weight:600;}}
  header .meta{{font-size:12px;color:#8892a6;display:flex;gap:12px;flex-wrap:wrap;}}
  header .meta span{{margin-right:4px;}}
  header .conform{{padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;color:#0f1117;background:{conform_color};}}
  #body{{display:flex;flex:1;min-height:0;}}
  #cy{{flex:1;min-width:0;}}
  aside{{width:320px;background:#12151c;border-left:1px solid #2a2f3a;padding:12px;overflow-y:auto;flex-shrink:0;}}
  aside h3{{margin:0 0 8px;font-size:13px;color:#e6e8ee;}}
  aside .item{{padding:8px;border:1px solid #2a2f3a;border-radius:6px;margin-bottom:8px;font-size:12px;background:#0f1117;}}
  aside .item .badge{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;margin-right:6px;}}
  aside .item .badge.HIGH{{background:#ff6666;color:#fff;}}
  aside .item .badge.MEDIUM{{background:#ffcc66;color:#0f1117;}}
  aside .item .badge.LOW{{background:#66cc99;color:#0f1117;}}
  aside .item .badge.INFO{{background:#8892a6;color:#0f1117;}}
  aside .item .rule{{color:#c4b5fd;font-weight:600;}}
  aside .item .detail{{color:#c8cdd6;margin-top:4px;white-space:pre-wrap;}}
  #legend{{padding:8px 20px;background:#12151c;border-top:1px solid #2a2f3a;font-size:12px;display:flex;gap:20px;flex-wrap:wrap;flex-shrink:0;}}
  #legend .item{{display:flex;align-items:center;gap:6px;}}
  #legend .dot{{width:12px;height:12px;border-radius:50%;display:inline-block;}}
  #legend .line{{width:24px;height:2px;display:inline-block;}}
</style>
</head>
<body>
<header>
  <h1>qcc-super 推理链可视化</h1>
  <div class="meta">
    <span>目标：<b style="color:#66cc99">{center_name}</b> ({center_uscc})</span>
    <span>findings {n_findings}</span>
    <span>规则 {iv_data.get('rule_count', 0)}</span>
    <span>owlrl {owlrl_status}</span>
    <span class="conform">{conform_text}</span>
  </div>
</header>
<div id="body">
  <div id="cy"></div>
  <aside>
    <h3>Findings ({n_findings})</h3>
    <div id="findings"></div>
  </aside>
</div>
<div id="legend">
  <div class="item"><span class="dot" style="background:#c4b5fd"></span>规则 (Rule)</div>
  <div class="item"><span class="dot" style="background:#ffcc66"></span>Finding (INFO/LOW)</div>
  <div class="item"><span class="dot" style="background:#ff6666"></span>Finding (HIGH)</div>
  <div class="item"><span class="dot" style="background:#66cc99"></span>结论 (符合)</div>
  <div class="item"><span class="dot" style="background:#ff6666"></span>结论 (违反)</div>
  <div class="item"><span class="line" style="background:#8892a6"></span>应用</div>
  <div class="item"><span class="line" style="background:#ff6666"></span>违反</div>
  <div class="item"><span class="line" style="background:#66cc99"></span>满足</div>
</div>
<script>
const elements = {elements_json};
const findings = {findings_json};
const style = [
  {{ selector: 'node', style: {{
    'label': 'data(label)',
    'font-size': '10px',
    'color': '#e6e8ee',
    'text-valign': 'center',
    'text-halign': 'center',
    'text-wrap': 'wrap',
    'text-max-width': '140px',
    'text-outline-color': '#0f1117',
    'text-outline-width': 2,
    'background-color': '#8892a6',
    'border-width': 1,
    'border-color': '#ffffff33',
    'shape': 'ellipse',
    'width': 40,
    'height': 40,
  }} }},
  {{ selector: 'node[type="rule"]', style: {{
    'background-color': '#c4b5fd', 'shape': 'round-rectangle',
    'width': 72, 'height': 46, 'font-size': '11px', 'font-weight': 'bold',
    'color': '#0f1117',
  }} }},
  {{ selector: 'node[type="rule"][severity="HIGH"]', style: {{
    'background-color': '#ff88cc', 'border-color': '#ff4488',
  }} }},
  {{ selector: 'node[type="rule"][severity="MEDIUM"]', style: {{
    'background-color': '#ffb066', 'border-color': '#ff9044',
  }} }},
  {{ selector: 'node[type="finding"]', style: {{
    'background-color': '#ffcc66', 'shape': 'diamond',
    'width': 62, 'height': 62, 'font-size': '10px', 'color': '#0f1117',
  }} }},
  {{ selector: 'node[type="finding"][severity="HIGH"]', style: {{
    'background-color': '#ff6666', 'border-width': 3, 'border-color': '#ffccff',
    'width': 78, 'height': 78, 'z-index': 5,
  }} }},
  {{ selector: 'node[type="finding"][severity="MEDIUM"]', style: {{
    'background-color': '#ff9944', 'border-width': 2, 'border-color': '#ffcc88',
    'width': 68, 'height': 68,
  }} }},
  {{ selector: 'node[type="finding"][severity="LOW"]', style: {{
    'background-color': '#66cc99',
  }} }},
  {{ selector: 'node[type="finding"][severity="INFO"]', style: {{
    'background-color': '#8892a6',
  }} }},
  {{ selector: 'node[type="conclusion"][conform="true"]', style: {{
    'background-color': '#66cc99', 'shape': 'hexagon',
    'width': 110, 'height': 76, 'font-size': '13px', 'font-weight': 'bold',
    'border-width': 3, 'border-color': '#ffffff',
    'color': '#0f1117', 'z-index': 10,
  }} }},
  {{ selector: 'node[type="conclusion"][conform="false"]', style: {{
    'background-color': '#ff6666', 'shape': 'hexagon',
    'width': 110, 'height': 76, 'font-size': '13px', 'font-weight': 'bold',
    'border-width': 3, 'border-color': '#ffffff',
    'color': '#ffffff', 'z-index': 10,
  }} }},
  {{ selector: 'node[type="owlrl"]', style: {{
    'background-color': '#8892a6', 'shape': 'parallelogram',
    'width': 88, 'height': 44, 'font-size': '10px',
  }} }},
  {{
    selector: 'edge',
    style: {{
      'width': 1.4,
      'line-color': '#555c6b',
      'curve-style': 'bezier',
      'target-arrow-shape': 'triangle',
      'target-arrow-color': '#555c6b',
      'target-arrow-size': 8,
      'label': 'data(label)',
      'font-size': '9px',
      'color': '#8892a6',
      'text-opacity': 0.7,
      'text-outline-color': '#0f1117',
      'text-outline-width': 2,
      'edge-text-rotation': 'none',
    }}
  }},
  {{ selector: 'edge[prop="applies"]',   style: {{ 'line-color': '#8892a6', 'target-arrow-color': '#8892a6' }} }},
  {{ selector: 'edge[prop="violates"]',  style: {{ 'line-color': '#ff6666', 'target-arrow-color': '#ff6666', 'width': 2 }} }},
  {{ selector: 'edge[prop="satisfies"]', style: {{ 'line-color': '#66cc99', 'target-arrow-color': '#66cc99' }} }},
  {{ selector: 'edge[prop="based_on"]',  style: {{ 'line-color': '#8892a6', 'target-arrow-color': '#8892a6', 'line-style': 'dotted' }} }},
  {{ selector: 'node:selected, edge:selected', style: {{
    'border-width': 3, 'border-color': '#ffffff', 'z-index': 10,
    'line-color': '#ffffff', 'target-arrow-color': '#ffffff',
    'text-opacity': 1,
  }} }},
  {{ selector: 'node:unselected, edge:unselected', style: {{
    'opacity': 0.4, 'text-opacity': 0.25,
  }} }},
];
const cy = cytoscape({{
  container: document.getElementById('cy'),
  elements: elements,
  style: style,
  layout: {{ name: 'cose', animate: false, padding: 50, nodeRepulsion: 12000, idealEdgeLength: 130, edgeElasticity: 180 }},
  wheelSensitivity: 0.25,
  minZoom: 0.3,
}});
// 侧栏渲染 findings
const findingsBox = document.getElementById('findings');
if(findings.length === 0){{
  findingsBox.innerHTML = '<div class="item" style="color:#8892a6;">暂无 finding — 全部规则符合或跳过</div>';
}} else {{
  findingsBox.innerHTML = findings.map(f =>
    '<div class="item">' +
      '<span class="badge ' + (f.severity||'INFO') + '">' + (f.severity||'INFO') + '</span>' +
      '<span class="rule">' + (f.rule||'?') + '</span>' +
      '<div class="detail">' + (f.detail||'').replace(/</g,'&lt;') + '</div>' +
    '</div>'
  ).join('');
}}
// 点击同步
cy.on('tap', 'node', function(e){{
  const d = e.target.data();
  if(d.type === 'finding'){{
    // 在侧栏中找到对应 finding 并高亮
    const items = findingsBox.querySelectorAll('.item');
    items.forEach(el => el.style.background = '');
    const idx = findings.findIndex(f => (f.rule + '#' + f.severity) === (d.label.match(/\\[(\\w+)\\]/)&& d.label.match(/\\[(\\w+)\\]/)[1]));
  }}
}});
cy.on('tap', 'edge', function(e){{
  const d = e.target.data();
  const s = cy.getElementById(d.source).data('label');
  const t = cy.getElementById(d.target).data('label');
  const sel = s + ' → ' + t + ' · 边：' + d.label + ' (' + d.prop + ')';
  console.log(sel);
}});
</script>
</body>
</html>
"""
    result = {"ok": True, "html_bytes": len(html.encode("utf-8"))}
    if out_path is not None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")
        result["out_path"] = str(p)
        result["path_written"] = True
    else:
        result["html"] = html
    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return result
