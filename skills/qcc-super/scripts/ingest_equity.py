"""采集股东/主要人员数据入库（equity_edge + executive 表）

两步模式（避免重复消耗企查查额度）：
  python ingest_equity.py            # 第 1 步：调 MCP 拉取并存 raw JSON、打印结构
  python ingest_equity.py --insert   # 第 2 步：解析已存的 raw JSON 写入 DuckDB
"""
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ingest_real  # 复用 .env 加载 / init_session / call_tool

COMPANY_URL = ingest_real.COMPANY_URL
TARGET_NAME = ingest_real.TARGET_NAME
USCC = "91330110MAEW31RB2Q"
RAW_DIR = Path("f:/src/agent-qcc/skills/qcc-super/data/raw_zhejiang_jiaoyou")
DB_PATH = ingest_real.DB_PATH


async def fetch():
    jar = await ingest_real.init_session(COMPANY_URL, {})
    sh = await ingest_real.call_tool(COMPANY_URL, jar, "get_shareholder_info", {"searchKey": TARGET_NAME})
    kp = await ingest_real.call_tool(COMPANY_URL, jar, "get_key_personnel", {"searchKey": TARGET_NAME})
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.joinpath("shareholders.json").write_text(json.dumps(sh, ensure_ascii=False, indent=2), encoding="utf-8")
    RAW_DIR.joinpath("key_personnel.json").write_text(json.dumps(kp, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== shareholders.json 顶层结构 ===")
    print(json.dumps(sh, ensure_ascii=False)[:2000])
    print("\n=== key_personnel.json 顶层结构 ===")
    print(json.dumps(kp, ensure_ascii=False)[:2000])


def _num(v):
    """把 '68.00%' / '680万元人民币' / 数字 归一化为 float；失败返回 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s.endswith("%"):
        try:
            return float(s[:-1])
        except ValueError:
            return None
    import re
    m = re.search(r"[\d.]+", s)
    return float(m.group()) if m else None


def _ratio(v):
    """持股比例归一化为 0-1 小数（schema/推理层约定）；'68.00%' → 0.68。"""
    n = _num(v)
    if n is None:
        return None
    return n / 100.0 if n > 1 else n


ROLE_ENUM = ("法代", "董事长", "总经理", "董事", "监事", "高管", "实控人")


def _primary_role(raw):
    """多角色串（如 '经理,董事,财务负责人'）映射为本体枚举内的主角色。"""
    for part in str(raw or "").replace("，", ",").split(","):
        part = part.strip()
        if part in ROLE_ENUM:
            return part
        if part == "经理":
            return "总经理"
    return "高管"


def insert():
    sh = json.loads(RAW_DIR.joinpath("shareholders.json").read_text(encoding="utf-8"))
    kp = json.loads(RAW_DIR.joinpath("key_personnel.json").read_text(encoding="utf-8"))

    def rows(d, *keys):
        """从响应里挖出记录列表（兼容嵌套 data/result/中文键）。"""
        for k in keys:
            if isinstance(d, dict) and isinstance(d.get(k), list):
                return d[k]
        if isinstance(d, dict):
            for v in d.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
                if isinstance(v, dict):
                    got = rows(v)
                    if got:
                        return got
        if isinstance(d, list):
            return d
        return []

    def pick(row, *keys):
        for k in keys:
            if isinstance(row, dict) and row.get(k) not in (None, ""):
                return row[k]
        return None

    import duckdb
    conn = duckdb.connect(DB_PATH)
    conn.execute("DELETE FROM equity_edge WHERE target_uscc = ?", [USCC])
    conn.execute("DELETE FROM executive WHERE uscc = ?", [USCC])

    def _lookup_uscc(name: str):
        """MCP 响应里没有股东 USCC，用 name 反查 firm_wide 补齐。"""
        r = conn.execute("SELECT uscc FROM firm_wide WHERE name = ? LIMIT 1", [name]).fetchone()
        return r[0] if r else None

    n_eq = 0
    for row in rows(sh, "股东信息", "shareholderList", "shareholders", "data"):
        name = pick(row, "股东名称", "姓名", "name", "holderName", "shareholderName")
        if not name:
            continue
        raw_type = pick(row, "股东类型", "holderType", "type")
        holder_type = "person" if raw_type and ("自然人" in str(raw_type) or "person" in str(raw_type).lower()) else "firm"
        holder_uscc = pick(row, "股东统一社会信用代码", "统一社会信用代码", "holderUscc", "uscc", "creditCode") or _lookup_uscc(str(name))
        holder_person_id = None
        if holder_type == "person":
            import hashlib
            holder_person_id = "p_" + hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:16]
        conn.execute("""
            INSERT INTO equity_edge (holder_uscc, holder_name, holder_type, holder_person_id,
                                     target_uscc, target_name,
                                     stake_ratio, subscribed_capital, paid_in_capital, is_direct,
                                     source_tool, raw_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, 'get_shareholder_info', ?, current_timestamp)
        """, [
            holder_uscc, str(name), holder_type, holder_person_id,
            USCC, TARGET_NAME,
            _ratio(pick(row, "持股比例", "认缴比例", "stakeRatio", "ratio", "percent")),
            _num(pick(row, "认缴出资额", "认缴金额", "subscribedCapital")),
            _num(pick(row, "实缴出资额", "实缴金额", "paidInCapital")),
            json.dumps(row, ensure_ascii=False),
        ])
        n_eq += 1

    # 回填 firm_wide.raw_shareholder / raw_executive（MCP 原始响应兜底）
    conn.execute(
        "UPDATE firm_wide SET raw_shareholder = ? WHERE uscc = ?",
        [json.dumps(sh, ensure_ascii=False), USCC],
    )
    conn.execute(
        "UPDATE firm_wide SET raw_executive = ? WHERE uscc = ?",
        [json.dumps(kp, ensure_ascii=False), USCC],
    )

    n_ex = 0
    for row in rows(kp, "主要人员信息", "主要人员", "personnelList", "keyPersonnel", "data"):
        name = pick(row, "姓名", "名称", "name", "personName")
        if not name:
            continue
        conn.execute("""
            INSERT INTO executive (uscc, name, role, is_active, source_tool, raw_json)
            VALUES (?, ?, ?, TRUE, 'get_key_personnel', ?)
        """, [
            USCC, str(name),
            _primary_role(pick(row, "职务", "职位", "role", "position")),
            json.dumps(row, ensure_ascii=False),
        ])
        n_ex += 1

    conn.commit()
    print(f"equity_edge 插入 {n_eq} 行, executive 插入 {n_ex} 行")
    for r in conn.execute("SELECT holder_name, holder_type, stake_ratio FROM equity_edge WHERE target_uscc = ?", [USCC]).fetchall():
        print("  股东:", r)
    for r in conn.execute("SELECT name, role FROM executive WHERE uscc = ?", [USCC]).fetchall():
        print("  高管:", r)
    conn.close()


if __name__ == "__main__":
    if "--insert" in sys.argv:
        insert()
    else:
        asyncio.run(fetch())
