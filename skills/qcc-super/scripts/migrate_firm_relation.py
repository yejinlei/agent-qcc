#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
迁移脚本：创建并填充 firm_relation 统一边表。

归并 5 种关系到一行一条的规范表，穿透/关联分析直接查这一张表，
不再需要在代码里拼接 equity_edge + firm_wide + executive 三处查询。

关系映射：
  equity_edge              → equity      / hasShareholder / firm_to_firm
  firm_wide.legal_rep      → legal_rep   / hasLegalRep    / person_to_firm
  firm_wide.controller_name → controller  / hasController  / person_to_firm
  firm_wide.ubo_name       → ubo         / hasUBO         / person_to_firm
  executive                → executive   / hasExecutive   / person_to_firm

幂等：CREATE TABLE IF NOT EXISTS + 每次全量重建（DELETE 后 INSERT）。
运行：
  python migrate_firm_relation.py
  python migrate_firm_relation.py --dry-run   # 只统计不写入
"""
import argparse
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "qcc.db"
SCHEMA_SQL = ROOT / "references" / "schema.sql"

FIRM_RELATION_DDL = """
CREATE TABLE IF NOT EXISTS firm_relation (
  id                       INTEGER,
  subject_id               VARCHAR NOT NULL,
  subject_name             VARCHAR,
  subject_type             VARCHAR NOT NULL,
  object_uscc              VARCHAR NOT NULL,
  object_name              VARCHAR,
  relation_type            VARCHAR NOT NULL,
  edge_type                VARCHAR NOT NULL,
  direction                VARCHAR NOT NULL,
  weight                   DECIMAL(10,6) DEFAULT 1.0,
  role                     VARCHAR,
  source_table             VARCHAR NOT NULL,
  source_id                INTEGER,
  is_direct                BOOLEAN DEFAULT TRUE,
  discovered_at            TIMESTAMP DEFAULT current_timestamp,
  updated_at               TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS idx_rel_subject ON firm_relation(subject_id);
CREATE INDEX IF NOT EXISTS idx_rel_object ON firm_relation(object_uscc);
CREATE INDEX IF NOT EXISTS idx_rel_type ON firm_relation(relation_type);
CREATE INDEX IF NOT EXISTS idx_rel_edge ON firm_relation(edge_type);
"""


def ensure_table(conn) -> None:
    for stmt in [s.strip() for s in FIRM_RELATION_DDL.split(";") if s.strip()]:
        conn.execute(stmt)


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return row[0] > 0 if row else False


def backfill_equity(conn) -> int:
    if not _table_exists(conn, "equity_edge"):
        return 0
    n = conn.execute("SELECT COUNT(*) FROM equity_edge").fetchone()[0]
    if n == 0:
        return 0
    # 全量重建：先删本类
    conn.execute("DELETE FROM firm_relation WHERE source_table = 'equity_edge'")
    conn.execute(
        """
        INSERT INTO firm_relation
          (id, subject_id, subject_name, subject_type, object_uscc, object_name,
           relation_type, edge_type, direction, weight, role,
           source_table, source_id, is_direct)
        SELECT
          id,
          holder_uscc,
          holder_name,
          CASE
            WHEN holder_type = 'person' THEN 'Person'
            ELSE 'Enterprise'
          END,
          target_uscc,
          target_name,
          'equity',
          'hasShareholder',
          'firm_to_firm',
          COALESCE(stake_ratio, 1.0),
          NULL,
          'equity_edge',
          id,
          is_direct
        FROM equity_edge
        WHERE holder_uscc IS NOT NULL AND target_uscc IS NOT NULL
        """
    )
    return n


def backfill_legal_rep(conn) -> int:
    if not _table_exists(conn, "firm_wide"):
        return 0
    conn.execute("DELETE FROM firm_relation WHERE source_table = 'firm_wide_legal_rep'")
    n = conn.execute(
        """
        INSERT INTO firm_relation
          (id, subject_id, subject_name, subject_type, object_uscc, object_name,
           relation_type, edge_type, direction, weight, role,
           source_table, source_id, is_direct)
        SELECT
          ROW_NUMBER() OVER (ORDER BY uscc),
          'person:' || legal_rep,
          legal_rep,
          'Person',
          uscc,
          name,
          'legal_rep',
          'hasLegalRep',
          'person_to_firm',
          1.0,
          NULL,
          'firm_wide_legal_rep',
          NULL,
          TRUE
        FROM firm_wide
        WHERE legal_rep IS NOT NULL AND legal_rep != ''
        """
    ).fetchone()
    # fetchone 返回 None 时表示 INSERT 已执行，用 COUNT 补一下
    row = conn.execute(
        "SELECT COUNT(*) FROM firm_relation WHERE source_table = 'firm_wide_legal_rep'"
    ).fetchone()
    return row[0] if row else 0


def backfill_controller(conn) -> int:
    if not _table_exists(conn, "firm_wide"):
        return 0
    conn.execute("DELETE FROM firm_relation WHERE source_table = 'firm_wide_controller'")
    conn.execute(
        """
        INSERT INTO firm_relation
          (id, subject_id, subject_name, subject_type, object_uscc, object_name,
           relation_type, edge_type, direction, weight, role,
           source_table, source_id, is_direct)
        SELECT
          ROW_NUMBER() OVER (ORDER BY uscc),
          'person:' || controller_name,
          controller_name,
          'Person',
          uscc,
          name,
          'controller',
          'hasController',
          'person_to_firm',
          COALESCE(controller_stake, 1.0),
          NULL,
          'firm_wide_controller',
          NULL,
          TRUE
        FROM firm_wide
        WHERE controller_name IS NOT NULL AND controller_name != ''
        """
    )
    row = conn.execute(
        "SELECT COUNT(*) FROM firm_relation WHERE source_table = 'firm_wide_controller'"
    ).fetchone()
    return row[0] if row else 0


def backfill_ubo(conn) -> int:
    if not _table_exists(conn, "firm_wide"):
        return 0
    conn.execute("DELETE FROM firm_relation WHERE source_table = 'firm_wide_ubo'")
    conn.execute(
        """
        INSERT INTO firm_relation
          (id, subject_id, subject_name, subject_type, object_uscc, object_name,
           relation_type, edge_type, direction, weight, role,
           source_table, source_id, is_direct)
        SELECT
          ROW_NUMBER() OVER (ORDER BY uscc),
          'person:' || ubo_name,
          ubo_name,
          'Person',
          uscc,
          name,
          'ubo',
          'hasUBO',
          'person_to_firm',
          COALESCE(ubo_stake, 1.0),
          NULL,
          'firm_wide_ubo',
          NULL,
          TRUE
        FROM firm_wide
        WHERE ubo_name IS NOT NULL AND ubo_name != ''
        """
    )
    row = conn.execute(
        "SELECT COUNT(*) FROM firm_relation WHERE source_table = 'firm_wide_ubo'"
    ).fetchone()
    return row[0] if row else 0


def backfill_executive(conn) -> int:
    if not _table_exists(conn, "executive"):
        return 0
    n = conn.execute("SELECT COUNT(*) FROM executive").fetchone()[0]
    if n == 0:
        return 0
    conn.execute("DELETE FROM firm_relation WHERE source_table = 'executive'")
    conn.execute(
        """
        INSERT INTO firm_relation
          (id, subject_id, subject_name, subject_type, object_uscc, object_name,
           relation_type, edge_type, direction, weight, role,
           source_table, source_id, is_direct)
        SELECT
          id,
          'person:' || e.name,
          e.name,
          'Person',
          e.uscc,
          f.name,
          'executive',
          'hasExecutive',
          'person_to_firm',
          1.0,
          e.role,
          'executive',
          e.id,
          TRUE
        FROM executive e
        LEFT JOIN firm_wide f ON f.uscc = e.uscc
        WHERE e.name IS NOT NULL AND e.name != ''
        """
    )
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[migrate] DB not found: {DB_PATH}")
        return 1

    conn = duckdb.connect(str(DB_PATH))
    try:
        ensure_table(conn)

        if args.dry_run:
            counts = {
                "equity": _count_source(conn, "equity_edge"),
                "legal_rep": _count_source(conn, "firm_wide_legal_rep"),
                "controller": _count_source(conn, "firm_wide_controller"),
                "ubo": _count_source(conn, "firm_wide_ubo"),
                "executive": _count_source(conn, "executive"),
            }
            total = sum(counts.values())
            print(f"[migrate --dry-run] would insert ~{total} rows into firm_relation")
            for k, v in counts.items():
                print(f"  {k:<10} {v}")
            return 0

        counts = {
            "equity": backfill_equity(conn),
            "legal_rep": backfill_legal_rep(conn),
            "controller": backfill_controller(conn),
            "ubo": backfill_ubo(conn),
            "executive": backfill_executive(conn),
        }
        total = sum(counts.values())
        print(f"[migrate] firm_relation: total {total} rows")
        for k, v in counts.items():
            print(f"  {k:<10} {v}")

        # 重新编号：把整表按 source_table 排序，用 ROW_NUMBER 生成连续 id
        conn.execute(
            """
            CREATE OR REPLACE TABLE firm_relation AS
            SELECT
              ROW_NUMBER() OVER (
                ORDER BY
                  CASE source_table
                    WHEN 'equity_edge' THEN 1
                    WHEN 'firm_wide_legal_rep' THEN 2
                    WHEN 'firm_wide_controller' THEN 3
                    WHEN 'firm_wide_ubo' THEN 4
                    WHEN 'executive' THEN 5
                    ELSE 6
                  END,
                  COALESCE(source_id, 0),
                  subject_id
              ) AS id,
              subject_id, subject_name, subject_type,
              object_uscc, object_name,
              relation_type, edge_type, direction,
              weight, role, source_table, source_id,
              is_direct, discovered_at, updated_at
            FROM firm_relation
            """
        )
        # 重建索引
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_subject ON firm_relation(subject_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_object ON firm_relation(object_uscc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_type ON firm_relation(relation_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_edge ON firm_relation(edge_type)")

        final = conn.execute("SELECT COUNT(*) FROM firm_relation").fetchone()[0]
        print(f"[migrate] final row count: {final}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[migrate] ERROR: {type(e).__name__}: {e}")
        return 1
    finally:
        conn.close()
    return 0


def _count_source(conn, source_table: str) -> int:
    """只统计源表可能贡献的行数（不做写入）。"""
    mapping = {
        "firm_wide_legal_rep": "legal_rep",
        "firm_wide_controller": "controller_name",
        "firm_wide_ubo": "ubo_name",
    }
    if source_table in mapping:
        col = mapping[source_table]
        if not _table_exists(conn, "firm_wide"):
            return 0
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM firm_wide WHERE {col} IS NOT NULL AND {col} != ''"
            ).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
    if not _table_exists(conn, source_table):
        return 0
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {source_table}").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0



if __name__ == "__main__":
    sys.exit(main())
