"""Slash 命令路由器：拦截 /xxx 输入，分发到 qcc-super skill 的 slash.py。

设计：
- Agent 的 CLI/TUI 层先调 ``try_dispatch_slash(user_input)``
- 返回 dict → slash 命令已处理，直接渲染结果
- 返回 None → 不是 slash 命令，走 4R+1 自然语言 graph

支持的命令族（25 条，见 skills/qcc-super/references/slash_registry.json）：
- 核心查询: /qcc-profile /qcc-risk-scan /qcc-ubo /qcc-equity ...
- 元命令: /qcc-help /qcc-tool-search /raw-sql
- 知识图谱: /kg-build /kg-query /kg-validate /kg-infer /kg-export /kg-install
- 官方预设: /credit-due-diligence /company-risk-report ...
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

# qcc-super skill 的 slash.py 路径
_SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "qcc-super"
_SLASH_PY = _SKILL_ROOT / "scripts" / "slash.py"

# 缓存已加载的 dispatch 函数
_dispatch_fn = None


def _load_dispatch():
    """动态加载 skills/qcc-super/scripts/slash.py 的 dispatch 函数。"""
    global _dispatch_fn
    if _dispatch_fn is not None:
        return _dispatch_fn
    if not _SLASH_PY.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("qcc_slash", _SLASH_PY)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules["qcc_slash"] = module
        spec.loader.exec_module(module)
        _dispatch_fn = getattr(module, "dispatch", None)
        return _dispatch_fn
    except Exception:
        return None


def is_slash_command(user_input: str) -> bool:
    """判断输入是否是 slash 命令（以 / 开头且非空）。"""
    text = (user_input or "").strip()
    return text.startswith("/") and len(text) > 1


def try_dispatch_slash(user_input: str) -> dict[str, Any] | None:
    """尝试分发 slash 命令。

    Returns:
        dict: 命令执行结果（含 ok / command / handler 等字段）
        None: 不是 slash 命令，或 slash.py 加载失败
    """
    if not is_slash_command(user_input):
        return None

    dispatch = _load_dispatch()
    if dispatch is None:
        return {
            "ok": False,
            "error": f"无法加载 slash 分发器：{_SLASH_PY}",
            "hint": "请确认 skills/qcc-super/scripts/slash.py 存在",
        }

    text = user_input.strip()
    # 拆分命令和参数：/qcc-profile 浙江胶友科技有限公司 → cmd="/qcc-profile", rest="浙江胶友科技有限公司"
    parts = text.split(None, 1)
    cmd = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    try:
        result = dispatch(cmd, rest)
        if not isinstance(result, dict):
            result = {"ok": True, "data": result}
        result.setdefault("command", cmd)
        return result
    except Exception as exc:
        return {
            "ok": False,
            "command": cmd,
            "error": f"{type(exc).__name__}: {exc}",
        }


def list_slash_commands() -> list[dict[str, Any]]:
    """列出所有可用的 slash 命令（用于 /help 或自动补全）。"""
    dispatch = _load_dispatch()
    if dispatch is None:
        return []
    try:
        result = dispatch("/qcc-help", "")
        return result.get("commands") or []
    except Exception:
        return []
