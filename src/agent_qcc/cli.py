"""Command line entry points.

``agent-qcc`` runs a REPL backed by Rich; ``agent-qcc chat`` handles a single
question.  ``agent-qcc serve`` boots the same graph through the LangGraph
server so REST/Playground clients can reach it.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import NoReturn

import typer

from . import __version__


app = typer.Typer(help="企查查 (Qichacha) LangGraph Agent", no_args_is_help=True)

#: Binary we exec, the sub-command it runs, and the graph definition it loads.
#: The log line below prints these verbatim, so what a user reads is what is
#: exec'd — both wrappers (`agent-qcc serve`, `agent-qcc-serve`) land here.
_LANGGRAPH_BINARY = "langgraph"
_SERVE_COMMAND = "serve"
_GRAPH_TARGET = "src/agent_qcc/agent.py:graph"


def _banner(skill_name: str, offline: bool, model: str) -> None:
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    console.print(
        Panel(
            f"[bold]agent-qcc[/bold] v{__version__} — 4R+1 企业尽调 Agent\n"
            f"skill: [cyan]{skill_name}[/cyan]  model: [cyan]{model}[/cyan]  "
            f"mode: [cyan]{'offline' if offline else 'live'}[/cyan]\n"
            f"[dim]Route → Reason → Retrieve → Reflect → Respond[/dim]\n"
            f"[dim]自然语言 → 4R+1 graph；/xxx → skill slash 命令（/qcc-help 查看）[/dim]",
            title="企业尽调 Agent",
            border_style="cyan",
        )
    )


def _render(state: dict) -> None:
    from rich.console import Console
    from rich.markdown import Markdown

    console = Console()
    route = state.get("route") or {}
    plan = state.get("plan") or {}
    evidence = state.get("evidence") or []
    reflections = state.get("reflections") or []

    if route:
        console.print(
            f"[dim]route:[/dim] {route.get('intent')} · target={route.get('company') or '-'}"
        )
    calls = plan.get("tool_calls") or []
    if calls:
        names = ", ".join(f"`{call.get('tool')}`" for call in calls)
        console.print(f"[dim]plan:[/dim] {names}")
    if evidence:
        ok = sum(1 for item in evidence if item.get("ok"))
        console.print(f"[dim]evidence:[/dim] {ok}/{len(evidence)} ok")
    if reflections:
        last = reflections[-1]
        console.print(f"[dim]reflect:[/dim] {last.get('decision')} gaps={len(last.get('gaps') or [])}")

    answer = state.get("messages") or []
    text = str(answer[-1].content) if answer else "(no answer)"
    console.print()
    console.print(Markdown(text))


def _render_slash(result: dict) -> None:
    """渲染 slash 命令结果（skill 的 handler 返回纯 dict，无 markdown）。"""
    import json

    from rich.console import Console
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text

    console = Console()
    command = str(result.get("command") or "/?")
    ok = bool(result.get("ok"))
    console.print(
        f"[dim]slash:[/dim] [bold]{command}[/bold] · "
        + ("[green]ok[/green]" if ok else "[red]failed[/red]")
    )

    # 错误信息走 Text：内容可能含路径，交给 markup 解析器会把 `[` 当标签
    if not ok:
        console.print(Text(str(result.get("error") or "unknown error"), style="red"))
        if result.get("hint"):
            console.print(Text(str(result["hint"]), style="dim"))
        return

    commands = result.get("commands")
    if commands:
        table = Table(title="Slash 命令")
        table.add_column("命令", style="cyan", no_wrap=True)
        table.add_column("分类")
        table.add_column("说明")
        table.add_column("用法", style="dim")
        for item in commands:
            table.add_row(
                item.get("slash", ""),
                item.get("category", ""),
                item.get("label", ""),
                item.get("usage", ""),
            )
        console.print(table)
        return

    body = {k: v for k, v in result.items() if k not in {"command", "ok", "handler", "servers"}}
    console.print(
        Syntax(
            json.dumps(body, ensure_ascii=False, indent=2, default=str),
            "json",
            word_wrap=True,
        )
    )


def _slash_shortcut(question: str) -> bool:
    """``/xxx`` 直接走 skill 的 slash 分发器，返回 True 表示本轮已处理。

    单独拆出来是为了让单轮 ``chat`` 在构建 graph 之前就短路：slash 命令只查
    DuckDB / 知识图谱，不需要 LLM，也不该因为模型或 skill 配置缺失而失败。
    """
    from .slash_router import is_slash_command, try_dispatch_slash

    if not is_slash_command(question):
        return False
    result = try_dispatch_slash(question)
    if result is None:
        return False
    _render_slash(result)
    return True


def _turn(graph, question: str, *, thread_id: str) -> dict | None:
    """一轮输入的双通道路由。

    ``/xxx`` → skill 的 slash 分发器（直接查 DuckDB / KG，不进 LLM）；
    其余 → 原有 4R+1 graph。返回 graph state，slash 通道返回 None。
    """
    if _slash_shortcut(question):
        return None

    state = _run(graph, question, thread_id=thread_id)
    _render(state)
    return state


def _fail(message: str) -> NoReturn:
    """Print a diagnostic to stderr and exit 1 — never a blank screen.

    Without a guard the same failure surfaces as Click's rendering of an unhandled
    exception: exit 1 and nothing the user can act on.  Every error path in this
    module goes through here instead, so the user always reads *what* is missing
    and *which knob* fixes it (``serve_main`` already did this for the missing
    ``langgraph`` binary; ``chat`` / ``tui`` / ``skills`` did not).

    Plain text, no markup: these messages carry file paths, which are never safe
    to hand to Rich's markup parser (a ``[`` in a path would be parsed as a tag).
    """
    typer.secho(f"错误：{message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _factory_error_message(exc: BaseException, settings) -> str:
    """Turn a failing ``build_agent`` into a message that names the fix.

    ``load_skill`` raises ``FileNotFoundError`` for both "the skills root does
    not exist" and "no skill with that name exists", so the report repeats the
    root that was read and the exact ``SKILL.md`` expected — that is what tells
    the two cases apart — and names both settings that decide them.  Any other
    exception is reported with its type and message, the same settings attached,
    because they are where the rest of the configuration lives.
    """
    detail = str(exc) or exc.__class__.__name__
    hint = (
        "请检查 AGENT_QCC_SKILLS_DIR（skills 根目录）与 "
        "AGENT_QCC_QCC_SKILL_NAME（skill 名）的配置，"
        "或在 skills 根目录下补齐 <skill 名>/SKILL.md。"
    )
    if not isinstance(exc, FileNotFoundError):
        return f"初始化 agent 失败：{exc.__class__.__name__}: {detail}\n{hint}"

    expected = Path(settings.skills_dir) / settings.qcc_skill_name / "SKILL.md"
    return "\n".join(
        [
            f"初始化 agent 失败：{detail}",
            f"skills 根目录：{settings.skills_dir}",
            f"期望的 skill 文件：{expected}",
            hint,
        ]
    )


def _build_or_fail() -> tuple:
    """``build_agent()`` with a readable failure: exit 1 + which path is missing.

    The import is inside the ``try`` because ``agent_qcc.agent`` compiles the
    graph at module level (``langgraph serve`` needs that callable), so a bad
    ``AGENT_QCC_SKILLS_DIR`` raises while the module imports — before the command
    body runs.  Outside the ``try`` the user would read a traceback out of
    ``agent.py`` instead of this message.

    The settings are read again on the way out, so the report shows the
    configuration in force (the repo ``.env`` plus ``AGENT_QCC_*``) rather than
    the compiled-in default.  If even reading them fails, the report still names
    the exception that was caught.
    """
    try:
        from .agent import build_agent

        return build_agent()
    except Exception as exc:  # noqa: BLE001 - a misconfiguration must not print a traceback
        try:
            from .config import get_settings

            settings = get_settings()
        except Exception:  # noqa: BLE001 - a nameless failure is still better than silence
            _fail(f"初始化 agent 失败：{exc.__class__.__name__}: {exc}")
        _fail(_factory_error_message(exc, settings))


def _run(graph, question: str, *, thread_id: str) -> dict:
    """Run one turn through the agent.

    ``agent.invoke`` owns its own event loop (``asyncio.run(graph.ainvoke)``), so
    the call is a plain synchronous call: there is nothing to ``await``.  That is
    the P1 fix — ``chat`` and ``tui`` used to be ``async def``, which Typer wraps
    in a plain ``def`` that returns the coroutine without running the body, so the
    CLI printed nothing and exited 0.  These commands are ``def`` again, and this
    line is actually reached.

    Called directly rather than through ``asyncio.to_thread``: ``invoke`` starts
    its own loop, so it cannot be nested inside a running one anyway (see
    ``test_synchronous_invoke_rejects_a_loop_that_is_already_running``).
    """
    from .agent import invoke

    return invoke(graph, question, thread_id=thread_id)


@app.command()
def chat(
    question: str = typer.Argument(..., help="企业相关问题"),
    thread: str = typer.Option("cli", "--thread", help="会话线程 ID"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """单轮问答（自然语言走 4R+1，``/xxx`` 走 skill 的 slash 命令）."""
    if _slash_shortcut(question):
        return

    graph, _checkpointer, pipeline, skill, settings = _build_or_fail()
    _banner(skill.name, settings.offline, settings.model_name)
    if not settings.offline:
        console_note(
            f"[yellow]live 模式[/yellow]：将向 {pipeline.client.url} 发起真实连接器调用"
        )
    state = _turn(graph, question, thread_id=thread)
    if verbose and state is not None:
        import json

        console_note("[dim]" + json.dumps(state, ensure_ascii=False, default=str)[:2000] + "[/dim]")


@app.command()
def tui(
    thread: str = typer.Option("tui", "--thread", help="会话线程 ID"),
) -> None:
    """交互式 TUI（输入 q/quit 退出）."""
    graph, _checkpointer, pipeline, skill, settings = _build_or_fail()
    _banner(skill.name, settings.offline, settings.model_name)
    if settings.offline:
        console_note("[dim]离线演示模式：不会真的调用企查查，仅演示 4R+1 编排[/dim]")

    while True:
        question = typer.prompt("\n 你的问题").strip()
        if question.lower() in {"q", "quit", "exit", ":q"}:
            break
        if not question:
            continue
        _turn(graph, question, thread_id=thread)
    typer.echo("已退出。")


@app.command("commands")
def slash_commands() -> None:
    """列出 skill 提供的全部 slash 命令."""
    from .slash_router import list_slash_commands

    commands = list_slash_commands()
    if not commands:
        _fail(
            "无法读取 slash 命令注册表。\n"
            f"期望的文件：{Path('skills/qcc-super/references/slash_registry.json')}，"
            "以及 skills/qcc-super/scripts/slash.py 的 dispatch()。"
        )
    _render_slash({"ok": True, "command": "/qcc-help", "commands": commands})


@app.command()
def skills(
    skills_dir: Path = typer.Option(Path("skills"), "--dir", help="skills 目录"),
) -> None:
    """列出可用的 connector 型 skill."""
    from rich.console import Console
    from rich.table import Table

    from .skill import load_skill

    skills_root = Path(skills_dir).expanduser()
    if not skills_root.exists():
        _fail(
            "skills 目录不存在。"
            f"检查的路径：{skills_root}\n"
            "请检查 AGENT_QCC_SKILLS_DIR 与 AGENT_QCC_QCC_SKILL_NAME，"
            "或在 skills 根目录下补齐 <skill 名>/SKILL.md。"
        )
    if not skills_root.is_dir():
        _fail(
            f"skills 路径不是一个目录：{skills_root}\n"
            "请检查 AGENT_QCC_SKILLS_DIR 与 AGENT_QCC_QCC_SKILL_NAME。"
        )

    console = Console()
    table = Table(title="Connector skills")
    table.add_column("skill")
    table.add_column("tools")
    table.add_column("transport")
    for directory in sorted(skills_root.iterdir()):
        if not (directory / "SKILL.md").exists():
            continue
        try:
            skill = load_skill(skills_root, directory.name)
        except Exception as exc:  # noqa: BLE001 - a broken skill should not hide the rest
            table.add_row(directory.name, f"broken: {exc}", "-")
            continue
        table.add_row(skill.name, str(len(skill.tools)), f"{skill.transport.get('type')} {skill.transport.get('url')}")
    console.print(table)


def serve_main(
    host: str = "127.0.0.1",
    port: int = 2024,
) -> None:
    """通过 `langgraph serve` 暴露 REST API / LangGraph Playground。

    Both entry points of this module are Typer commands, so each one resolves its
    own options and passes the resolved values in: the `agent-qcc serve` sub-command
    and the `agent-qcc-serve` console script.  ``serve_command`` therefore calls
    ``serve_main(host, port)`` — never with no arguments.  Default values must live
    in the parameter defaults (as here), not in ``typer.Option(...)`` defaults:
    this function is also called from code, and a bare call used to receive Typer
    ``OptionInfo`` objects instead of values, so ``" ".join(args)`` raised
    ``TypeError`` before ``os.execvp`` could even run.

    The values are stringified before being joined, so an uncast ``host`` /
    ``port`` can never reach the log line or ``argv``.  ``shlex.quote`` makes the
    printed command re-runnable.
    """
    argv = [
        _SERVE_COMMAND,
        _GRAPH_TARGET,
        "--host",
        str(host),
        "--port",
        str(port),
    ]
    typer.echo(f"$ {_LANGGRAPH_BINARY} " + " ".join(shlex.quote(part) for part in argv))
    try:
        os.execvp(_LANGGRAPH_BINARY, [_LANGGRAPH_BINARY, *argv])
    except OSError as exc:
        typer.secho(f"无法启动 langgraph CLI: {exc}", fg=typer.colors.RED, err=True)
        typer.secho(
            "请先安装: uv pip install -e '.[dev]'",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1) from exc


@app.command("serve")
def serve_command(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    port: int = typer.Option(2024, "--port", help="监听端口"),
) -> None:
    """通过 `langgraph serve` 暴露 REST API。"""
    serve_main(host, port)


def console_note(message: str) -> None:
    from rich.console import Console

    Console().print(message)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
