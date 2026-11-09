---
name: qcc-super
description: 企查查超级智能体。自然语言→最少MCP调用→DuckDB大宽表→本体论风险推理→结构化报告。
---

# 企查查超级智能体 SKILL

## 触发条件

用户输入包含企业名 + 尽调/风险/合规/诉讼/授信/投资/采购等意图时触发。

## 数据层（DuckDB）

本 skill 内置 DuckDB 嵌入式列式数据库，作为 Agent 与本体论层（`scripts/kg_engine.py`，rdflib + pyshacl）之间的持久化数据层。

### 目录结构

```
skills/qcc-super/
├── SKILL.md                     # 本文件
├── mcp.json                     # 11 个 MCP Server 配置
├── field-index.json             # 字段→工具映射索引
├── requirements.txt             # duckdb + rdflib + pyshacl（semantica 可选）
├── data/
│   ├── qcc.db                   # DuckDB 数据文件（bootstrap 生成）
│   └── raw_<企业>/               # MCP 原始响应缓存（fetch 与 --insert 分离，避免重复消耗额度）
├── references/
│   ├── schema.sql               # 7 张大宽表 DDL（含 firm_relation 统一边表）
│   ├── tool_fields.json         # 169 工具字段清单
│   ├── slash_registry.json      # slash 命令注册表（22 条）
│   ├── ontology/turtle/qcc_ontology.ttl   # QCC 企业本体
│   ├── shacl/qcc_shapes.ttl               # SHACL 约束形状
│   └── examples/query_enterprise.sparql   # SPARQL 示例
└── scripts/
    ├── bootstrap.py             # 装 duckdb + 建表 + 灌工具目录
    ├── query.py                 # Agent 原子查询 CLI
    ├── reason.py                # 12 条推理规则测试版（回归/教学用）
    ├── slash.py                 # slash 命令统一入口（CLI + dispatch API）
    ├── kg_engine.py             # 本体论引擎：DuckDB → RDF → SHACL → 图推理 → 导出
    ├── ingest_real.py           # 工商/风险数据入库（firm_wide）
    ├── ingest_equity.py         # 股东/董监高入库（equity_edge + executive）
    ├── ingest_raw_backfill.py   # 从磁盘 raw_*/ 目录批量回补 firm_wide.raw_* 字段
    ├── migrate_firm_relation.py # 生成 firm_relation 统一边表（归并 5 种关系）
    └── handlers/                # core / meta / kg / official 四类处理器
```

> **解释器要求**：本项目依赖装在 `.venv` 里，系统 Python 缺 duckdb/rdflib。所有命令必须用
> `.venv\Scripts\python.exe`（Windows）或 `.venv/bin/python`（*nix）执行。

### 一键初始化

```bash
python skills/qcc-super/scripts/bootstrap.py
# 输出:
#   [bootstrap] Installing duckdb...
#   [bootstrap] Schema applied: skills/qcc-super/data/qcc.db
#   [bootstrap] Seeded 169 tools into tool_catalog
#   [bootstrap] Done.
```

优先 `uv add`，回退 `pip install`，最后提示手工 `uv add duckdb`。

### 数据模型（7 张表）

| 表 | 用途 | 主键（逻辑） | 备注 |
|---|---|---|---|
| `firm_wide` | 企业宽表 100+ 列 | `uscc` | 工商信息+财务+34类风险+知产+JSON兜底 |
| `event_wide` | 事件明细宽表 | `id` (显式赋值) | 每条事件一行，含 occurred_at/type/status |
| `equity_edge` | 股权关系边表 | 无（多对多） | holder_uscc → target_uscc + ratio |
| `executive` | 董监高任职表 | 无（可多任职） | person + role + appointed_at |
| `tool_catalog` | 169 MCP 工具目录 | `id` | bootstrap 自动灌入 |
| `mcp_call_log` | MCP 调用日志 | 无 | 用于审计和缓存决策 |
| `firm_relation` | **统一边表**（穿透专用） | 无 | 归并 equity/legal_rep/controller/ubo/executive，`direction` = firm_to_firm / person_to_firm |

`firm_relation` 由 `migrate_firm_relation.py` 从 `equity_edge` + `firm_wide` + `executive` 派生，每次 `ingest_*` 后重跑即可保持同步。`kg_engine._build_multi_relation_graph()` 优先走 `firm_relation`，缺失时回退到分散的多表查询。

> **DuckDB 约束提示**：不支持表级 `PRIMARY KEY`/`UNIQUE`/`NOT NULL` 之外的约束；`INTEGER` 不自增，需显式赋值；默认 autocommit，无需 `COMMIT`。去重和业务约束在 ingest 层做。

### 查询 CLI（Agent 使用）

`scripts/query.py` 提供 8 个原子查询子命令，**只做查询，不做推理**（推理交给尽调 Agent）：

```bash
# 企业画像（firm_wide 全字段一次取出）
python query.py profile --uscc 91xxxxxxxxxxxxxxxx

# 事件明细（可过滤类型）
python query.py events --uscc 91xxxx --type dishonest --limit 100

# 股权关系（穿透用）
python query.py equity --uscc 91xxxx

# 董监高
python query.py executives --uscc 91xxxx

# 按关键词找工具（路由决策用）
python query.py tool-schema --term 实控人 --top 5

# 按 server 列工具
python query.py tool-list --server qcc-risk

# 查任意表原始行
python query.py raw --uscc 91xxxx --table firm_wide

# 高级自定义 SQL
python query.py sql --sql "SELECT uscc, name FROM firm_wide WHERE risk_level='D'"
```

