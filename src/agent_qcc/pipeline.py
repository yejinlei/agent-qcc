"""The 4R+1 pipeline: Route -> Reason -> Retrieve -> Reflect -> Respond."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.messages import AIMessage

from .config import Settings
from .llm import LlmClient, make_llm
from .prompts import (
    CHITCHAT_SYSTEM,
    PLAN_SYSTEM,
    REFLECT_SYSTEM,
    RESPOND_SYSTEM,
    ROUTE_SYSTEM,
)
from .qcc import QccClient
from .skill import Skill
from .state import AgentState, Evidence


ASPECT_TOOLS = {
    "简介": "get_company_profile",
    "主营业务": "get_company_profile",
    "行业": "get_company_profile",
    "工商": "get_company_registration_info",
    "登记": "get_company_registration_info",
    "注册资本": "get_company_registration_info",
    "成立": "get_company_registration_info",
    "法定代表人": "get_company_registration_info",
    "法人": "get_company_registration_info",
    "存续": "get_company_registration_info",
    "核实": "verify_company_accuracy",
    "身份": "verify_company_accuracy",
    "股东": "get_shareholder_info",
    "股权": "get_shareholder_info",
    "实控人": "get_actual_controller",
    "实际控制人": "get_actual_controller",
    "受益所有人": "get_beneficial_owners",
    "穿透": "get_beneficial_owners",
    "高管": "get_key_personnel",
    "董事": "get_key_personnel",
    "管理层": "get_key_personnel",
    "对外投资": "get_external_investments",
    "变更": "get_change_records",
    "历史": "get_change_records",
    "财务": "get_financial_data",
    "年报": "get_annual_reports",
    "上市": "get_listing_info",
    "分支机构": "get_branches",
    "分公司": "get_branches",
    "联系方式": "get_contact_info",
    "官网": "get_contact_info",
    "电话": "get_contact_info",
    "开票": "get_tax_invoice_info",
    "税号": "get_tax_invoice_info",
    "开户行": "get_tax_invoice_info",
}


CORE_TOOLS = ("get_company_registration_info", "get_shareholder_info", "get_actual_controller")


class Pipeline:
    """Nodes of the 4R+1 graph. Kept as a class so state is explicit."""

    def __init__(self, skill: Skill, settings: Settings) -> None:
        self.skill = skill
        self.settings = settings
        self.client = QccClient(skill, settings)
        self.llm: LlmClient | None = make_llm(settings)

    # -- R0 Slash ---------------------------------------------------------
    async def slash(self, state: AgentState) -> dict[str, Any]:
        """``/xxx`` 直通 skill 的 slash 分发器，不进 LLM、不走 4R+1。

        永远返回 ``slash_result`` 键：同一 thread 的上一轮可能留下结果，
        写回空 dict 才能把 graph 的条件边重新指回 ``route``。
        """
        from .slash_router import try_dispatch_slash

        result = try_dispatch_slash(self._user_text(state))
        if result is None:
            return {"slash_result": {}}
        return {
            "slash_result": result,
            "messages": [AIMessage(content=slash_markdown(result))],
        }

    # -- R1 Route ---------------------------------------------------------
    async def route(self, state: AgentState) -> dict[str, Any]:
        user_text = self._user_text(state)
        result = await self._llm_json(ROUTE_SYSTEM, user_text, key="intent") or {}
        if not result.get("company"):
            result["company"] = self._guess_company(user_text)
        company = str(result.get("company") or "").strip()
        route = {
            "intent": result.get("intent") or self._infer_intent(user_text),
            "company": company,
            "aspects": result.get("aspects") or self._infer_aspects(user_text),
            "missing": result.get("missing") or [],
            "need_clarification": bool(result.get("need_clarification", not company)),
            "reason": result.get("reason") or "规则路由（离线模式）",
        }
        return {"route": route}

    # -- R2 Reason --------------------------------------------------------
    async def reason(self, state: AgentState) -> dict[str, Any]:
        route = state.get("route") or {}
        company = str(route.get("company") or "")
        aspects = list(route.get("aspects") or [])
        intent = str(route.get("intent") or "")

        # Try LLM plan first, constrained to the skill's tool catalog.
        llm_plan = None
        if company and self.llm is not None:
            catalog = self.skill.catalog()
            human = json.dumps(
                {"target": company, "aspects": aspects, "intent": intent, "tools": catalog},
                ensure_ascii=False,
            )
            llm_plan = await self._llm_json(PLAN_SYSTEM, human)
            if llm_plan and self._validate_plan(llm_plan, company):
                return {"plan": llm_plan}

        wanted = self._tools_for(aspects, intent)
        ordered = [tool for tool in self.client.tools if tool in wanted]
        if not ordered:
            ordered = [tool for tool in CORE_TOOLS if tool in self.client.tools]
        ordered = ordered[:8]

        plan = {
            "target": company,
            "tool_calls": [
                {
                    "tool": tool,
                    "args": self._args_for(tool, company),
                    "reason": self._why(tool, aspects, intent),
                }
                for tool in ordered
            ],
            "missing": list(route.get("missing") or []),
            "notes": f"为 {company or '未指定企业'} 规划 {len(ordered)} 次连接器调用",
        }
        return {"plan": plan}

    # -- R3 Retrieve ------------------------------------------------------
    async def retrieve(self, state: AgentState) -> dict[str, Any]:
        calls = (state.get("plan") or {}).get("tool_calls") or []
        valid_calls = [
            (str(call.get("tool") or ""), call.get("args") or {})
            for call in calls
            if call.get("tool")
        ]
        if not valid_calls:
            return {"evidence": []}

        sem = asyncio.Semaphore(5)

        async def _call_one(name: str, args: dict[str, Any]) -> Evidence:
            async with sem:
                result = await self.client.call(name, args)
                return result.to_evidence()

        results = await asyncio.gather(*[_call_one(name, args) for name, args in valid_calls])
        return {"evidence": list(results)}

    # -- R4 Reflect -------------------------------------------------------
    async def reflect(self, state: AgentState) -> dict[str, Any]:
        route = state.get("route") or {}
        plan = state.get("plan") or {}
        evidence = list(state.get("evidence") or [])

        if not str(route.get("company") or "").strip():
            return {
                "decision": "clarify",
                "reflections": [
                    {
                        "iteration": state.get("iteration", 0) + 1,
                        "decision": "clarify",
                        "coverage": [],
                        "gaps": ["缺少企业名称或统一社会信用代码"],
                        "fixes": [],
                        "answer_notes": "先向用户确认查询主体",
                    }
                ],
            }

        # LLM reflection with rule-based fallback.
        if self.llm is not None and evidence:
            human = json.dumps(
                {"route": route, "evidence": evidence, "iteration": state.get("iteration", 0)},
                ensure_ascii=False,
                default=str,
            )
            llm_reflect = await self._llm_json(REFLECT_SYSTEM, human)
            if llm_reflect and llm_reflect.get("decision"):
                decision = str(llm_reflect["decision"])
                iteration = int(state.get("iteration", 0)) + 1
                if decision == "revise" and iteration <= self.settings.max_reflect_retries:
                    fixes = llm_reflect.get("fixes") or []
                    revised_plan = {
                        **plan,
                        "tool_calls": [
                            {"tool": f.get("tool"), "args": f.get("args") or {}, "reason": f.get("reason", "")}
                            for f in fixes
                        ],
                    }
                    return {
                        "decision": "revise",
                        "iteration": iteration,
                        "reflections": [llm_reflect],
                        "plan": revised_plan,
                    }
                return {
                    "decision": decision if decision in ("respond", "clarify", "chitchat") else "respond",
                    "iteration": iteration,
                    "reflections": [llm_reflect],
                }

        # Rule-based fallback.
        company = str(route.get("company") or "").strip()
        wanted = {str(item) for item in route.get("aspects") or []}
        covered = [
            e.get("tool")
            for e in evidence
            if e.get("ok") and (not company or not e.get("target") or e.get("target") == company)
        ]
        gaps = self._gaps(wanted, covered, evidence)
        iteration = int(state.get("iteration", 0)) + 1

        if gaps and iteration <= self.settings.max_reflect_retries:
            fixes = self._fixes(gaps, plan, evidence)
            return {
                "decision": "revise",
                "iteration": iteration,
                "reflections": [
                    {
                        "iteration": iteration,
                        "decision": "revise",
                        "coverage": covered,
                        "gaps": gaps,
                        "fixes": fixes,
                        "answer_notes": "补充调用后再次反思",
                    }
                ],
                "plan": {
                    **plan,
                    "tool_calls": [
                        {"tool": fix["tool"], "args": fix.get("args") or {}, "reason": fix.get("reason", "")}
                        for fix in fixes
                    ],
                },
            }

        return {
            "decision": "respond",
            "iteration": iteration,
            "reflections": [
                {
                    "iteration": iteration,
                    "decision": "respond",
                    "coverage": covered,
                    "gaps": gaps,
                    "fixes": [],
                    "answer_notes": "证据充分或无法进一步补充，进入作答",
                }
            ],
        }

    # -- +1 Respond -------------------------------------------------------
    async def respond(self, state: AgentState) -> dict[str, Any]:
        route = state.get("route") or {}
        decision = state.get("decision") or "respond"

        if decision == "clarify":
            text = self._clarify_text(route)
        elif decision == "chitchat":
            text = await self._chitchat(state) or (
                "这个问题超出我的能力边界。我专注于中国境内企业的工商信息查询，"
                "例如：查一下某公司的注册资本与股东结构。"
            )
        else:
            text = await self._render(state)

        return {"decision": decision, "messages": [AIMessage(content=text)]}

    # -- LLM helpers ------------------------------------------------------

    async def _llm_json(self, system: str, human: str, *, key: str | None = None) -> dict[str, Any] | None:
        if self.llm is None:
            return None
        return await self.llm.invoke_json(system, human, key=key)

    def _validate_plan(self, plan: dict[str, Any], company: str) -> bool:
        """Reject LLM plans that invent tools or omit required args."""
        calls = plan.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            return False
        for call in calls:
            name = str(call.get("tool") or "")
            if name not in self.client.tools:
                return False
            spec = self.client.tools[name]
            args = call.get("args") or {}
            if not isinstance(args, dict) or not args:
                return False
            missing = [arg for arg in spec.requires if not args.get(arg)]
            if missing:
                return False
        return True

    def _args_for(self, tool: str, company: str) -> dict[str, Any]:
        """Build connector args for rule-based plans using the tool's first required arg.

        qcc-super tools mostly require ``searchKey``; qcc-company tools require ``name``.
        """
        if not company:
            return {}
        spec = self.client.tools.get(tool)
        requires = spec.requires if spec is not None else ()
        key = requires[0] if requires else "name"
        return {key: company}

    # -- helpers ----------------------------------------------------------
    def _user_text(self, state: AgentState) -> str:
        messages = state.get("messages") or []
        for message in reversed(messages):
            if message.type == "human":
                return str(message.content)
        return ""

    @staticmethod
    def _guess_company(text: str) -> str:
        import re

        # 优先匹配 18 位统一社会信用代码 (USCC)
        uscc_match = re.search(r"\b([0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10})\b", text, re.IGNORECASE)
        if uscc_match:
            return uscc_match.group(1).upper()

        match = re.search(r"[\u4e00-\u9fa5A-Za-z0-9（）()]{4,30}(?:股份有限公司|有限公司|集团|公司|中心|事务所|合伙企业)", text)
        return match.group(0) if match else ""

    @staticmethod
    def _infer_intent(text: str) -> str:
        if not any(token in text for token in ("公司", "企业", "股", "资本", "法人", "工商")):
            return "chitchat"
        tokens = [aspect for aspect in ASPECT_TOOLS if aspect in text]
        return "multi" if len(tokens) > 1 else "company_info"

    @staticmethod
    def _infer_aspects(text: str) -> list[str]:
        return [aspect for aspect in ASPECT_TOOLS if aspect in text]

    @staticmethod
    def _tools_for(aspects: list[str], intent: str) -> list[str]:
        wanted: list[str] = []
        for aspect in aspects:
            for token, tool in ASPECT_TOOLS.items():
                if token in aspect and tool not in wanted:
                    wanted.append(tool)
        if intent in ("multi", "all") and not wanted:
            wanted.extend(CORE_TOOLS)
        return wanted

    @staticmethod
    def _why(tool: str, aspects: list[str], intent: str) -> str:
        label = next((item for item in aspects if ASPECT_TOOLS.get(item) == tool), tool)
        return f"覆盖需求「{label}」（意图 {intent}）"

    @staticmethod
    def _gaps(
        wanted: set[str], covered: list[str], evidence: list[dict[str, Any]]
    ) -> list[str]:
        if not evidence:
            return ["连接器没有任何返回，需检查调用参数"]
        failures = [f"{e.get('tool')}: {e.get('error')}" for e in evidence if not e.get("ok")]
        if failures and len(failures) == len(evidence):
            return [f"全部调用失败：{failures[0]}"]
        return []

    @staticmethod
    def _fixes(
        gaps: list[str], plan: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return []

    def _clarify_text(self, route: dict[str, Any]) -> str:
        missing = route.get("missing") or ["企业名称全称或统一社会信用代码"]
        joined = "、".join(str(item) for item in missing)
        return f"请补充：{joined}。给我之后我会立刻通过企查查连接器查询。"

    async def _chitchat(self, state: AgentState) -> str | None:
        if self.llm is None:
            return None
        return await self.llm.invoke_text(CHITCHAT_SYSTEM, self._user_text(state))

    async def _render(self, state: AgentState) -> str:
        route = state.get("route") or {}
        evidence = state.get("evidence") or []
        target = str(route.get("company") or "")
        reflections = state.get("reflections") or []

        payload = {
            "user_request": self._user_text(state),
            "target": target,
            "evidence": evidence,
            "reflection": reflections[-1] if reflections else {},
        }

        if self.llm is not None:
            answer = await self.llm.invoke_text(
                RESPOND_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str)
            )
            if answer:
                return answer
        return self._render_rules(payload)

    @staticmethod
    def _render_rules(payload: dict[str, Any]) -> str:
        evidence = payload.get("evidence") or []
        target = str(payload.get("target") or "")
        request = str(payload.get("user_request") or "")
        reflection = payload.get("reflection") or {}

        lines = [f"# {target or '企业查询'} — 编排结果（规则渲染）", ""]
        lines.append(f"> 用户请求：{request}")
        lines.append(">")

        if not evidence:
            lines.append("**暂无证据**：连接器没有返回任何数据。")
        else:
            ok = [item for item in evidence if item.get("ok")]
            failed = [item for item in evidence if not item.get("ok")]
            lines.append(f"共发起 {len(evidence)} 次连接器调用，成功 {len(ok)} 次，失败 {len(failed)} 次。")
            lines.append("")
            for item in evidence:
                marker = "✅" if item.get("ok") else "⚠️"
                detail = item.get("data") if item.get("ok") else item.get("error")
                summary = detail if isinstance(detail, str) else _truncate(detail)
                lines.append(f"- {marker} `{item.get('tool')}` ({item.get('duration_ms')}ms): {summary}")

        gaps = reflection.get("gaps") or []
        if gaps:
            lines.extend(["", "## 缺口", *[f"- {gap}" for gap in gaps]])

        lines.extend(
            [
                "",
                "---",
                "数据时效提示：工商登记数据为 T+0 同步；财务与年报依赖企业公示周期，可能存在滞后。",
            ]
        )
        return "\n".join(lines)


def slash_markdown(result: dict[str, Any]) -> str:
    """把 slash 结果渲染成 markdown，供 REST / Playground 客户端消费。

    CLI 有自己的 Rich 渲染（``cli._render_slash``），这里只负责把同一份 dict
    变成 ``messages`` 里的文本，保证任何前端都能读到结果。
    """
    command = str(result.get("command") or "/slash")
    if not result.get("ok"):
        lines = [f"## {command} 执行失败", "", str(result.get("error") or "unknown error")]
        if result.get("hint"):
            lines += ["", f"> {result['hint']}"]
        return "\n".join(lines)

    commands = result.get("commands")
    if commands:
        lines = [
            f"## {command} — 可用命令（{len(commands)}）",
            "",
            "| 命令 | 分类 | 说明 | 用法 |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| `{item.get('slash', '')}` | {item.get('category', '')} "
            f"| {item.get('label', '')} | `{item.get('usage', '')}` |"
            for item in commands
        ]
        return "\n".join(lines)

    body = {k: v for k, v in result.items() if k not in {"command", "ok", "handler", "servers"}}
    return "\n".join(
        [
            f"## {command}",
            "",
            "```json",
            json.dumps(body, ensure_ascii=False, indent=2, default=str),
            "```",
        ]
    )


def _truncate(value: Any, limit: int = 220) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"
