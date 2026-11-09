"""Load a *connector-type* skill from the ``skills/`` directory.

A connector skill is a directory that describes an external service (here the
Qichacha / 企查查 connector) through a ``SKILL.md`` front-matter plus an
optional ``mcp.json`` transport descriptor.  The agent reads both at runtime so
that the graph never hard-codes a provider endpoint.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


FRONT_MATTER_RE = re.compile(r"^---\s*\r?\n(?P<body>.*?)\r?\n---\s*\r?\n", re.DOTALL)
# ``[\d.、\s]*`` must not use ``\w``: unicode ``\w`` would swallow the Chinese title.
TOOL_HEADING_RE = re.compile(
    r"^#{2,4}\s*[\d.、\s]*\s*(?P<title>[^`]+?)\s*`(?P<tool>[A-Za-z_][A-Za-z0-9_-]*)`\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ToolSpec:
    """One callable connector tool."""

    name: str
    title: str
    summary: str
    requires: tuple[str, ...] = ()
    ordinal: int = 0
    server: str = ""  # MCP server 名（qcc-company / qcc-risk 等），用于多 server 路由


@dataclass(frozen=True)
class Skill:
    """A loaded connector skill."""

    name: str
    directory: Path
    display_name: str
    description: str
    transport: dict[str, object]
    tools: tuple[ToolSpec, ...] = ()
    raw: str = field(default="", repr=False)

    def tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools]

    def catalog(self) -> list[dict[str, object]]:
        """Machine-readable catalog used by the ``reason`` stage."""
        return [
            {
                "name": tool.name,
                "title": tool.title,
                "summary": tool.summary,
                "required_args": list(tool.requires),
            }
            for tool in self.tools
        ]

    def as_prompt(self) -> str:
        lines = [f"Connector skill: {self.display_name} ({self.name})", f"Description: {self.description}"]
        lines.append("Available tools:")
        for tool in self.tools:
            args = ", ".join(tool.requires) or "企业全称或统一社会信用代码"
            lines.append(f"- `{tool.name}` {tool.title}: {tool.summary} [args: {args}]")
        return "\n".join(lines)


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Parse a flat ``key: value`` YAML front matter block."""
    metadata: dict[str, str] = {}
    match = FRONT_MATTER_RE.match(text)
    if match is None:
        return metadata, text
    for line in match.group("body").splitlines():
        if not line.strip() or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')
    return metadata, text[match.end() :]


def _extract_summary(body: str, heading_end: int) -> str:
    """First meaningful paragraph after a tool heading, backticks stripped."""
    chunk = body[heading_end : heading_end + 1200]
    chunk = re.split(r"^#{1,6}\s+", chunk, maxsplit=1, flags=re.MULTILINE)[0]
    for line in chunk.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("- ", ">", "**示例", "**适用", "**参数", "**⚠️", "-")):
            continue
        if stripped.startswith(("---", "```")):
            break
        cleaned = re.sub(r"[`*_]", "", stripped)
        cleaned = re.sub(r"查询(.+?)(?:。|$)", r"查询\1。", cleaned)
        return cleaned[:180]
    return ""


def _requires_from_body(body: str, heading_end: int) -> tuple[str, ...]:
    """Pull explicit argument names such as ``searchKey`` from the tool body."""
    chunk = body[heading_end : heading_end + 1500]
    chunk = re.split(r"^#{1,6}\s+", chunk, maxsplit=1, flags=re.MULTILINE)[0]
    names = re.findall(r"`(name|searchKey|creditCode|keyword)`", chunk)
    return tuple(dict.fromkeys(names))


def parse_tools(body: str) -> tuple[ToolSpec, ...]:
    """Extract the tool catalogue from a ``SKILL.md`` body."""
    tools: list[ToolSpec] = []
    matches = list(TOOL_HEADING_RE.finditer(body))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        title = match.group("title").strip()
        tools.append(
            ToolSpec(
                name=match.group("tool"),
                title=title,
                summary=_extract_summary(body, match.end()),
                requires=_requires_from_body(body, match.end()),
                ordinal=index + 1,
            )
        )
    return tuple(tools)


def parse_tools_from_json(json_path: Path) -> tuple[ToolSpec, ...]:
    """从 tool_fields.json 加载工具目录（qcc-super 专用）。

    JSON 结构: {"stats": {...}, "records": [{"server", "tool", "title", "description_clean", "input_params", ...}]}
    """
    if not json_path.exists():
        return ()
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ()
    records = payload.get("records") or []
    tools: list[ToolSpec] = []
    for index, rec in enumerate(records):
        name = str(rec.get("tool") or "").strip()
        if not name:
            continue
        # 提取必填参数（input_params 里 required=true 的）
        requires: list[str] = []
        for param in rec.get("input_params") or []:
            if isinstance(param, dict) and param.get("required"):
                pname = str(param.get("name") or "").strip()
                if pname:
                    requires.append(pname)
        tools.append(
            ToolSpec(
                name=name,
                title=str(rec.get("title") or name),
                summary=str(rec.get("description_clean") or "")[:180],
                requires=tuple(requires) or ("searchKey",),
                ordinal=index + 1,
                server=str(rec.get("server") or ""),
            )
        )
    return tuple(tools)


def load_skill(skills_dir: Path, skill_name: str) -> Skill:
    """Load ``skills/<skill_name>`` and fail loudly when it is incomplete.

    工具目录加载优先级：
    1. SKILL.md 里的 ``## N. 标题 \`tool_name\`` 格式（qcc-company 等传统 skill）
    2. references/tool_fields.json（qcc-super 等新版 skill，169 工具）

    transport 加载：
    - mcp.json 里的 mcpServers，支持多 server（qcc-super 有 11 个）
    - 单 server 时 transport.url 直接可用；多 server 时 transport.servers 保存映射
    """
    directory = Path(skills_dir) / skill_name
    skill_file = directory / "SKILL.md"
    if not skill_file.exists():
        raise FileNotFoundError(f"connector skill not found: {skill_file}")

    text = skill_file.read_text(encoding="utf-8")
    metadata, body = split_front_matter(text)

    # 1. 先尝试从 SKILL.md 解析工具
    tools = parse_tools(body)

    # 2. 解析不到则回退到 tool_fields.json
    if not tools:
        json_path = directory / "references" / "tool_fields.json"
        tools = parse_tools_from_json(json_path)

    # 3. 加载 transport（支持多 server）
    transport: dict[str, object] = {}
    transport_file = directory / "mcp.json"
    if transport_file.exists():
        payload = json.loads(transport_file.read_text(encoding="utf-8"))
        servers = payload.get("mcpServers") or {}
        if len(servers) == 1:
            # 单 server：兼容旧逻辑
            server = next(iter(servers.values()))
            transport = {
                "type": server.get("type", "streamableHttp"),
                "url": server.get("url"),
                "server": next(iter(servers.keys()), skill_name),
            }
        elif servers:
            # 多 server：保存映射，url 取第一个作为默认
            first_name = next(iter(servers.keys()))
            first_server = servers[first_name]
            transport = {
                "type": first_server.get("type", "streamableHttp"),
                "url": first_server.get("url"),
                "server": first_name,
                "servers": {
                    name: {
                        "type": s.get("type", "streamableHttp"),
                        "url": s.get("url"),
                    }
                    for name, s in servers.items()
                },
            }

    return Skill(
        name=metadata.get("name", skill_name),
        directory=directory,
        display_name=metadata.get("display_name") or metadata.get("display_name_en") or skill_name,
        description=metadata.get("description_zh") or metadata.get("description") or "",
        transport=transport,
        tools=tools,
        raw=text,
    )