输出统一为 JSON 数组，可直接被 Agent 消费，或作为本体论层（`/kg-build`）的构图输入。

### 推理测试脚本（`reason.py`，非生产用）

`scripts/reason.py` 是 12 条推理规则的**参考实现**，仅作回归测试 / 教学演示。
生产推理由上层尽调 Agent（`agents/due_diligence/`）承担，两者规则编号一致，便于交叉验证。

```bash
# 用 DB 里真实数据推理
python reason.py --uscc 91xxxx --format markdown
python reason.py --uscc 91xxxx --format json

# 用内置 mock 数据自测（不依赖 DB）
python reason.py --demo
```

推理结果与 `agents/due_diligence/reasoner.py` 保持一致：

```
skill  hits: ['R1', 'R12']
agent  hits: ['R1', 'R12']
grade  skill=D  agent=D
一致: ✓ PASS
```

### 与本体论工具联动

本体论层由 `scripts/kg_engine.py` 实现，**不依赖 Neo4j，也不走 RML**：直接从 DuckDB 读行、
用 rdflib 构内存图，pyshacl 做约束校验。`semantica` 包仅作可用性探测（`semantica_available()`），
装与不装构图路径完全一致。

1. **查询出口**：Agent 通过 `query.py`、`/qcc-*` slash 命令或 DuckDB API 抽数据
2. **RDF 建模**：`kg_engine.fetch_enterprise_graph()` 读 `firm_wide`/`equity_edge`/`executive`/`event_wide`
   → `to_rdflib_graph()` 按 `references/ontology/turtle/qcc_ontology.ttl` 本体生成三元组
3. **约束校验**：`/kg-validate` 用 pyshacl 对 `references/shacl/qcc_shapes.ttl` 校验（`inference="rdfs"`）
4. **图查询**：`/kg-query` 执行 SPARQL；`/kg-infer` 跑轻量图推理（R1/R4/R7/R10）
5. **导出**：`/kg-export` 输出 ttl / jsonld / nt；`/kg-visualize` 生成 Cytoscape.js 单文件 HTML（合并股东企业 + owlrl 闭包）；`/kg-infer-visualize` 生成推理链图 HTML（Rule→Finding→Conclusion）；`/kg-semantica-probe` 深度探测 rdflib / pyshacl / owlrl / semantica 的安装状态与新旧架构；`/kg-traverse` 跨企业多关系 N 层 BFS 穿透（equity/legal_rep/controller/ubo/executive，含自然人 Person 节点）+ Louvain 社区发现 + degree centrality

### 数据流总览

```
用户自然语言 / slash 命令
    ↓
字段提取 (field-index.json)  或  slash_registry.json 直接路由
    ↓
MCP 批量调用 (mcp.json, 11 servers)  —— 消耗额度
    ↓
Step 1  落盘 raw_<企业>/*.json  (ingest_real.py --fetch)
    ↓
Step 2  解析 raw → equity_edge + executive  (ingest_equity.py --insert)
    ↓
Step 3  批量回补 firm_wide.raw_*  (ingest_raw_backfill.py)
    ↓
Step 4  重建 firm_relation 统一边表  (migrate_firm_relation.py)
    ↓
query.py 原子查询  /  /qcc-* slash 命令
    ↓
本体论层 (kg_engine.py)：/kg-build → /kg-validate → /kg-query → /kg-infer → /kg-export → /kg-visualize → /kg-infer-visualize → /kg-traverse → /kg-semantica-visualize
基础设施：/kg-semantica-probe 探测 rdflib / pyshacl / owlrl / semantica（含 semantica 0.6+ 新架构 12 个模块）
    ↓
上层尽调 Agent 推理（reason.py 为参考实现，不在本 skill 生产链路内）
    ↓
结构化报告
```

### MCP → DuckDB 4 步流水线（推荐入库顺序）

**原则**：MCP 响应一律先落 `data/raw_<企业>/<工具>.json`，再从磁盘解析入库。fetch 与 insert 分离，避免重复消耗 MCP 额度。

| Step | 脚本 | 用途 | 消耗 MCP | 幂等 |
|---|---|---|---|---|
| 1 | `ingest_real.py --fetch` | 调 MCP 拉取 registration / profile / risk_scan / shareholder / executive，写入 `data/raw_<企业>/` 目录 | ✅ 消耗 | 已存在则跳过 |
| 2 | `ingest_equity.py --insert` | 读 `raw_*/shareholder.json` → 写 `equity_edge` + `executive`；顺带回填 `firm_wide.raw_shareholder` / `raw_executive`；反查股东名 → `holder_uscc`；自然人股东生成 `holder_person_id` | ❌ 零消耗 | 按 uscc+holder 去重 |
| 3 | `ingest_raw_backfill.py` | 扫描全部 `raw_*/` 目录，把 registration / profile / risk_scan 等 JSON 回补到 `firm_wide.raw_registration` / `raw_profile` / `raw_risk_scan` 等 JSON 兜底字段 | ❌ 零消耗 | 幂等覆盖 |
| 4 | `migrate_firm_relation.py` | 从 `equity_edge` + `firm_wide` + `executive` 派生 `firm_relation` 统一边表（归并 equity / legal_rep / controller / ubo / executive 5 种关系），供 `/kg-traverse` 多关系穿透使用 | ❌ 零消耗 | 先 DELETE 对应 `source_table` 再 INSERT |

