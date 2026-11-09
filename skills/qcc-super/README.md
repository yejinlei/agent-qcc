# qcc-super 使用教程

面向使用者的上手教程。规范、数据模型、命令真源见 [SKILL.md](./SKILL.md)。

一句话架构：**自然语言 / slash 命令 → 最少 MCP 调用 → DuckDB 大宽表 → 本体论层（rdflib + pyshacl）→ 结构化结论**。
skill 只负责数据、查询、约束校验与轻量图推理；重推理与结论由上层尽调 Agent 承担。

---

## 0. 前置条件

依赖装在项目根的 `.venv` 里，**系统 Python 缺 duckdb/rdflib**，所有命令必须用 venv 解释器：

```powershell
# Windows
.venv\Scripts\python.exe --version
# *nix
.venv/bin/python --version
```

首次使用先初始化（建 6 张宽表 + 灌 169 个 MCP 工具目录）：

```powershell
.venv\Scripts\python.exe skills/qcc-super/scripts/bootstrap.py
```

下文示例统一在**项目根目录**（`f:\src\agent-qcc`）执行，并省略解释器前缀写作 `PY`：

```powershell
$PY = ".venv\Scripts\python.exe"
$SLASH = "skills/qcc-super/scripts/slash.py"
```

---

## 1. 30 秒上手

```powershell
# 看有哪些命令（22 条：core 8 + meta 2 + kg 6 + official 6）
& $PY $SLASH "/qcc-help"

# 企业画像
& $PY $SLASH "/qcc-profile 浙江胶友科技有限公司"

# 本体论：构图 + 校验 + 推理
& $PY $SLASH "/kg-build 浙江胶友科技有限公司"
& $PY $SLASH "/kg-validate 浙江胶友科技有限公司"
& $PY $SLASH "/kg-infer 浙江胶友科技有限公司"
```

---

## 2. 两种入口通道

| 通道 | 形态 | 适用 |
|---|---|---|
| **自然语言** | 「查一下 X 的股东和风险」 | 人机对话；由 Agent 拆解成 MCP 调用 + 查询 |
| **slash 命令** | `/qcc-ubo X --threshold 0.25` | 确定性、可脚本化、可回归测试 |

两者共用同一套 handler，Agent 也可直接 import：

```python
import sys; sys.path.insert(0, "skills/qcc-super/scripts")
from slash import dispatch
result = dispatch("/kg-infer", "浙江胶友科技有限公司")
```

命令分组（真源 `references/slash_registry.json`）：

| 类别 | 数量 | 命令 |
|---|---|---|
| core | 8 | `/qcc-profile` `/qcc-risk-scan` `/qcc-ubo` `/qcc-equity` `/qcc-executive` `/qcc-events` `/qcc-financial` `/qcc-ipr` |
| meta | 2 | `/qcc-tool-search` `/qcc-sql` |
| kg | 10 | `/kg-install` `/kg-build` `/kg-query` `/kg-validate` `/kg-infer` `/kg-export` `/kg-visualize` `/kg-infer-visualize` `/kg-semantica-probe` `/kg-traverse` |
| official | 6 | `/credit-due-diligence` `/kyb-verification-qcc` `/ubo-screening-qcc` `/ic-memo-qcc` `/contract-review` `/vendor-assess`（路由声明，返回所需 MCP 工具 + 官方文档链接） |

---

## 3. 数据入库：两步分离，省额度

联网抓取与离线入库分离，复验阶段可反复跑第 2 步而**不消耗企查查额度**：

```powershell
# 第 1 步：联网 fetch，原始响应落盘 data/raw_<企业>/*.json（消耗额度）
& $PY skills/qcc-super/scripts/ingest_real.py
& $PY skills/qcc-super/scripts/ingest_equity.py

# 第 2 步：离线解析 raw JSON 写入 DuckDB（零额度，可重复执行）
& $PY skills/qcc-super/scripts/ingest_equity.py --insert

# 第 3 步：从磁盘 raw_*/ 目录批量回补 firm_wide.raw_* 字段（零额度）
& $PY skills/qcc-super/scripts/ingest_raw_backfill.py

# 第 4 步：重建 firm_relation 统一边表（归并 5 种关系）
& $PY skills/qcc-super/scripts/migrate_firm_relation.py
```

