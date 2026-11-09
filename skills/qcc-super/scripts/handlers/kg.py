# -*- coding: utf-8 -*-
"""Semantica 相关 slash 命令处理器。

依赖: semantica / rdflib / pyshacl 中至少一个可用。
安装命令: uv add semantica rdflib pyshacl
或:       pip install semantica rdflib pyshacl
"""
import json
import subprocess
import sys
from pathlib import Path

from .core import _connect, _rows_to_dicts  # noqa: F401  # 借用 conn 帮助函数

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import kg_engine  # noqa: E402


def _install_packages(pkgs: list) -> dict:
    """尝试通过 uv → pip 安装。"""
    # 优先 uv
    try:
        r = subprocess.run(
            ["uv", "add", *pkgs],
            capture_output=True, text=True, timeout=300, cwd=str(HERE.parents[2])
        )
        if r.returncode == 0:
            return {"ok": True, "via": "uv", "output": r.stdout[-1000:]}
        uv_err = r.stderr[-300:]
    except Exception as e:
        uv_err = str(e)

    # 回退 pip
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", *pkgs],
            capture_output=True, text=True, timeout=300
        )
        if r.returncode == 0:
            return {"ok": True, "via": "pip", "output": r.stdout[-1000:], "uv_error": uv_err}
        pip_err = r.stderr[-300:]
    except Exception as e:
        pip_err = str(e)

    return {"ok": False, "error": f"install failed. uv: {uv_err}; pip: {pip_err}",
            "hint": f"Manual: uv add {' '.join(pkgs)}"}


def kg_install(args: dict, cmd: dict) -> dict:
    pkgs = ["semantica", "rdflib", "pyshacl"]
    return _install_packages(pkgs)


def _get_target_graph(target: str, depth: int = 1) -> dict:
    g = kg_engine.fetch_enterprise_graph(target, depth=depth)
    if not g.get("found"):
        return g
    return g


def kg_build(args: dict, cmd: dict) -> dict:
    target = args.get("target")
    depth = int(args.get("depth") or 1)
    graph_data = _get_target_graph(target, depth)
    if not graph_data.get("found"):
        return {"ok": True, "found": False, "target": target, "note": "企业不在 DuckDB"}
    # 尝试构建 rdflib 图，统计节点/边数
    if not kg_engine.rdflib_available():
        return {
            "ok": True, "found": True, "target": target,
            "note": "rdflib 未安装，无法构建 RDF 图",
            "hint": "Run: uv add rdflib pyshacl",
            "firm": {"uscc": graph_data["uscc"], "name": graph_data["firm"].get("name")},
            "counts": {
                "shareholders": len(graph_data.get("shareholders", [])),
                "executives": len(graph_data.get("executives", [])),
                "events": len(graph_data.get("events", [])),
                "external_investments": len(graph_data.get("external_investments", [])),
            }
        }
    G = kg_engine.to_rdflib_graph(graph_data)
    # 简单统计
    nodes = set()
    edges = 0
    for s, p, o in G:
        if str(o).startswith("http://") or str(o).startswith("urn:"):
            nodes.add(str(o))
        edges += 1
    for s, p, o in G:
        if str(s).startswith("http://"):
            nodes.add(str(s))
    return {
        "ok": True, "found": True, "target": target,
        "uscc": graph_data["uscc"],
        "firm_name": graph_data["firm"].get("name"),
        "counts": {
            "triples": len(list(G)),
            "unique_nodes": len(nodes),
            "shareholders": len(graph_data.get("shareholders", [])),
            "executives": len(graph_data.get("executives", [])),
            "events": len(graph_data.get("events", [])),
        },
        "semantica_available": kg_engine.semantica_available(),
        "rdflib_available": kg_engine.rdflib_available(),
    }


