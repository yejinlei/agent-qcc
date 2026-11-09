# 双通道联网端到端验证记录

- **日期**：2026-09-15
- **目标**：验证 Agent 同时支持 skill 全部 slash 命令与自然语言输入，且默认使用新 `qcc-super` skill（169 工具 / 11 MCP server / DuckDB / Semantica）。
- **模式**：live（真实企查查 MCP，`https://agent.qcc.com/mcp/company/stream`，token 已配置）
- **样本企业**：浙江胶友科技有限公司（统一社会信用代码 91330110MAEW31RB2Q）

## 通道一：Slash 命令（走 skill 侧分发器，不进 LLM / 不走 4R+1）

命令：

```
agent-qcc chat "/qcc-profile 浙江胶友科技有限公司"
```

结果（耗时约 14s，exit 0）：

```
slash: /qcc-profile · ok
found: true
uscc: 91330110MAEW31RB2Q
legal_rep: 陈海斌
business_status: 存续
company_type: 其他有限责任公司
industry: 互联网信息服务
```

要点：`/xxx` 在 CLI 构建 graph 之前即短路（约 3s 启动，无需 langgraph 冷导入），离线/无凭据亦可运行。

## 通道二：自然语言（走完整 4R+1 + agent 侧多-server 连接器）

命令（`AGENT_QCC_USE_LLM=false`，以确定性验证规则规划器 `_args_for` 与多-server 路由）：

```
agent-qcc chat "查询：浙江胶友科技有限公司｜工商信息、股东结构"
```

流水线轨迹（耗时约 41s，exit 0）：

```
route:   multi · target=浙江胶友科技有限公司
plan:    get_company_registration_info, get_shareholder_info
evidence: 2/2 ok
reflect: respond gaps=0
```

真实返回数据：

- **工商信息**：法定代表人 陈海斌；登记状态 存续；成立日期 2025-08-27；注册资本 1000万元；实缴资本 500万元；组织机构代码 MAEW31RB-2；工商注册号 330110002470548。
- **股东结构**（2 条）：浙江瑞航国际贸易有限公司 68.00%（认缴 680 / 实缴 340）；厦门胶友科技有限公司 32.00%。

## 验证结论

| 改动点 | 验证方式 | 结果 |
| --- | --- | --- |
| `Pipeline._args_for`（按 `ToolSpec.requires` 首个必填参数生成入参，qcc-super → `searchKey`） | 规则规划器实调连接器 | `2/2 ok`；若入参仍为 `name` 则会因缺 `searchKey` 校验失败 |
| qcc.py 多 MCP server 路由（session 按 URL 隔离，`url_for` 按 `ToolSpec.server` 选端点） | 两个分属不同 server 的工具连续调用 | 均成功命中并返回真实数据 |
| CLI/Graph 双通道分流 | `/qcc-profile` 短路 vs 自然语言走 4R+1 | 分流正确，互不干扰 |

## 回归测试

- 离线全量：`pytest -m "not integration"` 退出码 0，全绿（含新增 `tests/test_slash_channel.py` 16 例）。
- 说明：`tests/test_import_sanity.py` 子进程守卫超时由 60s 调整为 180s——本机 `langchain_core` + `langgraph` 冷导入约 50s，属环境开销而非代码回归；这些守卫只验证「无 ModuleNotFoundError + 产出报告」，不测性能。

---

# 补充验证：Semantica 安装与本体论/推理实测

- **日期**：2026-09-16
- **样本企业**：浙江胶友科技有限公司（91330110MAEW31RB2Q，沿用 live 验证时入库的 DuckDB 数据）
- **环境**：全部命令用 `.venv\Scripts\python.exe` 执行（系统 Python 缺 duckdb/rdflib）

## Semantica 安装

- `/kg-install` 失败：内置 `uv add` 300s 超时，且 `.venv` 无 pip 模块。
- 改用 `uv pip install --python .venv\Scripts\python.exe semantica` 成功：装入 **semantica 0.6.8**（138 个包，含 torch 2.14.0 / transformers / spacy / faiss-cpu；跨盘全量复制耗时约 15 分钟）。
- 安装后 `/kg-build` 返回 `semantica_available: true`（此前为 false，走 rdflib 回退）。

## 推理测试（reason.py，12 条规则参考实现）

| 用例 | 命令 | 结果 |
| --- | --- | --- |
| 内置样例自测 | `reason.py --demo --format markdown` | 命中 R1（经营异常，grade D）+ R12（法代变更×2）→ 评级 **D / 风险分 65 / Reject** ✓ |
| 真实入库数据 | `reason.py --uscc 91330110MAEW31RB2Q` | 命中 R9 控制力衰减（delta=-0.1）→ 评级 **A / 风险分 40 / Accept** ✓ |

## 本体论测试（kg-* 命令，DuckDB → rdflib → pyshacl → RDF 链路）

| 命令 | 结果 |
| --- | --- |
| `/kg-build 浙江胶友科技有限公司 --depth 1` | ok，名称→USCC 解析命中；5 三元组 / 2 节点；`semantica_available: true` |
| `/kg-validate 浙江胶友科技有限公司` | SHACL **Conforms: True**，0 违规（shapes：`references/shacl/qcc_shapes.ttl`） |
| `/kg-infer 浙江胶友科技有限公司` | 4 条图谱规则（R1/R4/R7/R10），findings=0——与 reason.py 的 A 评级交叉一致 ✓ |
| `/kg-export 浙江胶友科技有限公司 --format ttl` | 合法 Turtle：`qcc:Enterprise` + hasName / hasUSCC / hasLegalRep(陈海斌) / hasBusinessStatus(存续)，258 字节 |

## SPARQL 用例（/kg-query --query-file）

| # | 用例 | 查询要点 | 结果 |
| --- | --- | --- | --- |
| 0 | 官方示例 `references/examples/query_enterprise.sparql` | SELECT + OPTIONAL | 1 条：name/uscc/status=存续/controller=None ✓ |
| 1 | 法代 FILTER | `FILTER (?name = "浙江胶友科技有限公司")` + `qcc:hasLegalRep` | 1 条：legalRep=陈海斌 ✓ |
| 2 | 聚合统计 | `COUNT(DISTINCT ?e)` / `COUNT(?s)` | enterprises=1，triples=5 ✓ |
| 3 | 股东空结果负例 | `qcc:hasShareholder` + OPTIONAL `qcc:hasStakeRatio` | `results: []`, count=0（库中尚无股东子图，优雅返回空集）✓ |

注：CLI 层对含空格的 `--query` 内联参数会分词，复杂 SPARQL 一律走 `--query-file`。

## 观察与遗留

1. **图规模仅 5 三元组**：kg_engine 构图走 rdflib，semantica 包当前只体现为 `semantica_available` 升级；股东/高管子图为 0 是因 DuckDB 只有 profile/registration/risk_scan 三表数据。联网跑 `/qcc-shareholders` 等命令入库后 `/kg-build --depth 2` 可重建带股东（浙江瑞航国际贸易 68% / 厦门胶友科技 32%）、高管的完整图。
2. **谓词对照**（`kg_engine.to_rdflib_graph`）：股东 `qcc:hasShareholder` + `qcc:isShareholderOf`，持股比例 `qcc:hasStakeRatio`（挂企业节点），高管 `qcc:hasExecutive` + `qcc:hasRole`，实控人 `qcc:hasController` + `qcc:controls`。