```bash
# 首次入库完整链路（<企业USCC> 换成真实值）
.venv\Scripts\python.exe skills/qcc-super/scripts/ingest_real.py     --uscc <USCC> --fetch
.venv\Scripts\python.exe skills/qcc-super/scripts/ingest_equity.py    --uscc <USCC> --insert
.venv\Scripts\python.exe skills/qcc-super/scripts/ingest_raw_backfill.py
.venv\Scripts\python.exe skills/qcc-super/scripts/migrate_firm_relation.py

# 后续补数据（MCP 有响应但没有入库）
# 只需跑 Step 2~4，Step 1 用 --fetch-only 决定是否重新消耗额度
```

`firm_relation` 由 `kg_engine._build_multi_relation_graph()` 优先读取，缺失时回退到分散的多表查询，向后兼容。

## 快速开始

```
自然语言输入 → 字段提取 → 查 field-index.json → 按 server 聚合 → 批量调用 MCP → 本体论推理 → 报告
```

示例：
```
输入: "查一下字节跳动的注册资本、股东结构、专利和失信记录"
→ 字段: [注册资本, 股东, 专利, 失信]
→ 映射: qcc-company(get_company_registration_info, get_shareholder_info)
        qcc-ipr(get_patent_info)
        qcc-risk(get_dishonest_people)
→ 3 个 server, 4 个工具调用 (最少覆盖)
→ 本体论推理: 股权穿透 + 失信传导 → 风险评分
→ 输出: 结构化风险报告
```

## MCP 配置

见 `mcp.json`，共 11 个 MCP Server，统一使用 `Authorization: Bearer ${QCC_MCP_TOKEN}` 认证。

| Server | 地址 | 工具数 | 认证要求 |
|---|---|---|---|
| qcc-company | `/mcp/company/stream` | 12 | 无需 |
| qcc-risk | `/mcp/risk/stream` | 32 | 无需 |
| qcc-history | `/mcp/history/stream` | 14 | **企业实名认证** |
| qcc-executive | `/mcp/executive/stream` | 18 | 无需 |
| qcc-ipr | `/mcp/ipr/stream` | 9 | 无需 |
| qcc-operation | `/mcp/operation/stream` | 9 | 无需 |
| qcc-legal-regulation | `/mcp/regulation/stream` | 1 | 无需 |
| qcc-legal-case | `/mcp/case/stream` | 1 | 无需 |
| qcc-tender | `/mcp/tender/stream` | 1 | 无需 |
| qcc-document | `/mcp/document/stream` | 1 | 无需 |
| qcc-document-mcp | `npx qcc-document-mcp` | 1 | 无需(Node.js 20+) |

## 路由规则 (Route + Reason)

### Step 1: 字段提取

从自然语言中提取字段关键词，参照 `field-index.json` 的 `fieldIndex` 节进行匹配。

关键词匹配优先级：
1. 精确匹配（"注册资本" → get_company_registration_info）
2. 同义词匹配（"营业执照" → get_company_registration_info）
3. 模糊匹配（"风险" → get_company_risk_scan）

### Step 2: 按 Server 聚合

将匹配到的工具按 server 分组，**同 server 的工具批量调用**，最小化 server 连接数。

```
聚合规则:
1. 如果字段集 ⊆ get_company_risk_scan 的 34 类风险 → 1 次调用覆盖全部
2. 如果字段集 ⊆ 某预设 skill → 直接用预设 skill 工具集
3. 否则按 server 分组，每组批量调用
4. 优先使用批量工具（如 get_company_risk_scan 替代逐个风险工具）
```

### Step 3: 效率评估

```
效率 = 覆盖字段数 / 调用工具数
最优: 效率 ≥ 5.0 (如 get_company_risk_scan: 34字段/1调用)
次优: 效率 3.0-5.0 (如 get_company_registration_info: 9字段/1调用)
一般: 效率 1.0-3.0 (单字段工具)
```

### Step 4: 预设 Skill 匹配

如果字段集恰好匹配某个预设 skill 的字段集，且 server 数更优，直接使用预设 skill。
全部 27 个预设 skill 按效率排序，优先使用高效率 skill：

#### 🏦 银行·合规风控（12 个）

| Slash 命令 | Server 数 | 效率 | 适用场景 |
|---|---|---|---|
| `/ubo-screening-qcc` | 1 | 5.0 | 仅查股权/UBO/KYC |
| `/credit-due-diligence` | 4 | 5.0 | 信贷尽调 |
| `/litigation-analysis-qcc` | 4 | 5.0 | 诉讼风险评估 |
| `/credit-monitoring` | 4 | 3.0 | 贷后风险预警 |
| `/executive-background-qcc` | 4 | 3.0 | 高管背景核查 |
| `/guarantor-check` | 4 | 3.0 | 担保方资信核查 |
| `/bankruptcy-monitor` | 4 | 3.0 | 破产风险监控 |
| `/equity-structure` | 3 | 3.0 | 股权穿透+UBO |
| `/counterparty-risk` | 5 | 2.0 | 交易对手风险 |
| `/trade-finance-compliance` | 5 | 2.0 | 跨境贸易合规 |
| `/business-health-scan` | 5 | 2.0 | 经营状态动态追踪 |
| `/kyb-verification-qcc` | 5 | 2.0 | 银行开户KYB核验 |