def kg_query(args: dict, cmd: dict) -> dict:
    """SPARQL 查询。支持 --query 内联（无空格）或 --query-file 文件路径。

    CLI 层对含空格参数会分词，因此复杂 SPARQL 推荐用 query-file 或 Agent 直接调 dispatch API。
    """
    if not kg_engine.rdflib_available():
        return {"ok": False, "error": "rdflib not installed"}
    import os
    target = args.get("target") or os.environ.get("KG_TARGET")
    if not target:
        conn = _connect()
        try:
            row = conn.execute("SELECT uscc FROM firm_wide LIMIT 1").fetchone()
            if not row:
                return {"ok": False, "error": "no target and firm_wide is empty"}
            target = row[0]
        finally:
            conn.close()

    graph_data = _get_target_graph(target)
    if not graph_data.get("found"):
        return {"ok": True, "found": False, "target": target}
    G = kg_engine.to_rdflib_graph(graph_data)
    lang = (args.get("lang") or "sparql").lower()
    if lang != "sparql":
        return {"ok": False, "error": f"only SPARQL supported via rdflib, got: {lang}"}

    # 读 query：优先 query_file，其次 query；兼容多种命名（连字符/下划线/驼峰）
    query = args.get("query") or ""
    qf = (args.get("query_file")
          or args.get("query-file")
          or args.get("queryFile")
          or args.get("sparql_file")
          or args.get("sparql-file"))
    if qf:
        p = Path(qf)
        if not p.exists():
            # 依次尝试：项目根 / skills/qcc-super / 当前目录
            candidates = [
                HERE.parents[2] / qf,      # 项目根
                HERE / qf,                  # skills/qcc-super
                HERE.parents[0] / qf,       # 同 HERE
            ]
            for cand in candidates:
                if cand.exists():
                    p = cand
                    break
            else:
                return {"ok": False, "error": f"query file not found: {qf}"}
        query = p.read_text(encoding="utf-8").strip()

    if not query:
        return {"ok": False, "error": "no query provided. Use --query '...' or --query-file path.sparql"}

    # 剥掉 Agent 通过 dispatch API 传进来的外层引号
    if len(query) >= 2 and query[0] == query[-1] and query[0] in ('"', "'"):
        query = query[1:-1]

    try:
        results = G.query(query)
        out = []
        try:
            cols = [str(v) for v in results.vars]
        except Exception:
            cols = [f"col{i}" for i in range(5)]
        for row in results:
            try:
                d = {cols[i] if i < len(cols) else f"col{i}": row[i].n if hasattr(row[i], 'n') else str(row[i])
                     for i in range(len(row))}
            except Exception:
                d = {str(v): (v.n if hasattr(v, 'n') else str(v)) for v in row}
            out.append(d)
        return {"ok": True, "target": target, "results": out, "count": len(out), "triples": len(list(G))}
    except Exception as e:
        return {"ok": False, "error": str(e), "query": query[:200]}


def kg_validate(args: dict, cmd: dict) -> dict:
    if not kg_engine.rdflib_available() or not _pyshacl_available():
        return {"ok": False, "error": "rdflib/pyshacl not installed. Run: uv add rdflib pyshacl"}
    import os
    target = args.get("target") or os.environ.get("KG_TARGET")
    if not target:
        conn = _connect()
        try:
            row = conn.execute("SELECT uscc FROM firm_wide LIMIT 1").fetchone()
            if not row:
                return {"ok": False, "error": "no target and firm_wide is empty"}
            target = row[0]
        finally:
            conn.close()
    graph_data = _get_target_graph(target)
    if not graph_data.get("found"):
        return {"ok": True, "found": False, "target": target}
    result = kg_engine.shacl_validate(graph_data)
    result["target"] = target
    return result


def _pyshacl_available() -> bool:
    try:
        import pyshacl  # noqa: F401
        return True
    except ImportError:
        return False


def kg_infer(args: dict, cmd: dict) -> dict:
    """图推理（含 owlrl 闭世界闭包）。

    参数:
      target   : 目标企业（uscc/name）
      use_owlrl: 是否启用 owlrl 闭世界闭包，默认 true（缺失自动降级）
    """
    import os
    target = args.get("target") or os.environ.get("KG_TARGET")
    if not target:
        conn = _connect()
        try:
            row = conn.execute("SELECT uscc FROM firm_wide LIMIT 1").fetchone()
            if not row:
                return {"ok": False, "error": "no target and firm_wide is empty"}
            target = row[0]
        finally:
            conn.close()
    graph_data = _get_target_graph(target)
    if not graph_data.get("found"):
        return {"ok": True, "found": False, "target": target}
    use_owlrl = str(args.get("use_owlrl", "true")).lower() in ("1", "true", "yes", "on")
    result = kg_engine.infer_risk(graph_data, use_owlrl=use_owlrl)
    result["target"] = target
    result["uscc"] = graph_data.get("uscc")
    result["owlrl_available"] = kg_engine.owlrl_available()
    return result


