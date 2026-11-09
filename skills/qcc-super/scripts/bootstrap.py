"""
qcc-super skill 首次运行引导：
  1. 安装 duckdb（如果未安装）
  2. 创建 data/ 目录
  3. 执行 references/schema.sql 建表（幂等）
  4. 导入 tool_catalog 目录（从 tool_fields.json，如果存在）

幂等：可安全重复运行。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "qcc.db"
SCHEMA_SQL = ROOT / "references" / "schema.sql"
TOOL_FIELDS_JSON = ROOT / "references" / "tool_fields.json"

# 批量工具清单（供 efficiency 计算参考）
BATCH_TOOLS = {
    "get_company_risk_scan": 34,
    "get_executive_risk_scan": 18,
    "get_change_records": 5,
}


def _try(cmd: list[str]) -> bool:
    """尝试执行命令，成功返回 True"""
    try:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def install_duckdb() -> None:
    """确保 duckdb 可用。尝试 uv → pip → 报错退出。"""
    try:
        import duckdb  # noqa: F401
        return
    except ImportError:
        pass

    print("[bootstrap] Installing duckdb...")

    # 优先 uv（如果项目通过 uv 管理）
    if _try(["uv", "--version"]):
        if _try(["uv", "pip", "install", "duckdb>=1.0.0"]):
            try:
                import duckdb  # noqa: F401
                return
            except ImportError:
                pass

    # 尝试 pip
    if _try([sys.executable, "-m", "pip", "install", "--quiet", "duckdb>=1.0.0"]):
        try:
            import duckdb  # noqa: F401
            return
        except ImportError:
            pass

    # 提示用户手动安装
    raise SystemExit(
        "[bootstrap] Failed to install duckdb automatically.\n"
        "Please install manually:\n"
        "  uv pip install 'duckdb>=1.0.0'\n"
        "  # or: pip install 'duckdb>=1.0.0'\n"
        "  # or: 在项目根目录 pyproject.toml 加入 duckdb>=1.0.0 后运行 uv sync"
    )


def init_database() -> None:
    """执行 DDL 建表（幂等）"""
    import duckdb

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    schema = SCHEMA_SQL.read_text(encoding="utf-8")
    conn = duckdb.connect(str(DB_PATH))
    try:
        # 逐条执行 DDL（DuckDB 对多语句 execute 支持有限，拆开更稳）
        for stmt in _split_sql(schema):
            if stmt.strip():
                conn.execute(stmt)
        print(f"[bootstrap] Schema applied: {DB_PATH}")
    finally:
        conn.close()


def _split_sql(sql: str) -> list[str]:
    """按 ; 分割，忽略注释块"""
    lines: list[str] = []
    buf: list[str] = []
    in_block = False
    for line in sql.splitlines():
        # 块注释
        if "--[" in line and "]" not in line.split("--", 1)[1]:
            in_block = True
        if in_block:
            if "]" in line:
                in_block = False
            continue
        # 行注释整行去掉
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).rstrip().rstrip(";").strip()
            if stmt:
                lines.append(stmt)
            buf = []
    # 剩余语句
    tail = "\n".join(buf).strip().rstrip(";").strip()
    if tail:
        lines.append(tail)
    return lines


def seed_tool_catalog(conn) -> int:
    """从 tool_fields.json 导入工具目录到 tool_catalog 表"""
    if not TOOL_FIELDS_JSON.exists():
        return 0

    data = json.loads(TOOL_FIELDS_JSON.read_text(encoding="utf-8"))
    rows: list[tuple] = []
    for idx, r in enumerate(data["records"], start=1):
        fields = r.get("return_fields") or []
        return_field_count = len(fields)
        is_batch = r.get("tool") in BATCH_TOOLS
        efficiency = (
            BATCH_TOOLS[r["tool"]] / 1.0
            if is_batch
            else max(1.0, float(return_field_count))
        )
        rows.append((
            idx,  # id
            r.get("server"),
            r.get("tool"),
            r.get("title"),
            r.get("description_clean"),
            r.get("description_full"),
            return_field_count,
            "|".join(fields) if fields else "",
            json.dumps(r.get("input_params") or [], ensure_ascii=False),
            r.get("update_frequency"),
            r.get("data_source"),
            is_batch,
            efficiency,
        ))

    if not rows:
        return 0

    # 先删后插（DuckDB 不支持多 UNIQUE 约束下的 OR REPLACE）
    conn.execute("DELETE FROM tool_catalog")

    sql = """
        INSERT INTO tool_catalog
        (id, server, tool_name, title, description_clean, description_full,
         return_field_count, return_fields, input_params_json,
         update_frequency, data_source, is_batch, efficiency)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    conn.executemany(sql, rows)
    return len(rows)


def main() -> None:
    install_duckdb()
    init_database()

    # 导入工具目录
    import duckdb
    conn = duckdb.connect(str(DB_PATH))
    try:
        n = seed_tool_catalog(conn)
        if n:
            print(f"[bootstrap] Seeded {n} tools into tool_catalog")
    finally:
        conn.close()

    print("[bootstrap] Done.")


if __name__ == "__main__":
    main()
