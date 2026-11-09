"""
从 tools_raw.json 解析每个 MCP 工具返回的具体字段信息。
输出：
  - tool_fields.json : 结构化字段清单（每个工具一条记录，适合入库）
  - tool_fields.csv  : 扁平化 CSV（便于 Excel/ETL 直接导入）
"""
import json
import re
import csv
from pathlib import Path

SRC = Path(r"f:\src\agent-qcc\tools_raw.json")
OUT_JSON = Path(r"f:\src\agent-qcc\tool_fields.json")
OUT_CSV = Path(r"f:\src\agent-qcc\tool_fields.csv")

# ---------- 字段提取规则 ----------
# 描述里出现的返回字段列举句式：
#   "包括A、B、C等信息"
#   "包括A、B、C"
#   "涵盖A、B、C"
#   "覆盖A、B、C"
#   "提供A、B、C等关键...信息"
FIELD_PATTERNS = [
    r"包括([\u4e00-\u9fa5、／/和及,，\s\-()（）0-9%]{4,120}?)(?=等|信息。|。|$|【)",
    r"涵盖([\u4e00-\u9fa5、／/和及,，\s\-()（）0-9%]{4,120}?)(?=等|信息。|。|$|【)",
    r"覆盖([\u4e00-\u9fa5、／/和及,，\s\-()（）0-9%]{4,120}?)(?=等|信息。|。|$|【)",
    r"提供([\u4e00-\u9fa5、／/和及,，\s\-()（）0-9%]{4,120}?)(?=等|信息。|。|$|【)",
    r"返回([\u4e00-\u9fa5、／/和及,，\s\-()（）0-9%]{4,120}?)(?=等|信息。|。|$|【)",
]

# 数据更新频率
FREQ_PATTERN = r"数据更新频率[：:]\s*([^\r\n]{2,60})"

# 数据来源（"T+0（xxx）" 括号里的来源系统）
SOURCE_PATTERN = r"（(国家企业信用信息公示系统|最高人民法院|人民法院|中国执行信息公开网|国家信用信息共享平台|交易所|海关|银行间|证监会|市场监管总局|国家税务总局|生态环境部|商务部|知识产权局|税务局|海关总署)）"


def split_fields(text: str) -> list[str]:
    """将 'A、B、C、D' 类串拆成字段列表"""
    if not text:
        return []
    # 去除尾部残词
    text = text.strip().strip("、/／,，和及等 ")
    # 优先按顿号 / 逗号 / 斜杠 / 和 / 及 / 分隔
    parts = re.split(r"[、/／,，和及]+", text)
    out = []
    for p in parts:
        p = p.strip().strip("、/／,，和及等 ")
        # 过滤明显噪声
        if not p:
            continue
        if len(p) > 40:  # 过长多半是说明句
            continue
        if p in {"信息", "关键信息", "等", "等关键信息", "数据"}:
            continue
        out.append(p)
    # 去重保序
    seen = set()
    uniq = []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return uniq


def extract_from_desc(desc: str) -> list[str]:
    """在描述中扫描所有字段列举句式，合并字段列表"""
    fields: list[str] = []
    for pat in FIELD_PATTERNS:
        for m in re.finditer(pat, desc):
            fields.extend(split_fields(m.group(1)))
    # 去重保序
    seen = set()
    uniq = []
    for f in fields:
        if f in seen:
            continue
        seen.add(f)
        uniq.append(f)
    return uniq


def extract_freq(desc: str) -> str:
    m = re.search(FREQ_PATTERN, desc)
    return m.group(1).strip() if m else ""


def extract_source(desc: str) -> str:
    m = re.search(SOURCE_PATTERN, desc)
    return m.group(1) if m else ""


def strip_noise(desc: str) -> str:
    """去掉描述中的换行、多余空白、AI 数据纪律段（保留核心语义）"""
    d = re.sub(r"【AI 数据纪律[^】]*】[^\r\n]*", "", desc)
    d = re.sub(r"\r\n", " ", d)
    d = re.sub(r"\n", " ", d)
    d = re.sub(r"\s{2,}", " ", d)
    return d.strip()


def main():
    raw = json.loads(SRC.read_text(encoding="utf-8"))
    records = []
    stats = {"total": 0, "with_fields": 0, "empty_fields": 0}

    for server, tools in raw["tools"].items():
        for t in tools:
            name = t.get("name", "")
            title = t.get("title", "")
            desc = t.get("description", "") or ""
            schema = t.get("inputSchema", {}) or {}
            props = schema.get("properties", {}) or {}
            required = schema.get("required", []) or []

            input_params = []
            for pname, pdef in props.items():
                input_params.append({
                    "name": pname,
                    "type": pdef.get("type", ""),
                    "description": (pdef.get("description", "") or "").strip(),
                    "required": pname in required,
                    "enum": pdef.get("enum") if isinstance(pdef.get("enum"), list) else None,
                })

            fields = extract_from_desc(desc)
            freq = extract_freq(desc)
            source = extract_source(desc)
            clean_desc = strip_noise(desc)

            record = {
                "server": server,
                "tool": name,
                "title": title,
                "description_clean": clean_desc,
                "description_full": desc,
                "return_field_count": len(fields),
                "return_fields": fields,
                "input_params": input_params,
                "update_frequency": freq,
                "data_source": source,
            }
            records.append(record)
            stats["total"] += 1
            if fields:
                stats["with_fields"] += 1
            else:
                stats["empty_fields"] += 1

    OUT_JSON.write_text(
        json.dumps({"stats": stats, "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 扁平 CSV：server,tool,title,return_field_count,return_fields(用 || 分隔),input_params,update_frequency,data_source,description_clean
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "server", "tool", "title",
            "return_field_count", "return_fields",
            "input_params", "update_frequency", "data_source",
            "description_clean",
        ])
        for r in records:
            w.writerow([
                r["server"], r["tool"], r["title"],
                r["return_field_count"],
                "||".join(r["return_fields"]),
                json.dumps(r["input_params"], ensure_ascii=False),
                r["update_frequency"], r["data_source"],
                r["description_clean"],
            ])

    # 控制台摘要
    print(f"总工具数: {stats['total']}")
    print(f"含明确返回字段: {stats['with_fields']}")
    print(f"字段为空（描述未列举）: {stats['empty_fields']}")
    print(f"输出: {OUT_JSON.name}, {OUT_CSV.name}")

    # 每 server 的字段丰富度
    from collections import defaultdict
    by_server = defaultdict(lambda: [0, 0])
    for r in records:
        by_server[r["server"]][0] += 1
        if r["return_field_count"] > 0:
            by_server[r["server"]][1] += 1
    print("\n按 server 分组（工具数/有字段数）：")
    for s, (n, nf) in sorted(by_server.items()):
        print(f"  {s}: {n} 工具，{nf} 个有明确字段")


if __name__ == "__main__":
    main()
