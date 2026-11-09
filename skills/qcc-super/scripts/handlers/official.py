# -*- coding: utf-8 -*-
"""官方预设 slash 命令的分发器。

这些命令（/credit-due-diligence /kyb-verification-qcc 等 27 个官方 skill）
在当前 skill 内不做完整实现，只返回：
  · 该命令声明的 MCP server + 工具集合
  · 场景说明
  · 效率评估

Agent 收到后按其建议依次调用对应 MCP 工具完成尽调。
"""


def official_dispatch(args: dict, cmd: dict) -> dict:
    target = args.get("target")
    return {
        "ok": True,
        "official": True,
        "target": target,
        "scene": cmd.get("scene"),
        "servers": cmd.get("servers", []),
        "efficiency": cmd.get("efficiency"),
        "instruction": (
            f"该命令是企查查官方 skill ({cmd['slash']}) 的路由声明。"
            f"Agent 应按下列 servers 依次调用对应 MCP 工具：{cmd.get('servers', [])}。"
            f"如需完整尽调流程，请加载官方 skill 文档："
            f"https://agent.qcc.com/skill/v1/{cmd.get('category', '').replace('official-', '')}/README.md"
        ),
        "docs_url": f"https://agent.qcc.com/skill/v1/{cmd.get('category', '').replace('official-', '')}/README.md",
    }