> 当前 `TARGET_NAME` 硬编码在 `scripts/ingest_real.py`（现为「浙江胶友科技有限公司」），换企业需改该常量并新建 `data/raw_<企业>/` 目录。`ingest_raw_backfill.py` 会自动扫描所有 `raw_*/` 目录，无需逐家配置。

### 需要多少数据？——不需要全量，也不需要调所有 MCP 工具

本体论层**零 MCP 调用**：`kg_engine.fetch_enterprise_graph()` 只读 DuckDB 五张表（`firm_wide` / `equity_edge` 双向 / `executive` / `event_wide` / `firm_relation`）。图的丰富度 = 入库覆盖度；缺字段不阻塞构图，只在 `/kg-validate` 里以 violation 暴露。

| 层级 | MCP 工具 | 落表 | 解锁能力 |
|---|---|---|---|
| **L0 最小可构图** | `get_company_registration_info` | firm_wide | 满足 `EnterpriseShape` 必填（hasUSCC/hasName/hasLegalRep） |
| **L1 基线** | + `get_company_profile`、`get_company_risk_scan` | firm_wide、event_wide | R1 一票否决、R10 网络密度可判 |
| **L2 股权治理** | + `get_shareholder_info`、`get_key_personnel` | equity_edge、executive | R4/R7/R9；`ShareholderShape`（比例 ∈[0,1]）、`ExecutiveShape`（role 枚举）生效 |
| **L3 穿透增量** | 股东企业各自 registration + risk_scan、UBO、对外投资、司法/知产/财务 | 多企业多行 | 跨企业网络推理定论、二级穿透 |

SHACL 必填项（缺则报 violation）：Enterprise→`hasUSCC`/`hasName`/`hasLegalRep`；Shareholder→`hasName`；Executive→`hasName`/`hasRole`；RiskSignal→`hasOccurredAt`。

---

## 4. 实战：用本体论工具尽调「浙江胶友科技有限公司」

该企业已在库（uscc `91330110MAEW31RB2Q`），全流程离线。

### Step 1 构图 `/kg-build`

```powershell
& $PY $SLASH "/kg-build 浙江胶友科技有限公司"
```

```json
{"ok": true, "found": true, "uscc": "91330110MAEW31RB2Q",
 "counts": {"triples": 25, "unique_nodes": 8, "shareholders": 2, "executives": 2, "events": 0},
 "semantica_available": true, "rdflib_available": true}
```

### Step 2 约束校验 `/kg-validate`

```powershell
& $PY $SLASH "/kg-validate 浙江胶友科技有限公司"
```

```json
{"ok": true, "conform": true, "violations": [],
 "report_text": "Validation Report\nConforms: True\n"}
```

`conform: true` 只说明**数据形状合规**（USCC 18 位、比例 ∈[0,1]、role 命中枚举），不代表「无风险」。

### Step 3 图推理 `/kg-infer`

```powershell
& $PY $SLASH "/kg-infer 浙江胶友科技有限公司"
```

```json
{"ok": true, "conform": true, "count": 4, "findings": [
  {"rule": "R1-cleared",   "severity": "INFO",
   "detail": "风险扫描已执行且无一票否决事件，写入 qcc:NoVetoSignal（闭世界已证明无）", "conform": true},
  {"rule": "R4-cleared",   "severity": "INFO",
   "detail": "股东 浙江瑞航国际贸易有限公司 持股 68.0%，已入库且无风险标记", "conform": true},
  {"rule": "R4-cleared",   "severity": "INFO",
   "detail": "股东 厦门胶友科技有限公司 持股 32.0%，已入库且无风险标记", "conform": true},
  {"rule": "R7-data-gap",  "severity": "LOW",
   "detail": "实控人/UBO 未采集，R7 无法判定（需 get_actual_controller / get_beneficial_owners）", "conform": true}],
 "owlrl": {"available": true, "data_triples": 27, "new_triples": 490, "closed_triples": 723}}
```

规则语义（三态判定）：

