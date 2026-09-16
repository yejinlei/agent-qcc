# agent-qcc

基于 **LangGraph** 的企业尽调 Agent，用 **企查查 Connector 型 skill** 作为唯一权威数据源。

编排采用 **4R+1**：`Route → Reason → Retrieve → Reflect → Respond`。

- **R1 Route** 意图识别 + 企业名抽取；纯闲聊直接短路到 Respond。
- **R2 Reason** 按 skill 里的工具清单规划最少调用，不发明工具。
- **R3 Retrieve** 调用企查查连接器，结果原样落进 `evidence`。
- **R4 Reflect** 覆盖率反思，缺口未补时回环 `retrieve`（受 `max_reflect_retries` 限制）。
- **+1 Respond** 只依据 `evidence` 作答，禁止用模型记忆补全。

## 快速开始

```powershell
cd F:\src\agent-qcc
uv venv --python 3.11 .venv
uv pip install -e ".[dev]"
copy .env.example .env

# 单轮问答（默认离线模式，无需任何 Key）
agent-qcc chat "查一下企查查科技股份有限公司的注册资本和股东结构" -v

# 交互式 TUI
agent-qcc tui

# 查看已安装的 connector skill
agent-qcc skills
```

离线模式（默认 `AGENT_QCC_OFFLINE=true`）只演示编排流程、不真正打企查查；配上 Key 后把
`AGENT_QCC_OFFLINE` 置为 `false` 即走实时链路。

## 两种运行方式

**CLI / TUI**

```powershell
agent-qcc chat "帮我做一下字节的全面背调" --thread lead-001
agent-qcc tui --thread lead-001
```

`--thread` 对应 LangGraph 的 `thread_id`，同一线程可续接历史。

**langgraph serve（REST + Playground）**

```powershell
agent-qcc-serve --host 127.0.0.1 --port 2024
# 等价于：langgraph serve src/agent_qcc/agent.py:graph
```

然后：

```powershell
curl http://127.0.0.1:2024/ok
```

`src/agent_qcc/agent.py:graph` 就是 `langgraph serve` 加载的图定义；
`build_agent()` 是同一份代码的编程入口（CLI/TUI 走它）。

## 架构

`docs/architecture.html` — archify 生成的可交互架构图（4R+1），浏览器直接打开。

## 目录

```
src/agent_qcc/
  config.py     配置（.env / AGENT_QCC_*）
  skill.py      解析 skills/<name>/SKILL.md + mcp.json
  qcc.py        企查查 connector 客户端
  prompts.py    4R+1 各阶段提示词
  pipeline.py   4R+1 五个节点
  graph.py      StateGraph 拓扑（含 reflect 回环）
  agent.py      build_agent() + langgraph serve 图入口
  store.py      checkpointer 工厂
  cli.py        CLI / TUI / serve
skills/
  qcc-company/  企查查 connector skill（SKILL.md + mcp.json）
```

## 接入真实连接器

`skills/qcc-company/mcp.json` 当前是

```json
{ "mcpServers": { "qcc-company": { "type": "streamableHttp",
    "url": "https://agent.qcc.com/mcp/company/stream" } } }
```

替换 `url` 为你有权访问的端点，并把 `AGENT_QCC_OFFLINE=false` 即可。
认证方式（header/token）按你的平台补充到 `qcc.py:QccClient._remote` 的 `headers`。

## 测试

```powershell
agent-qcc skills        # 15 个工具应全部解析出来
pytest                  # 离线编排全链路
```
