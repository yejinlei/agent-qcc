#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
qcc-super slash 命令入口

用法:
  python slash.py "/qcc-profile 字节跳动"
  python slash.py "/qcc-ubo 字节跳动 --threshold 0.25"
  python slash.py "/kg-build 字节跳动 --depth 2"

Agent 也可直接 import:
  from slash import dispatch
  result = dispatch("/qcc-profile", "字节跳动")
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SKILL_ROOT = HERE.parent
REGISTRY_PATH = SKILL_ROOT / "references" / "slash_registry.json"
DB_PATH = SKILL_ROOT / "data" / "qcc.db"

# 让同级 handlers/ 可 import
sys.path.insert(0, str(HERE))

from handlers import (  # noqa: E402
    qcc_profile, qcc_risk_scan, qcc_ubo, qcc_equity, qcc_executive,
    qcc_events, qcc_financial, qcc_ipr,
    tool_search, raw_sql,
    kg_install, kg_build, kg_query, kg_validate, kg_infer, kg_export,
    kg_visualize, kg_infer_visualize, kg_semantica_probe, kg_traverse,
    kg_semantica_ontology, kg_semantica_reason, kg_semantica_requirements,
    kg_semantica_visualize,
    official_dispatch,
)

HANDLERS = {
    "qcc_profile": qcc_profile,
    "qcc_risk_scan": qcc_risk_scan,
    "qcc_ubo": qcc_ubo,
    "qcc_equity": qcc_equity,
    "qcc_executive": qcc_executive,
    "qcc_events": qcc_events,
    "qcc_financial": qcc_financial,
    "qcc_ipr": qcc_ipr,
    "tool_search": tool_search,
    "raw_sql": raw_sql,
    "kg_install": kg_install,
    "kg_build": kg_build,
    "kg_query": kg_query,
    "kg_validate": kg_validate,
    "kg_infer": kg_infer,
    "kg_export": kg_export,
    "kg_visualize": kg_visualize,
    "kg_infer_visualize": kg_infer_visualize,
    "kg_semantica_probe": kg_semantica_probe,
    "kg_traverse": kg_traverse,
    "kg_semantica_ontology": kg_semantica_ontology,
    "kg_semantica_reason": kg_semantica_reason,
    "kg_semantica_requirements": kg_semantica_requirements,
    "kg_semantica_visualize": kg_semantica_visualize,
    "official_dispatch": official_dispatch,
}


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        raise FileNotFoundError(f"slash registry not found: {REGISTRY_PATH}")
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def parse_args(line: str) -> list:
    """极简 shell 式分词，支持双引号保留含空格字段。"""
    return re.findall(r'"[^"]*"|\'[^\']*\'|\S+', line)


def normalize_args(tokens: list, arg_specs: list) -> dict:
    """把 tokens 转成 {arg_name: value}，处理 --key value 和位置参数。"""
    result = {spec["name"]: spec.get("default") for spec in arg_specs}

    # 第一遍：提取所有 --key value 命名的参数，并记录被消费的位置索引
    named: list = []          # [(key, value), ...]
    consumed: set = set()     # 已被 --key 或 --key value 消耗的位置索引
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("--") and i + 1 < len(tokens):
            next_tok = tokens[i + 1]
            # 后面紧跟一个非 -- 的 token → 当作 value
            if not next_tok.startswith("--"):
                named.append((t[2:], next_tok))
                consumed.add(i + 1)
                i += 2
                continue
            else:
                # 后面紧跟 -- 或已到末尾 → 当作布尔 flag
                named.append((t[2:], True))
                i += 1
                continue
        i += 1

    # 位置参数：排除 --flag 本身 + 被 --key value 消费掉的 value
    positional: list = []
    for idx, t in enumerate(tokens):
        if t.startswith("--"):
            continue
        if idx in consumed:
            continue
        positional.append(t)

    # 按 arg_specs 顺序填位置参数（跳过已有命名匹配的参数）
    named_keys = {k for k, _ in named}
    pos_idx = 0
    for spec in arg_specs:
        name = spec["name"]
        if name in named_keys:
            continue
        if pos_idx < len(positional):
            result[name] = positional[pos_idx]
            pos_idx += 1

    # 最后再覆盖一遍命名参数（含 flag=True），保证优先级
    for k, v in named:
        result[k] = v
    return result


def _convert_types(args: dict, arg_specs: list) -> dict:
    """按 default 类型把字符串转成 int/float/bool。"""
    for spec in arg_specs:
        name = spec["name"]
        val = args.get(name)
        if val is None:
            continue
        default = spec.get("default")
        if isinstance(default, (int, float)) and not isinstance(default, bool):
            try:
                args[name] = float(val) if isinstance(default, float) else int(val)
            except (TypeError, ValueError):
                pass
        elif isinstance(default, bool):
            if isinstance(val, str):
                args[name] = val.lower() in ("1", "true", "yes", "y")
    return args


def list_commands() -> list:
    reg = load_registry()
    return [
        {
            "slash": c["slash"],
            "label": c["label"],
            "category": c["category"],
            "usage": c["usage"],
            "official": c.get("official", False),
        }
        for c in reg["commands"]
    ]


def dispatch(slash_cmd: str, rest: str = "") -> dict:
    """执行一个 slash 命令，返回统一结果 dict。"""
    reg = load_registry()
    commands = {c["slash"]: c for c in reg["commands"]}

    if slash_cmd.startswith("/help") or slash_cmd == "/qcc-help":
        return {"ok": True, "commands": list_commands()}

    cmd = commands.get(slash_cmd)
    if not cmd:
        # 尝试通过 label/aliases 模糊匹配
        for c in reg["commands"]:
            if c["label"] == slash_cmd.lstrip("/"):
                cmd = c
                break
            if any(a == slash_cmd.lstrip("/") for a in c.get("aliases", [])):
                cmd = c
                break

    if not cmd:
        return {
            "ok": False,
            "error": f"Unknown command: {slash_cmd}",
            "hint": "Run /qcc-help to see available commands.",
        }

    tokens = parse_args(rest) if rest else []
    args = normalize_args(tokens, cmd.get("args", []))
    args = _convert_types(args, cmd.get("args", []))

    handler = HANDLERS.get(cmd["handler"])
    if not handler:
        return {"ok": False, "error": f"No handler for: {cmd['handler']}"}

    try:
        result = handler(args, cmd)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "command": slash_cmd}

    result.setdefault("ok", True)
    result["command"] = slash_cmd
    result["handler"] = cmd["handler"]
    result["servers"] = cmd.get("servers", [])
    return result


def main():
    """CLI 入口。用法: python slash.py '/qcc-profile 字节跳动' [--json]"""
    ap = argparse.ArgumentParser(description="qcc-super slash 命令入口", add_help=False)
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="完整 slash 命令，含参数")
    args = ap.parse_args()

    tokens = list(args.cmd or [])
    # 过滤掉 --json 之类的全局 flag（保留业务 flag）
    tokens = [t for t in tokens if t != "--json"]
    line = " ".join(tokens).strip() or "/qcc-help"

    parts = line.split(None, 1)
    slash_cmd = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    result = dispatch(slash_cmd, rest)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