| 规则 id | 严重度 | 逻辑 | 对 conform 的影响 |
|---|---|---|---|
| `R1-veto` | HIGH | 命中 dishonest/judgment_debtor/restricted_consumption/equity_freeze | 命中即 `false` |
| `R1-cleared` | INFO | 已执行风险扫描且无 veto 类事件，写入 `qcc:NoVetoSignal` | `true` |
| `R4-transmit` | HIGH | 持股 ≥20% 股东存在失信/被执行等风险，跨企业传导 | 命中即 `false` |
| `R4-cleared` | INFO | 持股 ≥20% 股东已入库且无风险标记 | `true` |
| `R7-transmit` | HIGH | 实控人控制的其他企业存在系统性风险 | 命中即 `false` |
| `R7-data-gap` | LOW | 实控人/UBO 未采集，无法判定 | `true`（仅提示） |
| `R10-network-density` | HIGH | 企业风险事件 ≥5 条 | 命中即 `false` |

三态区分「未发现」与「已证明无」：`*-cleared` 是有数据支撑的闭世界判定，写入 `qcc:NoVetoSignal` 类；`*-data-gap` 是数据缺失的诚实标注。

owlrl 闭世界闭包：`--use_owlrl true`（默认）启用 `DeductiveClosure(OWLRL_Semantics).expand()`，对本体 TTL（含 `subClassOf`、`inverseOf`、`symmetricProperty`、`disjointWith`）推理出对称/逆属性、子类传播、互斥断言。实测 27 → 723 三元组（+490）。缺失 rdflib/owlrl 时自动降级为纯规则推理。`R12`（法代频繁变更）尚未实现。

### Step 4 自定义 SPARQL `/kg-query`

CLI 会对含空格的参数分词，复杂查询写文件：

```powershell
& $PY $SLASH "/kg-query 浙江胶友科技有限公司 --query-file references/examples/query_enterprise.sparql"
```

```json
{"ok": true, "count": 1, "triples": 25,
 "results": [{"name": "浙江胶友科技有限公司", "uscc": "91330110MAEW31RB2Q",
              "status": "存续", "controller": "None"}]}
```

自写查询示例（股东 + 持股比例）：

```sparql
PREFIX qcc: <http://qcc.internal/ontology#>
SELECT ?holder ?ratio WHERE {
  ?e a qcc:Enterprise ; qcc:hasShareholder ?h .
  ?h qcc:hasName ?holder .
  OPTIONAL { ?h qcc:hasStakeRatio ?ratio }
}
```

> 注意：`qcc:hasStakeRatio` 挂在**股东节点**上，不是企业节点。写错会产生笛卡尔积。

### Step 5 导出 `/kg-export`

```powershell
& $PY $SLASH "/kg-export 浙江胶友科技有限公司 --format ttl"   # 也支持 jsonld / nt
```

返回 `content` + `bytes`（实测 1364 bytes），可直接落盘交给外部 SPARQL 端点或图工具消费。

### Step 5b 可视化 `/kg-visualize`

```powershell
& $PY $SLASH "/kg-visualize 浙江胶友科技有限公司"
# → skills/qcc-super/output/kg-91330110MAEW31RB2Q.html
```

生成单文件 Cytoscape.js HTML（零本地依赖，仅 CDN 引 `cytoscape@3.28.1`），浏览器打开可拖拽/缩放/hover。特点：

- 自动合并「中心企业 + 全部股东企业」的图，owlrl 闭世界闭包，把逆属性 (`isShareholderOf` / `worksAt` / `controls`) 一起画出
- 中心企业加白边加粗高亮；节点分 `enterprise`（矩形）/ `person`（椭圆）两类
- 8 种关系带颜色语义：股东/高管/法人/实控人/控制/任职/担保 —— 见页脚图例
- 交叉任职的自然人（同一人出现在多家企业）会以单点被多条边汇聚到，视觉上一眼可辨

实测 `91330110MAEW31RB2Q`：3 家企业 + 2 名自然人 = 5 节点 11 边，owlrl 闭包 +494 三元组。

### Step 5c 推理链可视化 `/kg-infer-visualize`