#### 📊 投资·尽调（6 个）

| Slash 命令 | Server 数 | 效率 | 适用场景 |
|---|---|---|---|
| `/ic-memo-qcc` | 6 | 5.0 | PE/VC投委会备忘录 |
| `/fundraising-tracker-qcc` | 2 | 2.0 | 融资历史溯源 |
| `/competitor-analysis` | 5 | 2.0 | 竞争对手分析 |
| `/strip-profile-qcc` | 6 | 2.0 | 投资前快速画像 |
| `/history-evolution-qcc` | 6 | 2.0 | 企业历史沿革 |
| `/ip-asset-inventory` | 6 | 2.0 | 知产资产盘点 |

#### ⚖️ 法律·法务（6 个）

| Slash 命令 | Server 数 | 效率 | 适用场景 |
|---|---|---|---|
| `/debt-recovery-assessment-qcc` | 4 | 3.0 | 债务追偿评估 |
| `/contract-review` | 3 | 1.0 | 合同风险批注 |
| `/new-supplier-screening` | 3 | 1.0 | 供应商快速筛查 |
| `/contract-party-check` | 6 | 2.0 | 合同主体核验 |
| `/labor-compliance` | 6 | 2.0 | 用工合规排查 |
| `/ip-infringement-alert` | 6 | 2.0 | 知产侵权预警 |

#### 🔗 采购·供应链（3 个）

| Slash 命令 | Server 数 | 效率 | 适用场景 |
|---|---|---|---|
| `/supplier-annual-check` | 5 | 2.0 | 供应商年度评审 |
| `/vendor-assess` | 5 | 2.0 | 供应商深度尽调 |
| `/new-supplier-screening` | 3 | 1.0 | 招投标候选筛查 |

> **注意**：`/new-supplier-screening` 在法务和供应链两个客群中均有引用，实际为同一个 skill。

## 本体论实体模型

### 核心实体

```
Enterprise（企业）
  - name, uscc, legal_rep, registered_capital, status, type
  - address, establishment_date, business_scope
  - shareholders[], executives[], investments[], branches[]
  - risk_signals[], financial_data{}, ipr_assets{}

Person（自然人）
  - name, id_hash
  - roles[]: 法代/董事长/实控人/高管/股东/受益所有人
  - risk_flags[]: 失信/限高/限出境/股权冻结/税收违法
  - controlled_companies[], affiliations[]

Relationship（关系）
  - 持股: party_a → party_b (ratio, direct/indirect)
  - 控制: controller → entity (authority_level)
  - 担保: guarantor → debtor (amount, type)
  - 关联交易: party_a ↔ party_b (type, frequency)
  - 任职: person → enterprise (role, period)
```

### 风险推理规则

#### 企业风险

```
规则1: 直接风险
  IF risk_signals CONTAINS 失信/被执行/限高/股权冻结/经营异常
  THEN risk_level = D (直接拒绝)
  ELSE IF risk_signals CONTAINS 行政处罚/税收违法/欠税公告
  THEN risk_level = C (条件准入)
  ELSE IF risk_signals CONTAINS 经营异常/限高
  THEN risk_level = B (谨慎准入)
  ELSE risk_level = A (准入)

规则2: 关联破产传导
  IF enterprise.real_controller = person P
  AND P.controls enterprise B
  AND B.status = 破产重整/清算
  THEN enterprise.关联破产风险 = HIGH
  risk_score += 0.3 × (P 对 enterprise 的控制权比例)

规则3: 壳公司识别
  IF historical_changes.legal_rep_count >= 3 (within 2y)
  AND historical_changes.address_count >= 2
  AND financial_data.insured_persons = 0
  THEN enterprise.壳公司嫌疑 = TRUE
  risk_level → D

规则4: 股东风险传导
  IF shareholder S has 失信/被执行
  AND S.持股比例 >= 20%
  THEN enterprise.股东风险传导 = TRUE
  risk_score += 0.2 × S.持股比例

规则5: 股权冻结影响
  IF equity_freeze EXISTS
  THEN enterprise.股权质押风险 = HIGH
  risk_score += 0.15 × 冻结股权占比
```

#### 法人/高管风险

```
规则6: 个人司法风险
  IF person.risk_flags CONTAINS 失信/限高/限出境/股权冻结
  THEN person.个人风险等级 = 一票否决

规则7: 系统性风险主体
  IF person.controls N companies
  AND M of N have 失信/被执行/限高
  THEN person.系统性风险 = TRUE
  risk_score = (M/N) × 100

规则8: 任职重叠风险
  IF person is executive at K enterprises
  AND any K enterprise has 失信/经营异常
  THEN person.关联风险传导 = TRUE

规则9: 控制力衰减
  IF person.持股比例 < 50% AND person.role != 法代
  THEN person.控制力 = 弱
  risk_score -= 0.1 (降低对个人风险的加权)
```