def kg_export(args: dict, cmd: dict) -> dict:
    if not kg_engine.rdflib_available():
        return {"ok": False, "error": "rdflib not installed"}
    import os
    target = args.get("target") or os.environ.get("KG_TARGET")
    if not target:
        conn = _connect()
        try:
            row = conn.execute("SELECT uscc FROM firm_wide LIMIT 1").fetchone()
            if not row:
                return {"ok": False, "error": "no target and firm_wide is empty"}
            target = row[0]
        finally:
            conn.close()
    graph_data = _get_target_graph(target)
    if not graph_data.get("found"):
        return {"ok": True, "found": False, "target": target}
    fmt = args.get("format", "ttl")
    content = kg_engine.export_graph(graph_data, fmt=fmt)
    return {"ok": True, "target": target, "format": fmt, "content": content, "bytes": len(content)}


def kg_visualize(args: dict, cmd: dict) -> dict:
    """生成 Cytoscape.js 单文件 HTML 可视化。"""
    if not kg_engine.rdflib_available():
        return {"ok": False, "error": "rdflib not installed"}
    import os
    target = args.get("target") or os.environ.get("KG_TARGET")
    if not target:
        conn = _connect()
        try:
            row = conn.execute("SELECT uscc FROM firm_wide LIMIT 1").fetchone()
            if not row:
                return {"ok": False, "error": "no target and firm_wide is empty"}
            target = row[0]
        finally:
            conn.close()

    use_owlrl = _truthy(args.get("use_owlrl", True))

    cy_data = kg_engine.build_cytoscape_data(target, use_owlrl=use_owlrl)
    if not cy_data.get("ok"):
        return {"ok": False, "error": cy_data.get("error")}

    # 输出路径：--out 指定 / 或默认 output/kg-{uscc}.html
    out = args.get("out")
    if not out:
        default_dir = HERE.parent / "output"
        out = default_dir / f"kg-{cy_data['center_uscc']}.html"

    r = kg_engine.render_graph_html(cy_data, out_path=out)
    r["stats"] = cy_data["stats"]
    r["center_uscc"] = cy_data["center_uscc"]
    r["center_name"] = cy_data["center_name"]
    r["network_size"] = cy_data["network_size"]
    r["triples"] = cy_data["triples"]
    r["owlrl"] = cy_data["owlrl"]
    return r


def kg_infer_visualize(args: dict, cmd: dict) -> dict:
    """运行 /kg-infer 并渲染推理链图 HTML（Rule → Finding → Conclusion）。"""
    if not kg_engine.rdflib_available():
        return {"ok": False, "error": "rdflib not installed"}
    import os
    target = args.get("target") or os.environ.get("KG_TARGET")
    if not target:
        conn = _connect()
        try:
            row = conn.execute("SELECT uscc FROM firm_wide LIMIT 1").fetchone()
            if not row:
                return {"ok": False, "error": "no target and firm_wide is empty"}
            target = row[0]
        finally:
            conn.close()

    use_owlrl = _truthy(args.get("use_owlrl", True))

    graph_data = _get_target_graph(target)
    if not graph_data.get("found"):
        return {"ok": True, "found": False, "target": target}

    iv_data = kg_engine.infer_visualize(graph_data, use_owlrl=use_owlrl)
    if not iv_data.get("ok"):
        return {"ok": False, "error": iv_data.get("error")}

    out = args.get("out")
    if not out:
        default_dir = HERE.parent / "output"
        out = default_dir / f"kg-infer-{iv_data['center_uscc']}.html"

    r = kg_engine.render_infer_html(iv_data, out_path=out)
    r["center_uscc"] = iv_data["center_uscc"]
    r["center_name"] = iv_data["center_name"]
    r["conform"] = iv_data["conform"]
    r["finding_count"] = iv_data["finding_count"]
    r["rule_count"] = iv_data["rule_count"]
    r["severity_counts"] = iv_data["severity_counts"]
    r["owlrl"] = iv_data["owlrl"]
    r["graph_elements"] = {
        "nodes": len(iv_data["elements"]["nodes"]),
        "edges": len(iv_data["elements"]["edges"]),
    }
    return r