```powershell
& $PY $SLASH "/kg-infer-visualize 浙江胶友科技有限公司"
# → skills/qcc-super/output/kg-infer-91330110MAEW31RB2Q.html
```

生成单文件 Cytoscape.js HTML，把 `/kg-infer` 结果画成推理链图：

```
[Rule 规则节点]  ── 应用 ──▶  [Finding 命中/未命中]  ── 违反/满足 ──▶  [Conclusion 结论]
                                                                    ▲
                                                                    │ 基于
                                                            [owlrl 元数据]
```

- **节点**：Rule（矩形紫）/ Finding（菱形，按 severity 染色 HIGH 红 / MEDIUM 橙 / LOW 绿 / INFO 灰）/ Conclusion（六边形绿/红）/ owlrl（平行四边形）
- **边**：`applies`（灰）/ `violates`（红粗）/ `satisfies`（绿）/ `based_on`（灰虚线）
- **侧栏**：按严重度倒序列出所有 findings 的完整详情，与图上节点一一对应
- 顶部状态条直接显示 `符合 / 不符合` 徽章（红/绿）

实测 `91330110MAEW31RB2Q`：R1/R4/R7 三条规则 + 4 条 finding（3 INFO + 1 LOW）→ 结论「全部规则符合」，owlrl 闭包 +490 三元组。

### Step 5d 依赖探测 `/kg-semantica-probe`

```powershell
& $PY $SLASH "/kg-semantica-probe"
```

深度探测本体论栈依赖，输出 4 部分：

1. **semantica**：版本 + 新旧架构识别 + 12 个核心模块逐一探测（`kg` / `ontology` / `reasoning` / `ingest` / `parse` / `triplet_store` / `graph_store` / `visualization` / `context` / `conflicts` / `deduplication` / `semantic_extract`）+ 旧 API 兼容检查（`semantica.modeling.graph` / `semantica.semantics.mapping`）
2. **rdflib / pyshacl / owlrl**：版本 + 可用性
3. **sample_graph**：最小企业图端到端跑 `to_rdflib_graph`，验证本体映射链路（含 firm / shareholders / executives / events 全字段）
4. **python**：运行环境与解释器路径

**关键洞察**：semantica 0.6+ 已从传统 RDF 建模库演进为完整知识工程框架，**旧 API 全部移除**。当前 qcc-super 只用 rdflib + owlrl + pyshacl，semantica 作为可选增强层预装。后续要接 semantica 时，用它的新 API（`semantica.kg` / `semantica.reasoning` / `semantica.ontology`），不要按旧文档照抄。

实测环境：
```json
"semantica": "0.6.8 · 新架构 (0.6+) · 核心模块 12/12 可用"
"rdflib": "7.6.0", "pyshacl": "0.40.1", "owlrl": "7.6.2"
"sample_graph": {"triples": 14, "ok": true}
```

### Step 5e 跨企业穿透 + 社区发现 `/kg-traverse`

```powershell
# 多关系穿透（默认 --relation all：股权+法代+实控人+UBO+高管）
& $PY $SLASH "/kg-traverse 91330110MAEW31RB2Q --layers 3 --relation all"
# 只走股权边（旧行为）
& $PY $SLASH "/kg-traverse 浙江胶友科技有限公司 --layers 3 --relation equity"
# 只看股权 + 法定代表人
& $PY $SLASH "/kg-traverse 浙江胶友科技有限公司 --relation equity,legal_rep"
# 只看高管关系
& $PY $SLASH "/kg-traverse 91330110MAEW31RB2Q --relation executive"
# 组合 + 持股比例过滤
& $PY $SLASH "/kg-traverse 浙江胶友科技有限公司 --relation all --min-ratio 0.2"
```

以中心企业为起点做 N 层 BFS 穿透（默认 3，范围 1-6）。**默认走全部 5 种关系**：`equity` + `legal_rep` + `controller` + `ubo` + `executive`，也可用 `--relation R` 指定单个或多个（逗号分隔）。

