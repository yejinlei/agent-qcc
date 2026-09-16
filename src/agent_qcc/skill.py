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


def load_skill(skills_dir: Path, skill_name: str) -> Skill:
    """Load ``skills/<skill_name>`` and fail loudly when it is incomplete."""
    directory = Path(skills_dir) / skill_name
    skill_file = directory / "SKILL.md"
    if not skill_file.exists():
        raise FileNotFoundError(f"connector skill not found: {skill_file}")

    text = skill_file.read_text(encoding="utf-8")
    metadata, body = split_front_matter(text)
    tools = parse_tools(body)

    transport: dict[str, object] = {}
    transport_file = directory / "mcp.json"
    if transport_file.exists():
        payload = json.loads(transport_file.read_text(encoding="utf-8"))
        servers = payload.get("mcpServers") or {}
        server = servers.get(skill_name) or (next(iter(servers.values())) if servers else {})
        transport = {
            "type": server.get("type", "streamableHttp"),
            "url": server.get("url"),
            "server": next(iter(servers.keys()), skill_name),
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