#### 跨企业网络风险

```
规则10: 关联网络密度
  IF enterprise has M related_companies
  AND K of M have risk signals
  THEN enterprise.网络风险 = (K/M) × 网络密度系数

规则11: 资金链断裂
  IF enterprise.guarantees enterprise B
  AND B.status = 破产/被执行
  THEN enterprise.担保传导风险 = HIGH

规则12: 实控人变更预警
  IF historical_changes.legal_rep_count >= 2 (within 1y)
  THEN enterprise.控制权稳定性 = 低
  risk_score += 0.15
```

## 报告输出格式

### 企业风险报告

```markdown
# {企业名称} 风险报告

## 一、企业概览
- 名称 / 统一社会信用代码 / 法定代表人 / 注册资本 / 成立日期 / 经营状态

## 二、风险评级
- 综合评级: A/B/C/D
- 直接风险: [失信/被执行/限高/股权冻结/经营异常]
- 关联风险: [股东传导/实控人网络/担保传导]
- 网络风险: [关联网络密度/系统性风险]

## 三、股权穿透
- 直接股东: [name, ratio]
- 实控人: [name, 控制力]
- 受益所有人(25%阈值): [name, ratio]

## 四、司法风险
- 裁判文书: N 条 (胜诉率 X%)
- 立案: N 条
- 被执行: N 条 (标的额 X 万元)
- 失信: N 条

## 五、高管风险
- 法代: [name] - 个人风险: [无/失信/限高/...]
- 实控人: [name] - 控制企业: N 家 (风险 M 家)

## 六、本体论推理结论
- 风险推理链: [规则N] → [结论]
- 建议: [准入/条件准入/谨慎准入/拒绝]
```

### 法人风险报告

```markdown
# {人名} 法人/高管风险报告

## 一、基本信息
- 姓名 / 关联企业 / 当前任职

## 二、个人风险信号
- 失信: N 条 / 限高: N 条 / 限出境: N 条 / 股权冻结: N 条

## 三、控制企业网络
- 控制企业: N 家 (风险 M 家)
- 系统性风险: [是/否]
- 关联风险传导: [是/否]

## 四、风险推理结论
- 综合评级: [一票否决/高风险/中风险/低风险]
- 建议: [拒绝/谨慎/准入]
```

## 使用示例

### 示例1: 快速企业画像

```
输入: "帮我看看字节跳动的注册信息、股东、专利和失信情况"
路由: qcc-company(2) + qcc-ipr(1) + qcc-risk(1) = 4 tools, 3 servers
推理: 股权穿透 → 股东风险传导 → 专利质押检查
输出: 企业风险报告
```

### 示例2: 高管背景核查

```
输入: "查一下张三的失信记录和关联企业的风险"
路由: qcc-executive(3) + qcc-risk(1) = 4 tools, 2 servers
推理: 高管个人风险 → 控制企业网络 → 系统性风险
输出: 法人风险报告
```

### 示例3: 信贷尽调

```
输入: "做一下某公司的信贷尽调，重点关注风险和财务状况"
路由: 匹配预设 credit-due-diligence skill → 4 servers, 20+ tools
推理: 全量风险扫描 + 财务分析 + 关联网络 + 历史对比
输出: 企业风险报告 (完整尽调版)
```

## 配置

```bash
# 环境变量
export QCC_MCP_TOKEN="your-api-key-here"

# 首次初始化（幂等，可重复执行）
python skills/qcc-super/scripts/bootstrap.py
```

## 注意事项

1. `qcc-history` 需企业实名认证后才能使用
2. `qcc-document-mcp` 需 Node.js 20+ 环境
3. 批量工具优先：`get_company_risk_scan` 可替代 32 个单独风险工具
4. 所有 server 统一使用 `Authorization: Bearer ${QCC_MCP_TOKEN}` 认证

---

## Slash 命令聚合器（方案 A）

本 skill 在 `scripts/slash.py` 提供统一 slash 命令入口，把 **22 条已注册命令**（8 core + 2 meta + 6 kg + 6 official）
聚合到一处，Agent 只需 `dispatch("/cmd", "args")` 一行调用。

> `references/slash_registry.json` 是唯一的命令真源；官方 27 个 skill 中目前落地注册 6 个高频命令，
> 其余走 `/qcc-tool-search` + MCP 直调，或按「Step 4 预设 Skill 匹配」表补注册。

### 目录结构

```
skills/qcc-super/
├── references/
│   └── slash_registry.json          # 命令注册表（22 条，唯一真源）
├── scripts/
│   ├── slash.py                     # CLI + dispatch() API
│   └── handlers/
│       ├── __init__.py              # 统一导出
│       ├── core.py                  # 8 个核心查询处理器
│       ├── meta.py                  # tool_search / raw_sql
│       ├── kg.py                    # 13 个 /kg-* 处理器
│       └── official.py              # 官方 skill 命令路由
└── requirements.txt                 # duckdb + rdflib + pyshacl
```

### 命令分组