**数据源**：`kg_engine._build_multi_relation_graph()` 优先走 **`firm_relation` 统一边表**（由 `migrate_firm_relation.py` 从 `equity_edge` + `firm_wide` + `executive` 派生）。若 `firm_relation` 表不存在，自动回退到分散的三表查询（兼容旧库）。

**关系方向与边类型**：
- `equity` → 边类型 `hasShareholder`，方向 `firm_to_firm`（holder → target 企业对企业，holder 侧继续向上传）
- `legal_rep` → 边类型 `hasLegalRep`，方向 `person_to_firm`（自然人叶子节点）
- `controller` → 边类型 `hasController`，方向 `person_to_firm`
- `ubo` → 边类型 `hasUBO`，方向 `person_to_firm`
- `executive` → 边类型 `hasExecutive`，方向 `person_to_firm`，含 `role`（法代/董事长/总经理/董事/监事/高管/实控人）

**节点类型**：`Enterprise`（USCC 为 id）+ `Person`（`person:<姓名>` 命名空间，与 USCC 不冲突）。**层内去重**：同一个自然人在同层既作法代又作高管，只保留一条边。

**返回**：
- **relation_types_used**：实际启用的关系类型
- **layers**：每层节点（含 `type` / `id` / `name` / `relation` / `ratio` 或 `stake` 或 `role`）
- **edges**：全部穿透边（含 `type` / `weight` / `label`）
- **stats**：`total_nodes` / `total_enterprises` / `total_persons` / `total_edges` / 每层分布
- **top_hubs**：degree centrality Top 5（含 Person + Enterprise，可暴露自然人的多公司关联）
- **communities_summary**：Louvain 社区列表，含 `type` 与 `contains_center` 标记
- **analysis**：`modularity` / `num_communities` / `node_community` / `connectivity`

**用途**：UBO 反制、关联方识别、穿透可视化、集团结构识别、"一个人控多少家公司"的自然人穿透。

实测 `91330110MAEW31RB2Q --relation all --layers 3`：1 层穿透 + 5 节点（3 企业 + 2 人）+ 4 条边（层内去重生效：陈海斌对中心企业既是法代又是高管，只保留 1 条）+ 1 个 Louvain 社区（浙江胶友 + 两家股东 + 两位自然人联通）。**陈海斌**作为 Person 节点，在 firm_relation 中共有 3 条边（浙江胶友 hasLegalRep+hasExecutive + 浙江瑞航 hasLegalRep）—— 一次穿透暴露他实控 2 家企业。**王爱军**在 firm_relation 中共有 2 条边（浙江胶友 hasExecutive/监事 + 厦门胶友 hasLegalRep）—— 这正是旧版只走 `equity_edge` 时会漏掉的关键信息。

### Step 6 交叉验证：12 条规则参考实现

```powershell
& $PY skills/qcc-super/scripts/reason.py --uscc 91330110MAEW31RB2Q --format json
```

```json
{"final_grade": "A", "veto": false, "risk_score": 40.0,
 "controller": null, "controller_stake": 0.0,
 "hits": [{"rule_id": "R9", "name": "控制力衰减", "detail": {"stake": 0.0, "is_legal_rep": false}}],
 "recommendation": "准入 (Accept)"}
```

---

## 5. 结果判读：缺口 ≠ 无风险

上例中必须区分「已确认的结论」与「数据缺口」：

| 现象 | 真实含义 | 补齐动作 |
|---|---|---|
| `events: 0`，R1 触发 `R1-cleared` | **已确认无风险事件**：`raw_risk_scan` 摘要为「已全量扫描 35 项风险因子：0 项有记录、35 项无记录」，owlrl 写入 `qcc:NoVetoSignal` | 无需补采；缓存过期后可重扫 |
| R7 触发 `R7-data-gap`，`controller: null` | **缺口**：实控人字段未采集（`controller_name IS NULL`） | UBO / 实控人穿透工具 |
| R9 命中 `stake = 0.0` | 与上同源；因此 `A / 40 / 准入` 中的 R9 扣分是假信号 | 同上 |
| R4 触发两条 `R4-cleared` | **已确证无传导**：两家股东（91330201MABPBTAF58 / 91350200MA3569Y783）均已入库并各自跑过风险扫描 | 无需补采；定期重扫 |
| `/qcc-profile` 大量结构化列为 `null`（如 `reg_capital`、`established_at`） | ingest 只回填了部分列，原值仍在 `raw_registration` / `raw_profile` / `raw_risk_scan` 里（注册资本 1000 万、成立 2025-08-27、参保 9 人） | 取数时优先解析 `raw_*` JSON；或补 ingest 字段映射 |