def kg_semantica_probe(args: dict, cmd: dict) -> dict:
    """探测 semantica 及本体论栈依赖状态。

    输出:
      - semantica_probe: 深度探测结果（版本 / 模块 / 新旧架构 / 一句话摘要）
      - rdflib / pyshacl / owlrl: 版本 + 可用性
      - sample_graph: 用 to_rdflib_graph 试跑一个最小企业图，验证本体映射链路
      - python: 运行环境
    """
    info = {"ok": True}

    # 深度 semantica 探测（真正验证核心模块可用性）
    info["semantica"] = kg_engine.semantica_probe()

    # rdflib
    info["rdflib_available"] = kg_engine.rdflib_available()
    try:
        import rdflib
        info["rdflib_version"] = rdflib.__version__
    except Exception:
        info["rdflib_version"] = None

    # pyshacl
    info["pyshacl_available"] = _pyshacl_available()
    try:
        import pyshacl
        info["pyshacl_version"] = getattr(pyshacl, "__version__", "unknown")
    except Exception:
        info["pyshacl_version"] = None

    # owlrl
    info["owlrl_available"] = kg_engine.owlrl_available()
    try:
        import owlrl
        info["owlrl_version"] = getattr(owlrl, "__version__", "unknown")
    except Exception:
        info["owlrl_version"] = None

    # sample graph quick-test：验证本体映射链路端到端可用
    info["sample_graph"] = {}
    try:
        graph_data = {
            "found": True,
            "target": "ProbeFirm",
            "firm": {
                "uscc": "TEST0000000000000001",
                "name": "ProbeFirm",
                "legal_rep": "张三",
                "status": "存续",
                "reg_capital": 100.0,
                "reg_capital_currency": "CNY",
                "establish_date": "2020-01-01",
                "dereg_date": None,
                "unified_social_credit_code": "TEST0000000000000001",
                "credit_code": "TEST0000000000000001",
                "province": "浙江省",
                "city": "杭州市",
                "registered_address": "测试地址",
            },
            "shareholders": [
                {"holder_uscc": "TEST0000000000000002", "holder_name": "股东A", "ratio": 60.0},
            ],
            "executives": [
                {"name": "李四", "position": "总经理"},
            ],
            "events": [],
            "external_investments": [],
        }
        G = kg_engine.to_rdflib_graph(graph_data)
        info["sample_graph"]["triples"] = len(list(G))
        info["sample_graph"]["ok"] = True
    except Exception as e:
        info["sample_graph"]["ok"] = False
        info["sample_graph"]["error"] = f"{type(e).__name__}: {e}"

    info["python"] = sys.version.split()[0]
    info["path"] = sys.executable
    return info


def kg_traverse(args: dict, cmd: dict) -> dict:
    """跨企业 N 层穿透（多关系 BFS）+ semantica 社区发现 + 中心性分析。

    参数:
      target:      中心企业 USCC 或名称（必填）
      layers:      最大穿透层数（默认 3，范围 1-6）
      min_ratio:   equity 边持股比例过滤阈值（默认 0.0，不过滤）
      relations:   关系集合（默认 'all'）
                    可选: all / equity / legal_rep / controller / ubo / executive
                    或逗号分隔组合，如 'equity,legal_rep,executive'

    返回边类型:
      hasShareholder (equity) / hasLegalRep / hasController / hasUBO / hasExecutive
    """
    target = args.get("target") or args.get("query")
    if not target:
        return {"ok": False, "error": "missing target", "hint": "/kg-traverse <企业USCC或名称>"}

    layers = int(args.get("layers") or args.get("depth") or 3)
    try:
        min_ratio = float(args.get("min_ratio") or args.get("ratio") or 0)
    except (TypeError, ValueError):
        min_ratio = 0.0

    relations = str(args.get("relation") or args.get("relations") or "all").strip()

    result = kg_engine.traverse_and_analyze(
        target,
        layers=layers,
        min_ratio=min_ratio,
        relations=relations,
    )

    # 精简返回，避免 stdout 太大
    out = {
        "ok": result.get("ok"),
        "target": result.get("target"),
        "center_uscc": result.get("center_uscc"),
        "center_name": result.get("center_name"),
        "layers_reached": result.get("layers_reached"),
        "relation_types_used": result.get("relation_types_used"),
        "stats": result.get("stats"),
        "layers": result.get("layers"),
        "edges": result.get("edges"),
        "top_hubs": result.get("top_hubs"),
        "communities_summary": result.get("communities_summary"),
        "analysis": {
            "centrality_scores": result.get("analysis", {}).get("centrality_scores"),
            "modularity": result.get("analysis", {}).get("modularity"),
            "num_communities": result.get("analysis", {}).get("num_communities"),
            "algorithm": result.get("analysis", {}).get("algorithm"),
            "node_community": result.get("analysis", {}).get("node_community"),
            "connectivity": result.get("analysis", {}).get("connectivity"),
        },
        "semantica_available": result.get("semantica_available"),
        "semantica_error": result.get("semantica_error"),
        "error": result.get("error"),
        "note": result.get("note"),
    }
    return out