| 类别 | 命令数 | 命令 |
|---|---|---|
| core | 8 | `/qcc-profile` `/qcc-risk-scan` `/qcc-ubo` `/qcc-equity` `/qcc-executive` `/qcc-events` `/qcc-financial` `/qcc-ipr` |
| meta | 2 | `/qcc-tool-search` `/qcc-sql` |
| kg | 14 | `/kg-install` `/kg-build` `/kg-query` `/kg-validate` `/kg-infer` `/kg-export` `/kg-visualize` `/kg-infer-visualize` `/kg-semantica-probe` `/kg-traverse` `/kg-semantica-ontology` `/kg-semantica-reason` `/kg-semantica-requirements` `/kg-semantica-visualize` |
| official | 6 | `/credit-due-diligence` `/kyb-verification-qcc` `/ubo-screening-qcc` `/ic-memo-qcc` `/contract-review` `/vendor-assess` |

### 用法

```bash
# CLI（必须用 .venv 里的解释器，系统 Python 缺 duckdb/rdflib）
.venv\Scripts\python.exe skills/qcc-super/scripts/slash.py "/qcc-help"
.venv\Scripts\python.exe skills/qcc-super/scripts/slash.py "/qcc-ubo 字节跳动 --threshold 0.25"
.venv\Scripts\python.exe skills/qcc-super/scripts/slash.py "/kg-build 演示风险科技有限公司 --depth 2"

# Agent 直接 import
from slash import dispatch
result = dispatch("/qcc-ubo", "字节跳动 --threshold 0.25")
```

### 企业不在 DuckDB 时的 MCP 路由

核心 handler 在企业未入库时返回 `mcp_route` 字段，Agent 据此调 MCP 采集并入库：

```json
{
  "found": false,
  "mcp_route": {
    "servers": ["qcc-company", "qcc-risk"],
    "tools": ["get_company_registration_info", "get_company_risk_scan"]
  }
}
```

---

## 本体论工具集成（/kg-* 命令）

本体论层由 `scripts/kg_engine.py`（抽取/构图/校验/推理/导出）+ `scripts/handlers/kg.py`（slash 适配）实现，技术栈是 **rdflib + pyshacl**：从 DuckDB 读行 → rdflib 内存图 → SHACL 校验 → SPARQL 查询 → 序列化导出。**不使用 Neo4j，也不走 RML 映射。**

`semantica` 是可选包：仅被 `kg_engine.semantica_available()` 探测（结果体现在 `/kg-build` 输出的 `semantica_available` 字段），装与不装构图路径完全一致。

### 依赖

- 必装（见 `requirements.txt`）：`rdflib>=7.0.0`、`pyshacl>=0.25.0`（`duckdb` 为基础依赖）
- 可选：`semantica>=0.6.0`（重量级，仅作可用性探测）

`/kg-install` 的实际行为：先 `uv add semantica rdflib pyshacl`（cwd=项目根，300s 超时）→ 失败回退 `<当前解释器> -m pip install ...`（`.venv` 内常无 pip，因此常一并失败）→ 均失败返回 `{ok:false, error, hint}`。已验证可用的手工路径：

```bash
uv pip install --python .venv\Scripts\python.exe rdflib pyshacl semantica
```

### 本体与约束文件

- `references/ontology/turtle/qcc_ontology.ttl` — QCC 企业本体（Enterprise / Person / Shareholder / Controller / Executive / RiskSignal 等 20+ 类 + 20+ 属性）
- `references/shacl/qcc_shapes.ttl` — SHACL 约束形状（USCC 18 位、`qcc:hasStakeRatio` 为 xsd:decimal ∈ [0,1]、业务状态枚举、风险等级 A/B/C/D 等）
- `references/examples/query_enterprise.sparql` — SPARQL 查询示例

> 建模约定（踩过的坑）：
> - 持股比例三元组挂在**股东/控制人节点**上（`ShareholderShape` 才是声明 `sh:datatype`/`sh:min`/`sh:max` 的形状），挂到企业节点会导致 SPARQL 出现笛卡尔积。
> - 数值字面量必须用 `Decimal` 构造：`Literal(float(x), datatype=XSD.decimal)` 是 ill-typed，pyshacl 判违规，且报告的 message 会被形状的 `sh:message` 覆盖成误导性的 “Stake ratio must be in [0,1]”——定位时看 `Source Shape` 与 `Constraint Violation in ...Component` 行。
> - URI 命名空间按 `holder_type` 选择：企业股东 → `entity/enterprise/`，自然人 → `entity/person/`；股东无 USCC 时用名称作局部标识。

### 9 个命令