R4 从 `data-gap` 升级为 `cleared` 需要两步：① 把两家股东企业各自跑 registration + risk_scan 入库（`equity_edge.holder_uscc` 必须回填，否则无法 JOIN）；② owlrl 闭包根据本体 `subClassOf` 推出「无失信 ⇒ 非 DishonestSignal ⇒ 非 RiskSignal」的反向链。68% 大股东一旦命中失信，`R1-veto` 直接把结论翻为一票否决。

---

## 6. 常见坑（FAQ）

| 症状 | 原因 | 解法 |
|---|---|---|
| `ModuleNotFoundError: duckdb / rdflib` | 用了系统 Python | 换成 `.venv\Scripts\python.exe` |
| `/kg-install` 失败 | `uv add` 300s 超时；`.venv` 内无 pip，回退也失败 | 手工 `uv pip install --python .venv\Scripts\python.exe rdflib pyshacl semantica` |
| 校验报 `Stake ratio must be in [0,1]` 但比例明明合法 | `Literal(float(x), datatype=XSD.decimal)` 是 ill-typed，message 被 shape 的 `sh:message` 覆盖 | 用 `Decimal` 构造；定位时看报告里的 `Source Shape` 与 `Constraint Violation in ...Component` 行 |
| SPARQL 出现重复行 / 笛卡尔积 | 三元组挂错节点（比例挂到了企业节点） | 比例挂股东/控制人节点 |
| 企业股东出现在 `entity/person/` | 按「有无 USCC」判命名空间 | 按 `holder_type` 判：企业 → `entity/enterprise/`，自然人 → `entity/person/`；无 USCC 时用名称作局部标识 |
| `--depth 2` 没多查出东西 | depth>1 分支的 SQL 与 depth=1 的对外投资查询相同（当前为空转） | 真二级穿透需先把股东企业入库（现 `holder_uscc` 为 NULL，无法 JOIN） |
| `--shape` / `--rules` 传了没反应 | registry 已声明但 handler 未实现 | 忽略即可，暂无替代参数 |
| SPARQL 内联查询报语法错 | CLI 对含空格参数分词 | 用 `--query-file`，或 Agent 走 `dispatch()` |
| `/qcc-sql` 报 `SELECT clause without selection list` / `only SELECT statements allowed` | SQL 未整体加引号，被分词成多个位置参数（只取到 `SELECT`） | 整条 SQL 用双引号包住：`/qcc-sql "SELECT ..."`；PowerShell 里外层再用单引号 |
| 结果里出现字符串 `"None"` | handler 对 NULL 直接 `str()` | 判空时按字符串 `"None"` 处理 |
| `/kg-*` 省略 target | 回落到 `firm_wide` 首行，也可用环境变量 `KG_TARGET` | 显式传企业名或 USCC 更安全 |

---

## 7. 命令速查

```powershell
# 元命令：找工具 / 自定义 SQL
& $PY $SLASH "/qcc-tool-search 实控人"
& $PY $SLASH '/qcc-sql "SELECT uscc,name,legal_rep FROM firm_wide"'   # SQL 必须整体加引号

# 原子查询 CLI（只查询，不推理）
& $PY skills/qcc-super/scripts/query.py profile --uscc 91330110MAEW31RB2Q
& $PY skills/qcc-super/scripts/query.py equity  --uscc 91330110MAEW31RB2Q
& $PY skills/qcc-super/scripts/query.py executives --uscc 91330110MAEW31RB2Q
& $PY skills/qcc-super/scripts/query.py events  --uscc 91330110MAEW31RB2Q --limit 100
```

企业不在 DuckDB 时，core handler 返回 `mcp_route`（所需 server + tool 列表），Agent 据此采集入库后重试。