def kg_semantica_ontology(args: dict, cmd: dict) -> dict:
    """用 semantica.ontology.OntologyEngine 校验 qcc_ontology.ttl 本体质量。

    参数:
      target: 可选，仅作记录用途（当前校验对象固定为 references/ontology/turtle/qcc_ontology.ttl）

    返回:
      - passed: 是否通过（issues 里无 ERROR 级）
      - classes_count / properties_count: TTL 解析出的类与属性数
      - metrics: 覆盖率 / 类覆盖率 / 属性覆盖率 / validator_valid
      - issues: semantica OntologyQualityReport 返回的问题清单（含 severity/code/message/element_id）
      - summary: 一句话摘要
    """
    result = kg_engine.semantica_ontology_validate(target=args.get("target"))
    return result


def kg_semantica_reason(args: dict, cmd: dict) -> dict:
    """用 semantica.reasoning.SPARQLReasoner 对企业图执行规则推理。

    参数:
      target:   目标企业（uscc/name），缺省取 firm_wide 首行
      rules:    规则 id 列表，逗号分隔或 'all'（默认 all）
                可选: IR1-highest-shareholder / IR2-direct-control / IR3-risk-propagation
      use_owlrl: 是否先做 owlrl 闭世界闭包（默认 true）

    内置规则:
      IR1-highest-shareholder : 识别持股 ≥50% 的控股股东
      IR2-direct-control      : 提取法定代表人关联（企业→自然人）
      IR3-risk-propagation    : 识别持股 ≥20% 股东的风险传导
    """
    target = args.get("target") or args.get("query")
    rules = str(args.get("rules") or args.get("rule") or "all")
    use_owlrl = _truthy(args.get("use_owlrl", True))
    result = kg_engine.semantica_reason(
        target=target, rules=rules, use_owlrl=use_owlrl,
    )
    return result


def kg_semantica_requirements(args: dict, cmd: dict) -> dict:
    """在推理前主动列出每条规则需要的数据 + 当前就绪度。

    参数:
      target: 目标企业（uscc/name），缺省为全表统计
      rules:  规则 id 列表，逗号分隔或 'all'（默认 all）

    对每条规则返回：
      - description: 规则描述
      - predicates: 需要的语义 predicate + 对应源表 + 字段
      - filters:    SPARQL 里的 FILTER 条件
      - availability: 每个 predicate 在 DuckDB 里的实际命中数
      - ready:      所有 predicate 都有数据才 True
      - suggestion: 若未就绪，提示需要运行哪些 ingest 命令
    """
    target = args.get("target") or args.get("query")
    rules = str(args.get("rules") or args.get("rule") or "all")
    return kg_engine.semantica_rule_requirements(rules=rules, target=target)


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _to_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def kg_semantica_visualize(args: dict, cmd: dict) -> dict:
    """用 semantica 原生可视化引擎导出 HTML/PNG。

    参数:
      kind:            "kg" (默认) 企业知识图谱 / "ontology" 本体结构图
      target:          目标企业 uscc/name（kind=kg 时生效）
      output:          "html" (默认) / "png"
      depth:           KG 穿透跳数，默认 2
      include_risk:    是否把 event_wide 风险事件作为独立 event 节点，默认 true
      include_executive: 是否展开 executive 高管（覆盖所有穿透到的企业），默认 true
      relations:       firm_relation 关系过滤：all|top
    """
    kind = str(args.get("kind") or "kg").lower()
    target = args.get("target") or args.get("query")
    output = str(args.get("output") or "html").lower()
    depth = _to_int(args.get("depth"), 2)
    include_risk = _truthy(args.get("include_risk", True))
    include_executive = _truthy(args.get("include_executive", True))
    relations = str(args.get("relations") or args.get("relation") or "all")
    return kg_engine.semantica_visualize(
        kind=kind, target=target, output=output,
        depth=depth, include_risk=include_risk,
        include_executive=include_executive, relations=relations,
    )