| 命令 | 功能 | 依赖 |
|---|---|---|
| `/kg-install` | 安装 semantica + rdflib + pyshacl（uv → pip 回退） | — |
| `/kg-build <target> [--depth N]` | 从 DuckDB 抽取企业图并构建 RDF，返回 triples/unique_nodes 等计数 | rdflib |
| `/kg-query <target> [--query '...'\|--query-file p.sparql]` | SPARQL 查询内存图，返回 results/count/triples | rdflib |
| `/kg-validate <target>` | SHACL 约束校验，返回 conform/violations/report_text | rdflib + pyshacl |
| `/kg-infer <target>` | 纯 Python 图推理（R1/R4/R7/R10） | — |
| `/kg-export <target> [--format ttl\|jsonld\|nt]` | 序列化导出，返回 content/bytes | rdflib |
| `/kg-visualize <target> [--out path.html] [--use_owlrl true\|false]` | 生成 Cytoscape.js 单文件 HTML（合并股东企业 + owlrl 闭包），返回 out_path/stats | rdflib |
| `/kg-infer-visualize <target> [--out path.html] [--use_owlrl true\|false]` | 运行 `/kg-infer` 并生成推理链图 HTML（Rule→Finding→Conclusion + 侧栏 findings），返回 out_path/conform/severity_counts | rdflib |
| `/kg-semantica-probe` | 深度探测 rdflib / pyshacl / owlrl / semantica 安装状态、版本、模块可用性与新旧架构识别，含 sample_graph 端到端验证 | — |
| `/kg-traverse <target> [--layers N] [--relation R] [--min-ratio X]` | 多关系 BFS 穿透（equity / legal_rep / controller / ubo / executive）+ Louvain 社区发现 + 中心性 | semantica (optional) |
| `/kg-semantica-ontology <target?>` | 用 `semantica.ontology.OntologyEngine.from_data` + `quality_check` 解析并校验 `qcc_ontology.ttl`，输出覆盖率 + issues 清单 | semantica |
| `/kg-semantica-reason <target?> [--rules all\|IR1,IR2] [--use_owlrl true\|false]` | 用 `semantica.reasoning.SPARQLReasoner` + rdflib 执行 3 条内置 SPARQL 规则（IR1 控股 ≥50% / IR2 法代关联 / IR3 股东风险传导），可选 owlrl 闭世界闭包 | semantica |
| `/kg-semantica-requirements <target?> [--rules all\|IR1,IR2]` | **推理前置检查**：列出每条 SPARQL 规则需要的 predicate + 源表 + 字段 + FILTER，并实时到 DuckDB 查询实际就绪度；未就绪时给出对应的 `/ingest-*` 建议 | semantica |
| `/kg-semantica-visualize [--kind kg\|ontology] [--target 企业名] [--output html\|png] [--depth N] [--include_risk bool] [--include_executive bool] [--relations all\|top]` | 用 semantica 原生 OntologyVisualizer / KGVisualizer 导出自包含 HTML/PNG。kind=kg 融合 firm_wide 直连 + equity_edge 多跳穿透 + executive 高管 + firm_relation 其他关系 + event_wide 风险事件，返回 nodes/edges + stats（equity_edges/executive_edges/relation_edges/event_edges/hop_reached） | semantica |

补充说明：

- `<target>` 为企业名或 USCC。`/kg-query` `/kg-validate` `/kg-infer` `/kg-export` `/kg-visualize` `/kg-infer-visualize` 省略 target 时回落到 `firm_wide` 首行，也可用环境变量 `KG_TARGET` 指定。
- `/kg-export` 只支持 `ttl` / `jsonld` / `nt`（`export_graph` 的 `fmt_map`），未识别格式回退 turtle。
- `/kg-visualize` 默认输出到 `skills/qcc-super/output/kg-<uscc>.html`；`/kg-infer-visualize` 默认输出到 `skills/qcc-super/output/kg-infer-<uscc>.html`。两者均为纯 CDN（`cytoscape@3.28.1`），零本地依赖，浏览器打开即可交互。
- `/kg-semantica-visualize` 默认输出到 `skills/qcc-super/data/visualize/semantica_<kind>_<uscc>.html`（kind=ontology 时省略 uscc 后缀）。kind=kg 使用 semantica 原生 KGVisualizer 渲染融合图（企业/自然人/事件 三类节点），kind=ontology 使用 OntologyVisualizer 渲染 TTL 类继承树。
- `/kg-semantica-probe` 无参数，只做只读探测（不实例化任何对象、不启动服务）。**重要**：semantica 0.6+ 已完全重构为知识工程框架，旧的 `semantica.modeling.graph` / `semantica.semantics.mapping` 已移除；当前 qcc-super 只用 rdflib + owlrl + pyshacl，semantica 作为可选增强层预装。
- registry 里已声明但 handler **尚未实现**的参数：`/kg-validate --shape`、`/kg-infer --rules`，传入会被忽略。`/kg-infer --use_owlrl true|false`、`/kg-visualize --use_owlrl true|false`、`/kg-infer-visualize --use_owlrl true|false` 已实现，默认 `true`。
- CLI 会对含空格的参数分词，复杂 SPARQL 请用 `--query-file`，或由 Agent 直接 `from slash import dispatch` 传参。

### 用法示例（浙江胶友科技有限公司实测）

