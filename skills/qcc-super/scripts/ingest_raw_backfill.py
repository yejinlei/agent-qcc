"""从磁盘已有的 raw_*/ 目录批量回补 firm_wide.raw_* 字段。

不消耗 MCP 额度，纯本地操作。适用于：
- ingest_real.py 只写了 registration/profile/risk_scan
- ingest_equity.py 只针对单家企业回填 shareholder/executive

支持目录（自动扫描 skills/qcc-super/data/raw_*/）：
  registration.json  -> raw_registration
  profile.json       -> raw_profile
  risk_scan.json     -> raw_risk_scan
  shareholders.json  -> raw_shareholder
  key_personnel.json -> raw_executive
  listing.json       -> raw_listing
  ipr.json           -> raw_ipr
  annual_report.json -> raw_annual_report
  contact.json       -> raw_contact

用法：
  python ingest_raw_backfill.py                     # 全量回补
  python ingest_raw_backfill.py --firm 浙江胶友      # 只回补指定企业
"""
import json, sys, argparse
from pathlib import Path
import duckdb

DB_PATH = Path("f:/src/agent-qcc/skills/qcc-super/data/qcc.db")
DATA_DIR = Path("f:/src/agent-qcc/skills/qcc-super/data")

FILE_TO_COLUMN = {
    "registration.json":  "raw_registration",
    "profile.json":       "raw_profile",
    "risk_scan.json":     "raw_risk_scan",
    "shareholders.json":  "raw_shareholder",
    "key_personnel.json": "raw_executive",
    "listing.json":       "raw_listing",
    "ipr.json":           "raw_ipr",
    "annual_report.json": "raw_annual_report",
    "contact.json":       "raw_contact",
}


def _match_uscc(conn, reg_data, name_hint: str) -> str | None:
    """从 registration.json 提取 USCC，失败则按 name 反查。"""
    uscc = None
    if isinstance(reg_data, dict):
        for k in ("统一社会信用代码", "unifiedSocialCreditCode", "socialCreditCode",
                  "creditCode", "uscc"):
            if reg_data.get(k):
                uscc = str(reg_data[k])
                break
    if not uscc and name_hint:
        row = conn.execute("SELECT uscc FROM firm_wide WHERE name = ? LIMIT 1",
                            [name_hint]).fetchone()
        if row:
            uscc = row[0]
    return uscc


def _guess_uscc_from_name(name: str, conn) -> str | None:
    """从 raw_XXX 目录名（如 raw_zhejiang_jiaoyou）猜测企业名并反查 USCC。"""
    # 常见映射
    mapping = {
        "raw_zhejiang_jiaoyou":  "浙江胶友科技有限公司",
        "raw_xiamen_jiaoyou":    "厦门胶友科技有限公司",
        "raw_zhejiang_ruihang":  "浙江瑞航国际贸易有限公司",
    }
    if name in mapping:
        row = conn.execute("SELECT uscc FROM firm_wide WHERE name = ?",
                            [mapping[name]]).fetchone()
        return row[0] if row else None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--firm", help="只回补指定企业名（模糊匹配目录名）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做的事")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[err] db not found: {DB_PATH}")
        return 1

    conn = duckdb.connect(str(DB_PATH))

    dirs = sorted(DATA_DIR.glob("raw_*"))
    if args.firm:
        kw = args.firm.lower()
        dirs = [d for d in dirs if kw in d.name.lower()]

    if not dirs:
        print(f"[info] no raw dirs matched (filter={args.firm!r})")
        conn.close()
        return 0

    total_rows = 0
    for d in dirs:
        uscc = None
        reg_file = d / "registration.json"
        if reg_file.exists():
            try:
                reg = json.loads(reg_file.read_text(encoding="utf-8"))
                uscc = _match_uscc(conn, reg, None)
            except Exception as e:
                print(f"[warn] parse {reg_file} failed: {e}")
        if not uscc:
            uscc = _guess_uscc_from_name(d.name, conn)

        if not uscc:
            print(f"[skip] {d.name}: cannot resolve uscc")
            continue

        updates = []
        for fname, col in FILE_TO_COLUMN.items():
            fp = d / fname
            if not fp.exists():
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[warn] parse {fp} failed: {e}")
                continue
            payload = json.dumps(data, ensure_ascii=False)
            updates.append((col, payload, uscc))

        if not updates:
            continue

        if args.dry_run:
            print(f"[dry-run] {d.name} -> uscc={uscc}, cols={[c for c,_,_ in updates]}")
            continue

        for col, payload, u in updates:
            conn.execute(f"UPDATE firm_wide SET {col} = ? WHERE uscc = ?",
                         [payload, u])
        total_rows += len(updates)
        print(f"[ok] {d.name} -> uscc={uscc}, {len(updates)} cols updated: "
              f"{[c for c,_,_ in updates]}")

    if not args.dry_run:
        conn.commit()

    conn.close()
    print(f"\n[done] total {total_rows} column updates across {len(dirs)} firm dirs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
