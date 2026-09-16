# 官方客群 SKILL 权威索引（4 客群 · 27 个）

本文件是 `skills/qcc-company/SKILL.md` 第七节的展开版：收录企查查官方 4 大客群 27 个 SKILL 的完整权威 URL，供需要行业报告模板时直接 fetch。

快照来源：`https://agent.qcc.com/skill/v1/<group>/README.md`（2026-09-16 实测抓取，HTTP 200）。

## 加载入口

| 入口 | URL |
|---|---|
| 客群索引（4 个） | `https://agent.qcc.com/skill/v1/banking/README.md` · `.../invest/README.md` · `.../legal/README.md` · `.../supply/README.md` |
| 单个 SKILL | `https://agent.qcc.com/skill/v1/<group>/<skill-id>/SKILL.md` |
| 技能广场 | `https://agent.qcc.com/skills` |
| 接入指南 | `https://agent.qcc.com/guide` |
| MCP 术语对照表 | `https://agent.qcc.com/skill/QCC-MCP-TERMINOLOGY.md` |

每个 SKILL.md 由三块构成：**业务规则 + MCP 工具清单 + 报告输出格式**。

安装目录约定（官方口径）：

- Claude Code：`~/.claude/skills/{skill-id}/SKILL.md` 或 `{workspace}/.claude/skills/{skill-id}/SKILL.md`
- WorkBuddy：`~/.workbuddy/skills/{skill-id}/SKILL.md`
- Cursor / Trae / 其他：按各自 SKILL 规范处理

## 一、银行合规风控（12 个 · group = banking）

1. KYB 企业核验 — `kyb-verification-qcc` — https://agent.qcc.com/skill/v1/banking/kyb-verification-qcc/SKILL.md
2. 诉讼风险评估 — `litigation-analysis-qcc` — https://agent.qcc.com/skill/v1/banking/litigation-analysis-qcc/SKILL.md
3. 授信尽调报告 — `credit-due-diligence-qcc` — https://agent.qcc.com/skill/v1/banking/credit-due-diligence-qcc/SKILL.md
4. 交易对手风险评估 — `counterparty-risk-qcc` — https://agent.qcc.com/skill/v1/banking/counterparty-risk-qcc/SKILL.md
5. 受益所有人识别 — `ubo-screening-qcc` — https://agent.qcc.com/skill/v1/banking/ubo-screening-qcc/SKILL.md
6. 贸易融资合规核查 — `trade-finance-compliance-qcc` — https://agent.qcc.com/skill/v1/banking/trade-finance-compliance-qcc/SKILL.md
7. 信贷风险定期监控 — `credit-monitoring-qcc` — https://agent.qcc.com/skill/v1/banking/credit-monitoring-qcc/SKILL.md
8. 股权结构穿透分析 — `equity-structure-qcc` — https://agent.qcc.com/skill/v1/banking/equity-structure-qcc/SKILL.md
9. 高管背景核查 — `executive-background-qcc` — https://agent.qcc.com/skill/v1/banking/executive-background-qcc/SKILL.md
10. 经营健康度扫描 — `business-health-scan-qcc` — https://agent.qcc.com/skill/v1/banking/business-health-scan-qcc/SKILL.md
11. 担保方资信核查 — `guarantor-check-qcc` — https://agent.qcc.com/skill/v1/banking/guarantor-check-qcc/SKILL.md
12. 企业破产预警监控 — `bankruptcy-monitor-qcc` — https://agent.qcc.com/skill/v1/banking/bankruptcy-monitor-qcc/SKILL.md

## 二、投资尽调（6 个 · group = invest）

1. 投资备忘录 — `ic-memo-qcc` — https://agent.qcc.com/skill/v1/invest/ic-memo-qcc/SKILL.md
2. 历史沿革演进 — `history-evolution-qcc` — https://agent.qcc.com/skill/v1/invest/history-evolution-qcc/SKILL.md
3. 企业画像速览 — `strip-profile-qcc` — https://agent.qcc.com/skill/v1/invest/strip-profile-qcc/SKILL.md
4. 竞品分析 — `competitor-analysis-qcc` — https://agent.qcc.com/skill/v1/invest/competitor-analysis-qcc/SKILL.md
5. 融资追踪 — `fundraising-tracker-qcc` — https://agent.qcc.com/skill/v1/invest/fundraising-tracker-qcc/SKILL.md
6. 知产资产盘点 — `ip-asset-inventory-qcc` — https://agent.qcc.com/skill/v1/invest/ip-asset-inventory-qcc/SKILL.md

## 三、法律法务（6 个 · group = legal）

1. 合同审查 — `contract-review` — https://agent.qcc.com/skill/v1/legal/contract-review/SKILL.md
2. 合同对手方核查 — `contract-party-check-qcc` — https://agent.qcc.com/skill/v1/legal/contract-party-check-qcc/SKILL.md
3. 劳动合规 — `labor-compliance` — https://agent.qcc.com/skill/v1/legal/labor-compliance/SKILL.md
4. 知产侵权预警 — `ip-infringement-alert` — https://agent.qcc.com/skill/v1/legal/ip-infringement-alert/SKILL.md
5. 债务回收评估 — `debt-recovery-assessment` — https://agent.qcc.com/skill/v1/legal/debt-recovery-assessment/SKILL.md
6. 资质许可证核验 — `license-validation` — https://agent.qcc.com/skill/v1/legal/license-validation/SKILL.md

## 四、采购供应链（3 个 · group = supply）

1. 供应商评估 — `vendor-assess` — https://agent.qcc.com/skill/v1/supply/vendor-assess/SKILL.md
2. 供应商年度复核 — `supplier-annual-check-qcc` — https://agent.qcc.com/skill/v1/supply/supplier-annual-check-qcc/SKILL.md
3. 新供应商筛查 — `new-supplier-screening` — https://agent.qcc.com/skill/v1/supply/new-supplier-screening/SKILL.md

## 使用约定

1. **本地优先**：日常取数与调用纪律用本目录的 `SKILL.md`，不要重复 fetch 官方 SKILL.md。
2. **需要行业报告模板时 fetch 官方 URL**：用户要求 KYB 报告 / 授信尽调报告 / IC Memo 等行业模板时，fetch 对应 `SKILL.md` 并按其「报告输出格式」骨架填空。
3. **禁止自行翻译猜测 SKILL ID**：官方明确禁止从中文名翻译猜 ID，禁止 fetch 清单外 URL，禁止加载已知不存在的 ID（例如 `judicial-risk-qcc`）。上表 27 个 ID 是唯一权威清单。
4. **不搬运模板**：官方报告骨架不复制进本仓库，避免双源漂移；交付时引用「按官方 {skill-id} 骨架输出」即可。
5. **命令触发方式**：安装后可用 `/{skill-id}` 命令触发，也可用自然语言（如「帮我对 XX 公司做 KYB 核验」）触发。