```bash
# 构建图
.venv\Scripts\python.exe skills/qcc-super/scripts/slash.py "/kg-build 浙江胶友科技有限公司"
# → {ok:true, found:true, uscc:"91330110MAEW31RB2Q",
#    counts:{triples:25, unique_nodes:8, shareholders:2, executives:2, events:0},
#    semantica_available:true, rdflib_available:true}

# SHACL 校验
.venv\Scripts\python.exe skills/qcc-super/scripts/slash.py "/kg-validate 浙江胶友科技有限公司"
# → {ok:true, conform:true, violations:[], report_text:"Validation Report\nConforms: True\n"}

# 图推理（默认启用 owlrl 闭世界闭包）
.venv\Scripts\python.exe skills/qcc-super/scripts/slash.py "/kg-infer 浙江胶友科技有限公司"
# → {ok:true, conform:true, count:4, findings:[
#      R1-cleared  风险扫描已执行且无一票否决事件，写入 qcc:NoVetoSignal,
#      R4-cleared  股东 浙江瑞航国际贸易有限公司 持股 68.0%，已入库且无风险标记,
#      R4-cleared  股东 厦门胶友科技有限公司 持股 32.0%，已入库且无风险标记,
#      R7-data-gap 实控人/UBO 未采集，R7 无法判定],
#  owlrl:{available:true, data_triples:27, new_triples:490, closed_triples:723}}

# SPARQL 查询
.venv\Scripts\python.exe skills/qcc-super/scripts/slash.py "/kg-query 浙江胶友科技有限公司 --query-file references/examples/query_enterprise.sparql"
# → {ok:true, count:1, triples:25, results:[{name, uscc, status:"存续", controller}]}

# 导出
.venv\Scripts\python.exe skills/qcc-super/scripts/slash.py "/kg-export 浙江胶友科技有限公司 --format ttl"
# → {ok:true, format:"ttl", bytes:1364, content:"@prefix qcc: <http://qcc.internal/ontology#> ..."}

# 可视化（合并股东企业 + owlrl 闭包，输出单文件 HTML）
.venv\Scripts\python.exe skills/qcc-super/scripts/slash.py "/kg-visualize 浙江胶友科技有限公司"
# → {ok:true, out_path:"skills/qcc-super/output/kg-91330110MAEW31RB2Q.html",
#    stats:{nodes:5, edges:11, enterprises:3, persons:2}, owlrl:{new_triples:494}}
# 浏览器打开该 HTML 即可交互查看（拖拽/缩放/hover）

# 推理链可视化（/kg-infer 结果的 Rule→Finding→Conclusion 图）
.venv\Scripts\python.exe skills/qcc-super/scripts/slash.py "/kg-infer-visualize 浙江胶友科技有限公司"
# → {ok:true, out_path:"skills/qcc-super/output/kg-infer-91330110MAEW31RB2Q.html",
#    conform:true, finding_count:4, rule_count:3,
#    severity_counts:{HIGH:0, MEDIUM:0, LOW:1, INFO:3},
#    graph_elements:{nodes:9, edges:9}, owlrl:{new_triples:490}}
```

### 事件类型 → RDF 子类映射

```
dishonest           → qcc:DishonestSignal
restricted_consumption → qcc:RestrictedConsumption
judgment_debtor     → qcc:JudgmentDebtor
equity_freeze       → qcc:EquityFreeze
business_exception  → qcc:BusinessException
penalty             → qcc:AdminPenalty
bankruptcy          → qcc:Bankruptcy
pre_litigation      → qcc:PreLitigation
(其他/未知)          → qcc:RiskSignal   # 兜底
```

### 推理规则（三态判定 + owlrl 闭世界闭包）

| 规则 id | 严重度 | 逻辑 | 对 conform 的影响 |
|---|---|---|---|
| `R1-veto` | HIGH | 命中 dishonest/judgment_debtor/restricted_consumption/equity_freeze | 命中即 `false` |
| `R1-cleared` | INFO | 已执行风险扫描且无 veto 类事件，写入 `qcc:NoVetoSignal` | `true` |
| `R4-transmit` | HIGH | 持股 ≥20% 股东存在失信/被执行等风险，跨企业传导 | 命中即 `false` |
| `R4-cleared` | INFO | 持股 ≥20% 股东已入库且无风险标记 | `true` |
| `R7-transmit` | HIGH | 实控人控制的其他企业存在系统性风险 | 命中即 `false` |
| `R7-data-gap` | LOW | 实控人/UBO 未采集，无法判定 | `true`（仅提示） |
| `R10-network-density` | HIGH | 企业风险事件 ≥5 条 | 命中即 `false` |

`/kg-infer` 返回 `conform = 所有 findings 的 conform 取与`。三态区分「未发现」与「已证明无」：`*-cleared` 是有数据支撑的闭世界判定；`*-data-gap` 是数据缺失的诚实标注。

**owlrl 闭世界闭包**：`--use_owlrl true`（默认）启用 `DeductiveClosure(OWLRL_Semantics).expand()`，对本体 TTL（`references/ontology/turtle/qcc_ontology.ttl`）中的 `subClassOf`、`inverseOf`、`symmetricProperty`、`disjointWith` 做推理。实测 27 → 723 三元组（+490）。缺失 rdflib/owlrl 时自动降级为纯规则推理。

**R4 从 data-gap 升级为 cleared 的两步**：① 把两家股东企业各自跑 registration + risk_scan 入库（`equity_edge.holder_uscc` 必须回填，否则无法 JOIN）；② owlrl 闭包根据 `subClassOf` 推出「无失信 ⇒ 非 DishonestSignal ⇒ 非 RiskSignal」的反向链。

`R12`（法代频繁变更）在 `kg_engine.infer_risk` 中仅留占位注释，尚未实现。

> 复杂推理交给上层尽调 Agent；本 skill 只负责数据抽取、RDF 建模、约束校验与轻量图推理。
