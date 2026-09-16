---
name: qcc-company
description: 企查查全平台 Connector Skill（V2.0）。以企业本体为核心，覆盖企查查 agent.qcc.com 的 4 大领域、11 个 MCP Server（10 个 streamableHttp + 1 个本地 stdio）、约 203 个工具，并顶层引用官方 4 大客群 27 个 SKILL。支持企业画像、法人关联公司图谱、尽调深度挖掘三大场景，内置实体强锚定、先扫后钻、脱水上下文与强语义负向防御规范。
description_zh: "企查查全平台数据接入：企业画像 / 法人关联图谱 / 尽调深挖"
description_en: "Qichacha full-platform connector: enterprise profile, legal-person related-company graph, and deep due diligence"
version: 2.0.0
display_name: "企查查（全平台）"
display_name_en: "Qcc Company"
visibility: "public"
icon: "https://codebuddy-platform-1258344699.cos.accelerate.myqcloud.com/public/45edac6b-2078-4678-89f3-6f9800cf5e5f/avatar/skill/au_44c7f07f-607.svg"
---

# 企查查（全平台）Connector Skill V2.0

本 Skill 替代 V1.0 单 Server 版本。V1.0 只声明了 qcc-company 一个 Server、15 个工具；V2.0 声明企查查 agent.qcc.com 全平台的 11 个 Server 与本体论分层工具清单，并把企业画像、法人关联公司图谱、尽调深度挖掘三大场景落成可执行的调用顺序。

V2.0 的工具来源分两类，阅读时请注意标注：

- **[实测]** 工具名来自 agent.qcc.com 官方 SKILL.md / QCC-MCP-TERMINOLOGY.md（2026-09-16 抓取），可直接调用。
- **[CLI 实测]** 工具名来自官方 CLI（qcc-agent-cli）命令，Server 归属为平台约定。
- **[推断]** 工具名为命名惯例推断，官方文档未直接列出，调用前需用远端 tools/list 校准。

---

## 一、平台地图与接入

### 1.1 认证与传输

所有 Server 共用同一个 API Key（agent.qcc.com 个人中心 → 获取我的 API KEY，每账号 1~5 个）。MCP 与 CLI 共用同一 Key，以 Bearer 形式传入：

- streamableHttp Server：HTTP header `Authorization: Bearer YOUR_API_KEY`，URL 形如 https://agent.qcc.com/mcp/<domain>/stream。注意 `GET /mcp/<domain>` 返回 404 是正常现象，入口必须带 `/stream` 后缀；未带 Authorization 时 initialize 返回 401 Unauthorized。
- 本地 stdio Server：只有 qcc-document-mcp 一个，用 `npx -y qcc-document-mcp` 启动（需 Node 20+），Key 走环境变量 `QCC_DOCUMENT_AUTHORIZATION=Bearer YOUR_API_KEY`。
- CLI 兜底通道：`npm install -g qcc-agent-cli` 后 `qcc init --authorization "Bearer YOUR_API_KEY"`，形如 `qcc company get_company_registration_info "企查查科技股份有限公司"`、`qcc regulation get_legal_regulation_search "数据出境"`、`qcc tender search_tenders "智慧工地"`、`qcc document parse_document --file_path "./财报.pdf"`、`qcc document get_parse_result "TASK_ID"`。CLI 零 Token 消耗、零幻觉，是 MCP 通道不可用时的确定性取数路径。

### 1.2 服务端矩阵

| Server | 定位 | 本 Skill 收录工具数 | 特殊约束 |
|---|---|---|---|
| **qcc-company** 企业基座 | 实体锚定与工作流基础层 | 16 | 所有场景第一步 |
| **qcc-risk** 风控大脑 | 风险维度分诊与熔断 | 35 | 先扫后钻，触发即熔断 |
| **qcc-ipr** 知产引擎 | 知识产权资产深度尽调 | 7 | 商标 / 专利翻页需申请开通 |
| **qcc-operation** 经营罗盘 | 经营动态与合规背书 | 12 | 招投标 / 招聘 / 舆情 |
| **qcc-history** 历史存档 | 时间维度穿透 | 20 | 需企业实名认证后开通 |
| **qcc-executive** 董监高画像 | 以人查风险 | 32 | 必须「企业 + 人名」双锚 |
| **qcc-legal-regulation** 法规 | 法规检索 | 1 | 跨主体，不限于单家企业 |
| **qcc-legal-case** 司法 | 类案与立案检索 | 2 | 跨主体 |
| **qcc-tender** 标讯 | 全国招投标 | 1 | 跨主体 |
| **qcc-document** 远端文档 | URL 解析 | 2 | 需公网可访问 URL |
| **qcc-document-mcp** 本地文档 | 本机文件解析 | 2 | stdio，需 Node 20+ |

### 1.3 计费与限额（写报告时引用）

注册赠 500 积分（30 天有效）；每日赠 普通 100 / VIP 150 / SVIP 200（当日有效）；充值 1 元 = 10 积分；同主体自然月积分封顶（个体工商户 20 / 企业 100 / 董监高人员 100）。批量场景（如一图铺全部董监高）必须先做单锚扫描、只对命中项下钻，避免把月度封顶打爆。

---

## 二、通用调用纪律（所有场景强制）

### 2.1 实体强锚定（18 位统一社会信用代码先行）

企查查业务工具只认真正的企业全称或 18 位统一社会信用代码。因此任何场景的第一步都是实体锚定，不得跳过：

1. 用户给的是品牌名、简称、别名（"腾讯"、"字节"、"华为云"）→ 先调 get_company_by_query 取候选主体列表，让用户确认或按注册资本 / 成立地择优，锁定唯一主体。
2. 用户给的是全称但可能有微差（少"市"、少"有限公司"、错别字）→ 调 verify_company_accuracy 用名称 + 信用代码双向核实。名称与代码不匹配时**停下纠错**，不得继续调用其他工具。
3. 用户已给 18 位信用代码 → 直接作为后续所有调用的主键，报告首行回显该代码。
4. 锚定结果（全称 + 信用代码 + 登记状态）写入上下文，后续工具全部复用同一主键，禁止中途换主体。

### 2.2 先扫后钻（防散弹枪）

凡一次性排查 2 个以上风险维度，一律先扫后钻，禁止逐个原子风险工具散弹枪式调用：

1. **分诊**：先调 get_company_risk_scan 一次拿企业自身各风险维度的命中计数（脱水版，只有有无 + 条数，不含明细）。
2. **下钻**：只对 count > 0 的维度调对应原子工具取明细。
3. **归零**：count = 0 的维度直接判定"无记录"，不再调用原子工具。
4. **单维问句**：用户只问某一项（"有没有失信"）时直接调该原子工具，无需先扫。
5. **定性必须有明细证据**：给任何风险维度定性（"多为原告身份"、"属正常维权"）之前，必须已下钻该维度明细；未下钻只能写"裁判文书 77 条（未取明细）"。
6. **关联扫描单层预警**：查关联企业 / 关联方风险先调 get_company_related_risk_scan 或 get_executive_related_risk_scan 拿风险面，只对"有风险关联方"单点下钻，禁止先列对外投资再逐个扫。
7. **year 留空拿全量**：立案 / 裁判 / 公告类带 year 过滤的工具，取全量时 year 一律留空（缺省即返回全部年份）；严禁为覆盖多年逐年循环调用。需要按年分桶时，基于一次拿回的全量在报告侧自行分桶。

### 2.3 上下文脱水（摘要与提示）

大结果不返回全量明细，而是返回 `摘要` 字段 + `提示` 字段。例：get_case_filing_info 返回 摘要="累计共有 3531 条记录，已实时检索 2025-06-05 至今的动态，包含 30 条记录…最近一次立案日期为 2026-04-09，案由为侵害商标权纠纷"，提示="该维度数据较多，已为您展示前 30 条"。

处理约定：

- `摘要` 是权威结论，必须逐字引用其数字与日期，不得重新统计或改写。
- `提示` 表明返回是截断子集，报告必须注明"已展示前 N 条 / 累计 M 条"，不得把子集当作全量。
- 脱水版结果只用于分诊，不能作为定性证据（见 2.2 第 5 条）。

### 2.4 强语义负向防御

空结果不返回空数组，而返回一段中文结论。例：get_serious_violation 返回 搜索结果="经企查查底层数据库全量核查实体 华******司，当前未发现任何【严重违法】记录。此项核心合规风控排查安全，允许进入下一步审计。"

处理约定：

- **逐字引用**该中文结论的措辞，保留"未发现任何【严重违法】记录"这样的限定，不要改写成"无严重违法"。
- **禁止**把"查无记录"升级为"绝对无风险"、"信用无瑕"、"可放心合作"。查无记录只代表当前时间点、当前数据源、当前可见范围内无该维度记录。
- 结论必须带三要素：查什么（维度）、查谁（实体全称 + 信用代码）、查到什么时候（数据截止时间 / 检索区间）。
- 负向结论与正向结论同等重要，报告中单列，不得埋在脚注。

---

## 三、工具清单（本体论分层 · 按 Server 分组）

### A 层 · 企业基座 qcc-company

定位：全平台工作流基础层。所有场景的实体锚定都在这里完成，其余 Server 都以本层输出的「企业全称 + 18 位统一社会信用代码」为主键入参。

典型触发语：这家公司做什么的 / 注册资本和成立时间 / 股东和实控人是谁 / 有没有上市 / 开票信息查一下。

### 1. 实体检索 `get_company_by_query`

按关键词检索企业候选主体列表，用于品牌名 / 简称 / 错拼的消歧。返回候选列表而非唯一主体，需人工或规则择优后再进入业务工具。

**参数**：`keyword`（品牌名 / 简称 / 部分名称）

**典型触发语**："查一下腾讯"、"华为相关的公司有哪些"

### 2. 企业信息核实 `verify_company_accuracy`

核实企业名称与统一社会信用代码是否匹配，是实体锚定的闸门工具。名称与代码不匹配必须停下纠错，不得带错误主体继续调用其他工具。

**⚠️ 特殊参数要求**：必须同时提供 `name`（企业名称或法定代表人）和 `searchKey`（统一社会信用代码），缺一不可。用户未提供信用代码时先询问。

### 3. 企业简介 `get_company_profile`

企业名称、业务简介及所属行业分类。构建企业画像的语义起点，用于判断企业真实主业与行业归属。

### 4. 工商登记信息 `get_company_registration_info`

统一社会信用代码、法定代表人、注册资本、成立日期、登记状态、注册地址、经营范围。身份核验的主数据源，报告首屏必用。

**典型触发语**："XX 公司还在正常经营吗"、"帮我查 XX 的工商登记信息"

### 5. 股东信息 `get_shareholder_info`

股东构成，含投资人姓名或名称、持股比例、认缴出资额、出资时间。关联图谱的第一跳。

### 6. 实际控制人 `get_actual_controller`

穿透股权后的最终控制主体。聚合值（持股比例、穿透层级）必须逐字引用，不得自行重算。

### 7. 受益所有人 `get_beneficial_owners`

最终受益自然人。反洗钱与央行 3 号令 25% 阈值穿透的取数源。

### 8. 主要管理人员 `get_key_personnel`

董事、监事、高管等主要人员姓名与职务。是切换到 qcc-executive 做以人查风险的入口名单。

### 9. 对外投资 `get_external_investments`

作为投资方的对外投资记录，含被投资企业名称、状态、持股比例、认缴出资额。关联图谱的主扩展边。

### 10. 历史变更记录 `get_change_records`

工商登记的历史变更事项，含变更内容、变更前后对比、变更日期。与 qcc-history 互补：本工具是变更事件流，qcc-history 是分维度的历史存档。

### 11. 财务数据 `get_financial_data`

资产负债、营收利润、偿债能力、营运效率、成长能力及关键财务比率。资产负债率 > 100% 即资不抵债，须显式提示。

### 12. 年度报告 `get_annual_reports`

历年工商年报，含经营状态、从业人数、股东股权转让、对外投资等年报披露内容。用于核实是否按时申报年报。

### 13. 上市信息 `get_listing_info`

上市日期、股票代码与简称、上市交易所、总市值、总股本、发行日期。数据为工商披露口径，非实时行情。

### 14. 分支机构 `get_branches`

分支机构列表，含机构名称、负责人、所在地区、成立日期、登记状态。

### 15. 联系方式 `get_contact_info`

公开联系信息，含电话、邮箱、官网、ICP 备案信息。

### 16. 税号开票信息 `get_tax_invoice_info`

增值税开票所需信息：纳税人识别号、企业名称、地址、联系电话、开户行及账号。

### B 层 · 风控大脑 qcc-risk

定位：风险维度分诊与熔断层。两个扫描工具是聚合分诊入口，其余 33 个是原子明细工具。任何风险结论都必须能追到具体明细工具，不得只凭扫描计数定性。

典型触发语：这家有没有失信 / 是不是被执行人 / 有没有被限高 / 诉讼多不多 / 有没有行政处罚 / 股权有没有被冻结 / 有没有破产清算。

### 17. 企业风险扫描 `get_company_risk_scan`

一次返回企业自身各风险维度的命中计数（脱水版：有无 + 条数，不含明细）。多风险维度排查的强制第一步。

### 18. 企业关联风险扫描 `get_company_related_risk_scan`

以单锚（一家企业）返回关联方风险面，含"有风险关联方 + 命中计数"。单层预警，禁止自动下钻到每个关联方。

### 19. 失信被执行人 `get_dishonest_info`

失信被执行人名单记录，含执行法院、案号、立案日期、履行情况。

### 20. 被执行人 `get_judgment_debtor_info`

被执行人（判决债务人）记录，含执行标的、执行法院、立案日期。

### 21. 限制高消费 `get_high_consumption_restriction`

限制高消费记录，含决定机关、决定日期、限制期限。

### 22. 限制出境 `get_exit_restriction`

限制出境记录，含决定机关、决定日期。

### 23. 终本案件 `get_terminated_cases`

终结本次执行案件，代表已生效债权未能执行的存量风险。

### 24. 立案信息 `get_case_filing_info`

立案数量、案由分布、最近立案日期。返回走脱水（摘要 + 提示），详见 2.3。

**典型触发语**："XX 公司这些年立了多少案"、"最近一次诉讼是什么案由"

### 25. 裁判文书 `get_judicial_documents`

裁判文书列表，可按 role 过滤原告 / 被告分布，用于判断企业是诉讼发起方还是被动方。

### 26. 裁判文书详情 `get_judicial_document_detail`

单份裁判文书详情，含当事人、案由、审理法院、判决结果、审理进程。

### 27. 开庭公告 `get_court_notice`

开庭公告，用于追踪正在进行的诉讼。

### 28. 庭审通知 `get_hearing_notice`

庭审通知，含庭审时间与案由。

### 29. 送达公告 `get_service_notice`

法院送达公告，含送达文书类型与当事人。

### 30. 法院公告 `get_service_announcement`

法院公告类信息（含公告送达、破产公告等）。

### 31. 司法拍卖 `get_judicial_auction`

司法拍卖标的，含标的物、起拍价、拍卖状态。资产被拍卖是重大执行信号。

### 32. 评估询价 `get_valuation_inquiry`

财产评估与询价记录，反映法院对财产价值的认定。

### 33. 经营异常 `get_business_exception`

经营异常名录记录，含列入原因、列入日期、移出情况。

### 34. 严重违法失信 `get_serious_violation`

严重违法失信名单记录。空结果返回强语义中文结论，详见 2.4。

### 35. 行政处罚 `get_administrative_penalty`

行政处罚记录，含处罚机关、处罚事由、处罚金额、处罚日期。

### 36. 环保处罚 `get_environmental_penalty`

环保行政处罚记录，含处罚机关、违法事实、处罚金额。

### 37. 纪律处分记录 `get_disciplinary_list`

行业主管或自律组织的纪律处分记录。

### 38. 欠税公告 `get_tax_arrears_notice`

欠税公告记录，含欠税金额、税种、公告日期。

### 39. 税务异常 `get_tax_abnormal`

税务异常状态记录，含异常原因与状态变更。

### 40. 税收违法 `get_tax_violation`

税收违法失信记录，含违法类型与认定机关。

### 41. 违约信息 `get_default_info`

债务违约信息，含违约类型、违约金额、披露来源。

### 42. 股权冻结 `get_equity_freeze`

股权被冻结记录，含冻结标的、冻结份额、执行法院、冻结期限。

### 43. 股权出质 `get_equity_pledge_info`

股权出质记录，含出质人、质权人、出质股权数额。

### 44. 股权质押 `get_stock_pledge_info`

上市公司股权质押记录，含质押股份数量、质权人、平仓风险。

### 45. 动产抵押 `get_chattel_mortgage_info`

动产抵押登记记录，含抵押物、抵押权人、担保金额。

### 46. 土地抵押 `get_land_mortgage_info`

土地抵押登记记录，含土地位置、抵押面积、担保金额。

### 47. 担保信息 `get_guarantee_info`

对外担保信息，含被担保方、担保金额、担保方式。评估或有负债的主取数源。

### 48. 破产重整 `get_bankruptcy_reorganization`

破产重整 / 重整申请记录，含受理法院、申请日期、进程状态。

### 49. 清算信息 `get_liquidation_info`

清算组备案与清算信息，含清算原因、清算组人员、备案日期。

### 50. 注销备案 `get_cancellation_record_info`

企业注销备案信息，含注销原因与注销日期。

### 51. 简易注销 `get_simple_cancellation_info`

简易注销公告信息，含公告日期与公告状态。

### C 层 · 知产引擎 qcc-ipr

定位：知识产权资产深度尽调层。用于判断企业的技术护城河与资产可处置性，也是关联交易与资产腾挪的线索源。

典型触发语：这家公司有多少专利 / 商标情况怎么样 / 有没有软件著作权 / 知产有没有被质押。

### 52. 商标信息 `get_trademark_info`

商标注册与持有信息，含注册号、商标名称、类别、法律状态、专用权期限。翻页能力需向平台申请开通。

### 53. 专利信息 `get_patent_info`

专利持有信息，含申请号、发明名称、专利类型、法律状态、申请与授权日期。

### 54. 作品著作权 `get_copyright_work_info`

作品著作权登记信息，含作品名称、作品类别、登记号。

### 55. 软件著作权 `get_software_copyright_info`

软件著作权登记信息，含软件名称、登记号、版本号、开发完成日期。

### 56. 标准信息 `get_standard_info`

参与制定或采用的标准信息，含标准号、标准名称、层级、状态。

### 57. 网络服务与数字资产备案 `get_internet_service_info`

ICP 备案、APP / 小程序 / 公众号 / 抖音 / 快手等数字资产矩阵。用于核实企业的线上经营载体。

### 58. 知识产权质押 `get_ipr_pledge`

知识产权质押记录，含质押标的、质权人、质押金额。知产被质押常伴随融资紧张。

### D 层 · 经营罗盘 qcc-operation

定位：经营动态与合规背书层。工商数据是静态的，本层数据是动态的，用于判断企业当下的经营活力与政府监管视角的合规背书。

典型触发语：最近中标了什么项目 / 有什么资质 / 招聘规模怎么样 / 最近有什么新闻 / 信用评价等级是多少。

### 59. 招投标信息 `get_bidding_info`

企业作为投标方 / 中标方 / 招标方的招投标记录，含项目名称、金额、中标日期、采购方。

### 60. 资质证书 `get_qualifications`

资质证书信息，含证书名称、证书编号、发证机关、有效期。

### 61. 行政许可 `get_administrative_license`

行政许可记录，含许可事项、许可机关、许可日期、有效期。

### 62. 电信许可 `get_telecom_license`

电信业务经营许可证信息，含业务种类、证号、有效期。

### 63. 信用评级 `get_credit_evaluation`

官方信用评级，含纳税信用 A-D 级等政府监管视角的评级结果。

### 64. 进出口信用 `get_import_export_credit`

海关信用等级（高级认证 / 一般认证 / 失信企业），直接对应通关便利与履约信用。跨境贸易金融的第一门槛。

### 65. 招聘信息 `get_recruitment_info`

招聘信息，含岗位、人数、发布时间。招聘规模变化是经营活跃度的先行指标。

### 66. 新闻舆情 `get_news_sentiment`

新闻舆情信息，含标题、来源、发布时间、情感倾向。

### 67. 公告信息 `get_company_announcement`

上市公司公告与重大事件披露信息。

### 68. 融资历程 `get_financing_records`

融资记录，含轮次、金额、投资方、融资日期。

### 69. 荣誉信息 `get_honor_info`

荣誉与资质背书信息，含荣誉名称、授予机构、授予年份。

### 70. 双随机抽查 `get_random_check`

双随机抽查记录，含检查机关、检查结果、抽查日期。

### E 层 · 历史存档 qcc-history（需企业实名认证）

定位：时间维度穿透层。当前状态是快照，历史存档是轨迹。本层是"洗白识别"的核心：把已被移除的风险记录与历史治理变更找回来。

典型触发语：这家公司的历史股东有哪些 / 法代换过几次 / 以前有没有失信 / 股权是不是刚解冻。

**开通约束**：本 Server 需企业实名认证后开通。未开通时全部历史工具不可用，按场景工作流的降级路径处理。

### 71. 历史工商登记 `get_historical_registration`

历史工商登记信息（成立时间、注册资本、经营范围的历史版本）。用于主体延续性判断。

**状态**：[推断] 工具名为命名惯例推断，官方文档未直接列出，调用前需用远端 tools/list 校准。

### 72. 历届法定代表人 `get_historical_legal_rep`

历届法定代表人及其任职区间。治理稳定性判断的主工具：法代频繁更换是高风险信号。

### 73. 历任高管 `get_historical_executives`

历任董事、监事、高管名单及任职区间。

### 74. 历史股东 `get_historical_shareholders`

历史股东及其持股变动区间。识别"换壳式"股权腾挪与代持。

### 75. 历史对外投资 `get_historical_investments`

历史对外投资记录，含已退出的投资标的。

### 76. 历史失信 `get_historical_dishonest`

历史失信被执行人记录，含移除日期。已被移除的失信记录是当前视图看不到的，必须查历史。

### 77. 历史被执行人 `get_historical_judgment_debtor`

历史被执行人记录，含执行结案情况。

### 78. 历史限制高消费 `get_historical_high_consumption_ban`

历史限制高消费记录及解除情况。

### 79. 历史经营异常 `get_historical_business_exception`

历史经营异常记录及移出原因（补报年报移出、地址可联系移出等）。移出原因本身是风险信号。

### 80. 历史行政处罚 `get_historical_admin_penalty`

历史行政处罚记录，含处罚金额与事由。

### 81. 历史行政许可 `get_historical_admin_license`

历史行政许可记录，含被吊销、被注销的许可。

### 82. 历史欠税 `get_historical_tax_arrears`

历史欠税公告记录及补缴情况。

### 83. 历史股权冻结 `get_historical_equity_freeze`

历史股权冻结及解冻记录。刚解冻的股权伴随新的诉讼概率高。

### 84. 历史股权出质 `get_historical_equity_pledge`

历史股权出质及解除记录。

### 85. 历史动产抵押 `get_historical_chattel_mortgage`

历史动产抵押登记及解除记录。

### 86. 历史土地抵押 `get_historical_land_mortgage`

历史土地抵押登记及解除记录。

### 87. 历史知产质押 `get_historical_ipr_pledge`

历史知识产权质押及解除记录。

### 88. 历史商标 `get_historical_trademark`

历史商标持有与转移记录。商标集中过户是资产腾挪信号。

### 89. 历史专利 `get_historical_patent`

历史专利持有、转让与无效记录。

### 90. 历史终本案件 `get_historical_terminated_cases`

历史终本案件，含结案方式。

### F 层 · 董监高画像 qcc-executive

定位：以人查风险层。企业是壳，人才是稳定的风险载体。本层所有工具都要求「企业全称 + 董监高姓名」双锚，缺一不可。

典型触发语：这家公司的法代还有什么别的公司 / 实控人有没有失信 / 董事长是不是被执行人 / 帮我画一下法代的关联企业。

**双锚约束**：`name` 传企业全称，`keyword` 传董监高姓名。只有企业没有人名时，先用 get_key_personnel 或 get_actual_controller 拿到人名，再进入本层。

### 91. 董监高职务 `get_executive_positions`

指定人员在企业内的职务与任职区间。

**参数**：`name` + `keyword`

### 92. 法定代表人任职 `get_executive_legal_rep_roles`

指定人员担任法定代表人的企业清单。构建"同一法代"边的主工具。

**参数**：`name` + `keyword`

### 93. 投资任职 `get_executive_investments`

指定人员对外投资与任职的企业清单。构建"投资人-企业"边。

**参数**：`name` + `keyword`

### 94. 控制企业 `get_executive_controlled_companies`

指定人员实际控制的企业清单，含控制权路径。

**参数**：`name` + `keyword`

### 95. 关联企业 `get_executive_related_companies`

指定人员的关联企业全量清单，是"人-企业"二部图的邻居展开工具。

**参数**：`name` + `keyword`

### 96. 历任合伙人 `get_executive_historical_partners`

指定人员的历任合伙人。合伙关系是图谱的高价值边。

**参数**：`name` + `keyword`

### 97. 历任任职 `get_executive_historical_positions`

指定人员的历史任职记录，含已离任岗位。

**参数**：`name` + `keyword`

### 98. 历届法代任职 `get_executive_historical_legal_rep_roles`

指定人员担任法定代表人的历史记录，含已卸任企业。

**参数**：`name` + `keyword`

### 99. 历史投资 `get_executive_historical_investments`

指定人员的历史投资记录，含已退出的投资。

**参数**：`name` + `keyword`

### 100. 董监高个人风险扫描 `get_executive_risk_scan`

指定个人的各风险维度命中计数（脱水版）。个人风险排查的强制第一步，只对命中维度下钻。

**参数**：`name` + `keyword`

### 101. 董监高关联风险扫描 `get_executive_related_risk_scan`

以个人为锚的关联企业风险面，单层预警。

**参数**：`name` + `keyword`

### 102. 董监高-失信被执行人 `get_executive_dishonest`

指定个人的失信被执行人记录。

**参数**：`name` + `keyword`

### 103. 董监高-被执行人 `get_executive_judgment_debtor`

指定个人的被执行人记录。

**参数**：`name` + `keyword`

### 104. 董监高-限制高消费 `get_executive_high_consumption_ban`

指定个人的限制高消费记录。

**参数**：`name` + `keyword`

### 105. 董监高-限制出境 `get_executive_exit_restriction`

指定个人的限制出境记录。

**参数**：`name` + `keyword`

### 106. 董监高-裁判文书 `get_executive_judicial_docs`

指定个人的裁判文书记录。

**参数**：`name` + `keyword`

### 107. 董监高-立案信息 `get_executive_case_filing`

指定个人的立案记录。

**参数**：`name` + `keyword`

### 108. 董监高-开庭公告 `get_executive_court_notice`

指定个人的开庭公告。

**参数**：`name` + `keyword`

### 109. 董监高-庭审通知 `get_executive_hearing_notice`

指定个人的庭审通知。

**参数**：`name` + `keyword`

### 110. 董监高-送达公告 `get_executive_service_notice`

指定个人的法院送达公告。

**参数**：`name` + `keyword`

### 111. 董监高-终本案件 `get_executive_terminated_cases`

指定个人的终本案件。

**参数**：`name` + `keyword`

### 112. 董监高-诉前调解 `get_executive_pre_litigation_mediation`

指定个人的诉前调解记录。

**参数**：`name` + `keyword`

### 113. 董监高-行政处罚 `get_executive_admin_penalty`

指定个人的行政处罚记录。

**参数**：`name` + `keyword`

### 114. 董监高-股权冻结 `get_executive_equity_freeze`

指定个人名下被冻结的股权。

**参数**：`name` + `keyword`

### 115. 董监高-股权出质 `get_executive_equity_pledge`

指定个人名下的股权出质记录。

**参数**：`name` + `keyword`

### 116. 董监高-股权质押 `get_executive_stock_pledge`

指定个人名下的股权质押记录。

**参数**：`name` + `keyword`

### 117. 董监高-税务违法 `get_executive_tax_violation`

指定个人的税收违法记录。

**参数**：`name` + `keyword`

### 118. 董监高-评估询价 `get_executive_valuation_inquiry`

指定个人的财产评估询价记录。

**参数**：`name` + `keyword`

### 119. 董监高-财产悬赏 `get_executive_property_reward_notice`

指定个人的财产悬赏公告，是财产线索披露渠道。

**参数**：`name` + `keyword`

### 120. 董监高-失信历史 `get_executive_historical_dishonest`

指定个人的历史失信记录及移除情况。

**参数**：`name` + `keyword`

### 121. 董监高-被执行历史 `get_executive_historical_judgment_debtor`

指定个人的历史被执行记录。

**参数**：`name` + `keyword`

### 122. 董监高受益所有人 `get_executive_beneficial_owner`

指定个人的受益所有人画像，用于 UBO 与关联企业穿透。

**参数**：`name` + `keyword`

### G 层 · 跨主体检索 qcc-legal-regulation 与 qcc-legal-case

定位：跨主体检索层。前六层都是"以企业为锚"，本层是"以主题 / 案由为锚"，用于法规依据定位、类案比对与判例研判。

### 123. 法规检索 `get_legal_regulation_search`

法律法规全文检索，按关键词或条文定位法规依据。

**状态**：[CLI 实测] 对应 CLI 命令 qcc regulation get_legal_regulation_search。

**典型触发语**："数据出境有什么法规要求"、"帮我查反垄断法的对应条款"

### 124. 类案检索 `get_judicial_case_search`

跨主体的类案检索，按案由 / 关键词找同类判例。

**状态**：[CLI 实测] 对应 CLI 命令 qcc case get_judicial_case_search。

### 125. 立案检索 `get_case_filing_info`

跨主体的立案信息检索，与 B 层的同名工具区别在于本工具以案由 / 关键词为锚而非企业为锚。

**状态**：[CLI 实测] 与 B 层第 24 项同名，消歧以 Server 为准（见 3.1）。

### H 层 · 标讯检索 qcc-tender

定位：跨主体标讯检索层。按关键词 / 采购方 / 中标方检索全国招投标公告，用于核验投标真实性与发现关联企业共同投标。

### 126. 招投标检索 `search_tenders`

按关键词检索全国招投标公告。

**状态**：[CLI 实测] 对应 CLI 命令 qcc tender search_tenders。

**典型触发语**："查一下智慧工地相关的招标公告"

### I 层 · 文档解析 qcc-document 与 qcc-document-mcp

定位：长文档处理层。企查查工具返回的是结构化字段，财报、合同、裁判文书正文等长文本需要文档解析。qcc-document 走远端解析（需公网可访问 URL），qcc-document-mcp 走本机 stdio（需 Node 20+），二者工具同名、按部署形态选择其一。

### 127. 提交文档解析 `parse_document`

提交远端文档解析任务，入参为公网可访问 URL，返回 TASK_ID。

**状态**：[CLI 实测] 对应 CLI 命令 qcc document parse_document --file_url。

### 128. 查询解析结果 `get_parse_result`

按 TASK_ID 查询远端解析结果。

**状态**：[CLI 实测] 对应 CLI 命令 qcc document get_parse_result "TASK_ID"。

### 129. 本地文档解析 `parse_document`

解析本机文件（财报 PDF、合同、裁判文书等），入参为 file_path。走 stdio Server qcc-document-mcp。

**状态**：[CLI 实测] 对应 CLI 命令 qcc document parse_document --file_path。

### 130. 查询本地解析结果 `get_parse_result`

按 TASK_ID 查询本地解析结果。

**状态**：[CLI 实测] 对应 CLI 命令 qcc document get_parse_result "TASK_ID"。

### 3.1 工具名冲突消歧

平台内有 5 组重名或易混工具，本 Skill 的约定是"先写 Server、再写工具名"，机器可读的完整消歧表见 `field-index.json` 的 `conflicts`：

- `get_case_filing_info`：只在 B 层 qcc-company 风控侧存在，以企业为锚返回立案量与案由分布。G 层的跨主体立案检索用 `get_judicial_case_search`（同名工具名不存在跨 Server 冲突，但语义相邻，禁止混用：查"这家公司的立案量"用 qcc-risk，查"某案由有哪些案例"用 qcc-legal-case）。
- `parse_document`：I 层第 127 项是远端 qcc-document，第 129 项是本地 qcc-document-mcp。
- `get_parse_result`：I 层第 128 项是远端 qcc-document，第 130 项是本地 qcc-document-mcp。
- `get_change_records` 与 E 层 get_historical_* 系列：前者是变更事件流，后者是分维度历史存档，语义重叠但不等价，不可互相替代。
- 招投标检索有两条路径：H 层 qcc-tender 的 search_tenders 按关键词找全国标讯（查"某主题有哪些标"），D 层 qcc-operation 的 get_bidding_info 以企业为锚取该公司的投标 / 中标记录（查"这家公司中标了什么"）。

引用格式约定：在报告内部与日志中用 `server.tool` 形式（如 qcc-risk.get_company_risk_scan），在最终交付报告正文中改用业务表述（见五、输出规范），禁止出现工具代码名。

---

## 四、三大场景工作流

### 场景一 · 企业画像（一页纸）

目标：一页纸说清"这家企业是谁、做什么、谁控制、资产与经营怎么样"。

**Step 0 · 实体锚定（强制前置）**

1. 用户给品牌名 / 简称 → 调 get_company_by_query 取候选，锁定唯一主体。
2. 有信用代码 → 调 verify_company_accuracy 双向核实；不匹配则停下纠错。
3. 记录并回显：企业全称 + 18 位统一社会信用代码 + 登记状态。后续全部工具复用此主键。

**Step 1 · 基础层（qcc-company）**

调用顺序：get_company_registration_info → get_company_profile → get_shareholder_info → get_actual_controller → get_key_personnel → get_financial_data → get_annual_reports。
产出：身份、主业、股权、实控、管理、财务、年报七个事实块。

**Step 2 · 动态层（qcc-operation）**

调用顺序：get_bidding_info → get_recruitment_info → get_news_sentiment → get_financing_records → get_credit_evaluation。
产出：中标活跃度、招聘活力、舆情倾向、融资节奏、官方信用。

**Step 3 · 资产层（qcc-ipr）**

调用顺序：get_patent_info → get_trademark_info → get_software_copyright_info → get_internet_service_info → get_ipr_pledge。
产出：技术资产规模、品牌资产、数字载体矩阵、知产是否已被质押。

**Step 4 · 风险底色（qcc-risk，轻量）**

先调 get_company_risk_scan 一次，只对命中维度补一次明细（通常只补 get_dishonest_info / get_judgment_debtor_info / get_equity_freeze 三件）。
目的：画像必须带风险底色，但不做尽调级深挖。

**Step 5 · 一页纸输出**

七块固定骨架：身份与登记、主业与行业、股权结构与实控、经营动态、知产资产、财务与年报、风险底色。每块一句结论 + 关键数字。

**失败降级**

- get_financial_data 无数据 → 标注"未披露财务数据"，用 get_annual_reports 从业人数与股权转让做替代活跃度判断。
- get_bidding_info / get_recruitment_info 为空 → 属正常（非招投标行业），标注"该维度不适用"而非"无经营"。
- get_company_profile 缺失 → 用 get_company_registration_info 的经营范围字段替代主业描述，并标注口径。
- qcc-ipr 无记录 → 标注"未发现知产登记"，不得推断为"无技术能力"。

### 场景二 · 法人关联公司图谱（人-企业二部图）

目标：以目标企业为原点，画出"人 - 企业"二部图，识别同一控制人、共同股东、代持与壳关联。

**图模型**

- 顶点两类：**E 顶点**（企业，主键 = 全称 + 信用代码）、**P 顶点**（人，主键 = 姓名 + 身份证后四位或任职记录）。
- 边类型：**持股**（企业 → 企业，含持股比例）、**控制**（企业 → 人 / 人 → 企业，含穿透层级）、**任职**（人 → 企业，含职务）、**同一法代**（人 → 企业，法代身份边）、**共同股东**（人 / 企业 → 企业，共同持股边）、**历史**（任意边，附任职或持股区间）。

**Step 0 · 实体锚定**：同场景一 Step 0，产出唯一 E 顶点。

**Step 1 · 向上穿透（控制链）**

调用顺序：get_actual_controller → get_beneficial_owners → get_shareholder_info。
建边：控制边（企业 → 实控人）、共同股东边（股东 → 企业）。

**Step 2 · 横向扩展（对外投资）**

调用顺序：get_external_investments → get_branches。
建边：持股边（企业 → 被投资企业）、分支边（企业 → 分支机构）。对每个新企业顶点记录其信用代码，作为下一轮可展开的节点。

**Step 3 · 人员桥接（人-企业边的关键）**

调用顺序：get_key_personnel → get_actual_controller（取人名）→ 对关键人逐个调 get_executive_legal_rep_roles → get_executive_investments → get_executive_related_companies → get_executive_historical_partners。
建边：任职边、同一法代边、投资边、合伙边。
**注意**：本步骤必须双锚（企业 + 人名），只有企业没有人名时不得猜测人名，回到 get_key_personnel 取名单。

**Step 4 · 历史沿革（时间维度的边）**

调用顺序：get_change_records → get_historical_shareholders → get_historical_legal_rep → get_historical_executives → get_historical_investments。
建边：给已有边补时间区间；新增"历史持股"、"历史任职"、"历届法代"边。识别"当前无关联、历史有关联"的隐性关系。

**Step 5 · 关联风险面（单层预警，不逐点下钻）**

调用顺序：get_company_related_risk_scan（以目标企业为锚）→ 对关键人调 get_executive_related_risk_scan。
只对返回的"有风险关联方"单点下钻一次 get_company_risk_scan。

**Step 6 · 图谱输出**

输出结构：核心节点表（顶点 + 主键）、关系边表（源、目标、边类型、权重、时间区间）、控制链图（Mermaid 或树形文本）、隐性关联清单（仅历史存在的边）、风险关联方清单。

**失败降级**

- qcc-history 未开通（无企业实名认证）→ 跳过 Step 4，图谱降级为"当前快照图"，并显式标注"历史维度不可用，换壳式腾挪无法识别"。
- qcc-executive 缺人名 → 只做 Step 1、2、5，图谱降级为"企业-企业单部图"，并标注"未做人-企业桥接"。
- get_actual_controller 返回空 → 用 get_shareholder_info 的持股比例自行推导最大股东，并标注为"推导值（非接口聚合值）"。
- 关联企业数量超出积分封顶 → 按持股比例排序，只展开前 N 个高风险节点，并在报告中列出未展开节点名单。

### 场景三 · 尽调深度挖掘

目标：给出可决策的风险结论与风险分级，而不是罗列数据。

**Step 0 · 实体锚定**：同场景一 Step 0，且必须完成 verify_company_accuracy 双向核实。

**Step 1 · 基础层（qcc-company）**

调用顺序：get_company_registration_info → get_company_profile → get_shareholder_info → get_actual_controller → get_key_personnel → get_financial_data → get_external_investments → get_annual_reports。
关键判断：登记状态（吊销 / 注销即一票否决）、注册资本实缴、资不抵债（资产负债率 > 100%）。

**Step 2 · 风控层（qcc-risk）——先扫后钻**

1. 先调 get_company_risk_scan 拿全维度命中计数。
2. 对 count > 0 的维度逐个下钻明细：失信类 → get_dishonest_info；执行类 → get_judgment_debtor_info、get_high_consumption_restriction、get_exit_restriction、get_terminated_cases；诉讼类 → get_case_filing_info、get_judicial_documents（按 role 取原被告分布）、get_judicial_document_detail；处罚类 → get_administrative_penalty、get_environmental_penalty、get_serious_violation；监管类 → get_business_exception；税务类 → get_tax_arrears_notice、get_tax_abnormal、get_tax_violation；资产限制类 → get_equity_freeze、get_equity_pledge_info、get_stock_pledge_info、get_chattel_mortgage_info、get_land_mortgage_info；或有负债类 → get_guarantee_info；终局类 → get_bankruptcy_reorganization、get_liquidation_info、get_cancellation_record_info。
3. count = 0 的维度逐字引用强语义负向结论（见 2.4）。
4. 定性必须先下钻，未下钻只写计数并标注"（未取明细）"。

**Step 3 · 历史存档（洗白识别）**

调用顺序：get_historical_dishonest → get_historical_judgment_debtor → get_historical_business_exception → get_historical_admin_penalty → get_historical_equity_freeze → get_historical_shareholders → get_historical_legal_rep。
洗白信号判定：当前视图干净但历史有失信 / 被执行 / 经营异常，且时间集中在最近 12~24 个月 → 标注"疑似风险出清，需关注时效"。

**Step 4 · 经营与舆情（qcc-operation）**

调用顺序：get_bidding_info → get_recruitment_info → get_news_sentiment → get_credit_evaluation → get_honor_info → get_qualifications。
关键判断：中标金额与营收是否匹配、招聘是否断崖式下降、舆情是否存在集中负面、资质证书是否过期。

**Step 5 · 人员风险（qcc-executive）**

对法代与实控人：get_executive_risk_scan 先扫 → 命中维度下钻 get_executive_dishonest、get_executive_judgment_debtor、get_executive_high_consumption_ban、get_executive_exit_restriction → get_executive_related_companies 看关联面。

**Step 6 · 标讯与法规（qcc-tender / qcc-legal-case / qcc-legal-regulation）**

按需调用：search_tenders 核验投标真实性；get_judicial_case_search 找同类判例；get_legal_regulation_search 定位法规依据。

**Step 7 · 结论与风险分级**

风险分级采用四级：

- **A（可合作）**：登记状态存续、无失信 / 被执行 / 限高、无股权冻结、无资不抵债、无集中负面舆情。
- **B（可合作需监控）**：有非终局风险（如少量行政处罚、诉讼中），但不影响偿付与履约能力。
- **C（有条件合作）**：存在终局风险之一（失信 / 被执行 / 限高 / 经营异常 / 股权冻结 / 资不抵债），需追加担保或预付款限制。
- **D（不建议合作）**：吊销或注销、破产重整或清算、实控人个人失信且无法代偿、疑似风险出清。

**失败降级**

- qcc-history 未开通 → Step 3 跳过，风险分级上限为 C（无法排除洗白），并标注该限制。
- qcc-executive 缺人名或不可用 → Step 5 降级为只查法代（从 get_company_registration_info 取），标注"未覆盖其他董监高"。
- qcc-risk 扫描工具不可用 → 回退到逐维度原子调用，但必须在报告中标注"未做先扫后钻，调用成本已上升"。
- 积分耗尽 → 按"Step 2 风控层 > Step 3 历史层 > Step 5 人员层 > Step 4 经营层"的优先级保序降级，先保证风险结论，再保证画像完整性。
- 任何工具的结论都不得替代专业意见，报告末尾必须附数据来源与免责声明。

---

## 五、返回语义与输出规范

1. **数据忠实**：严格引用工具返回的原始字段值与 `摘要` 文字，不推导、不编造、不重新统计。聚合值（持股比例、穿透层级、累计条数）逐字引用。
2. **负向结论限定三要素**：查什么（维度）、查谁（实体全称 + 18 位信用代码）、查到什么时候（检索区间 / 数据截止）。禁止出现"绝对无风险"、"信用无瑕"、"零风险"、"可放心合作"这类绝对化表述。
3. **截断透明**：返回带 `提示` 的必须是子集，报告写"已展示前 N 条 / 累计 M 条"，不得把子集当全量。
4. **先扫后钻留痕**：出现任何风险定性判断，必须能追到对应明细工具；否则写"计数 X（未取明细）"。
5. **推导值标注**：接口未直接给出、由模型推导的值必须标注"推导值"，与接口聚合值区分。
6. **金额与日期**：金额注明单位（元 / 万元）；日期用 YYYY-MM-DD。
7. **章节绑定**：报告每个章节标注数据来源业务口径（企查查工商登记数据 / 企查查风险信息数据 / 企查查财务数据等），不出现 MCP 工具代码名、Server 名、字段名。
8. **时效提示**：工商登记数据 T+0；财务与年报依赖企业公示周期，可能滞后；舆情与招聘按发布时间排序展示。
9. **边界声明**：境外主体、个人信息、实时股价行情不在覆盖范围；本 Skill 输出为数据事实整理，不构成法律、投资、信贷等专业意见。

---

## 六、本体模型定义（供下游数据层落地）

供后续 graph / state 落地时直接使用：

**实体（节点）字段**

- 企业实体：`entity_id`（18 位统一社会信用代码）、`full_name`（全称）、`registration_status`（登记状态）、`legal_rep`（法代姓名）、`actual_controller`、`industry`、`source_server`。
- 人员实体：`person_name`、`role`（法代 / 董事 / 监事 / 高管 / 股东 / 实控人）、`source_server`。

**关系（边）字段**

- `source`、`target`、`relation_type`（持股 / 控制 / 任职 / 同一法代 / 共同股东 / 投资 / 合伙 / 分支）、`weight`（持股比例或控制权层级）、`period_start`、`period_end`、`is_historical`（是否仅存在于历史）、`evidence_tool`（取证工具 server.tool）、`evidence_date`。

**属性（边权重 / 风险标记）字段**

- `risk_flags`（失信 / 被执行 / 限高 / 冻结 / 质押 / 处罚 / 经营异常 / 破产重整）、`risk_count`（scan 计数）、`risk_detail_drilled`（是否已下钻明细，布尔）、`negative_conclusion`（强语义负向结论原文）、`summary_quote`（`摘要` 原文）、`truncation_hint`（`提示` 原文）。

---

## 七、官方客群 SKILL 索引（顶层引用）

企查查官方在 agent.qcc.com 发布 4 大客群 27 个 SKILL，每个 SKILL.md 由「业务规则 + MCP 工具清单 + 报告输出格式」三块构成。本 Skill 负责取数与调用纪律，行业报告的**模板与业务规则**以官方 SKILL 为准。

加载入口（AI 可直接 fetch）：

- 客群索引：https://agent.qcc.com/skill/v1/{banking|invest|legal|supply}/README.md
- 单个 SKILL：https://agent.qcc.com/skill/v1/{group}/{skill-id}/SKILL.md
- 广场：https://agent.qcc.com/skills
- 接入指南：https://agent.qcc.com/guide
- 术语对照表：https://agent.qcc.com/skill/QCC-MCP-TERMINOLOGY.md

### 7.1 银行合规风控（12 个 · group = banking）

| SKILL ID | 场景 |
|---|---|
| kyb-verification-qcc | KYB 企业核验（贷款申请后主体核验 + 关联风险扫描） |
| litigation-analysis-qcc | 诉讼风险评估（对手方诉讼轨迹 + 核心人员司法画像） |
| credit-due-diligence-qcc | 授信尽调报告 |
| counterparty-risk-qcc | 交易对手风险评估 |
| ubo-screening-qcc | 受益所有人识别（央行 3 号令 25% 阈值穿透） |
| trade-finance-compliance-qcc | 贸易融资合规核查 |
| credit-monitoring-qcc | 信贷风险定期监控（贷后） |
| equity-structure-qcc | 股权结构穿透分析 |
| executive-background-qcc | 高管背景核查 |
| business-health-scan-qcc | 经营健康度扫描 |
| guarantor-check-qcc | 担保方资信核查 |
| bankruptcy-monitor-qcc | 企业破产预警监控 |

### 7.2 投资尽调（6 个 · group = invest）

| SKILL ID | 场景 |
|---|---|
| ic-memo-qcc | 投资备忘录 |
| history-evolution-qcc | 历史沿革演进 |
| strip-profile-qcc | 企业画像速览 |
| competitor-analysis-qcc | 竞品分析 |
| fundraising-tracker-qcc | 融资追踪 |
| ip-asset-inventory-qcc | 知产资产盘点 |

### 7.3 法律法务（6 个 · group = legal）

| SKILL ID | 场景 |
|---|---|
| contract-review | 合同审查 |
| contract-party-check-qcc | 合同对手方核查 |
| labor-compliance | 劳动合规 |
| ip-infringement-alert | 知产侵权预警 |
| debt-recovery-assessment | 债务回收评估 |
| license-validation | 资质许可证核验 |

### 7.4 采购供应链（3 个 · group = supply）

| SKILL ID | 场景 |
|---|---|
| vendor-assess | 供应商评估 |
| supplier-annual-check-qcc | 供应商年度复核 |
| new-supplier-screening | 新供应商筛查 |

**使用约定**

1. **本地优先**：日常取数与调用纪律用本 Skill，不重复 fetch 官方 SKILL.md。
2. **需要行业报告模板时 fetch 官方 URL**：用户要求 KYB 报告、授信尽调报告、IC Memo 等行业模板时，fetch 对应 SKILL.md 并按其「报告输出格式」骨架填空。
3. **禁止自行翻译猜测 SKILL ID**：官方明确禁止从中文名翻译猜 ID，也禁止 fetch 清单外 URL。上表 27 个 ID 是唯一权威清单。
4. **安装目录约定**：官方 SKILL 安装到 `~/.claude/skills/{skill-id}/SKILL.md` 或 `{workspace}/.claude/skills/{skill-id}/SKILL.md`，与本项目的 `skills/` 目录并存不冲突。
5. **不搬运模板**：官方 SKILL 的报告骨架不复制进本 SKILL.md，避免双源漂移；只在报告中引用"按官方 {skill-id} 骨架输出"。

---

## 八、旧 → 新能力对照表（V1.0 → V2.0）

| 维度 | V1.0 | V2.0 |
|---|---|---|
| Server 声明 | 1 个（qcc-company，无 headers） | 11 个（10 streamableHttp + 1 stdio，全带 Authorization 占位） |
| 工具数 | 15 | 130 条目 / 127 个唯一工具名（4 组跨 Server 重名，见 3.1） |
| 实体锚定 | "名称或信用代码，优先全称" | 强制前置：get_company_by_query 消歧 + verify_company_accuracy 双向核实，名称微差停下纠错 |
| 风险排查 | 无风控工具 | get_company_risk_scan 先扫后钻 + 33 个原子明细 + 3 个关联 / 个人扫描 |
| 历史维度 | 无 | qcc-history 20 个工具 + 洗白识别判定规则 |
| 人员维度 | 仅主要人员名单 | qcc-executive 32 个工具 + 双锚约束 + 人-企业二部图构建步骤 |
| 知产 / 经营 | 无 | qcc-ipr 7 个 + qcc-operation 12 个 |
| 跨主体检索 | 无 | qcc-legal-regulation / qcc-legal-case / qcc-tender |
| 长文档 | 无 | qcc-document（远端 URL）+ qcc-document-mcp（本机 stdio） |
| 返回语义 | "空数据如实告知暂无记录" | `摘要` / `提示` 脱水规范 + 强语义负向结论逐字引用 + 禁止绝对化表述 |
| 场景工作流 | 5 步通用流程 | 三大场景各自完整调用顺序、失败降级、前置锚定 |
| 官方 SKILL | 未提及 | 顶层引用 4 客群 27 个 SKILL + 使用约定 |
| 计费限额 | 未提及 | 积分规则与封顶写入平台地图 |
| 工具名消歧 | 无冲突 | 4 组重名以 server.tool 形式消歧 |
| front-matter | version 1.0.0 | version 2.0.0，display_name / visibility / icon 字段保留 |

---

## 九、字段索引 field-index.json（路由数据）

同目录下 `field-index.json` 是本 SKILL 的机器可读路由索引，供任何 MCP 客户端 / 编排层在 Route 阶段查询，不改变本 SKILL 的文档结构：

| 表 | 内容 | 用途 |
|---|---|---|
| `entity_anchor` | 主键 + 锚定工具 | 计划的第一步 |
| `servers` | 11 个 Server 的 url / type / headers / 工具全量列表 | 正向索引，可直接拼 MCP 配置 |
| `fields` | 144 条业务字段 → (server, tool)，含 439 个同义词 tags | 倒排索引，自然语言关键词匹配 |
| `packages` | 8 个预设场景包（字段覆盖 + server 数 + 调用顺序） | 命中即整包调用，省规划开销 |
| `conflicts` | 5 组跨 Server 重名工具消歧 | 以 server.tool 引用 |
| `cost_rules` | 积分规则 + 封顶 + 省调用策略 + 禁用模式 | 成本控制 |
| `ontology_hooks` | 9 类关系定义 + 9 条风险推理规则 + 边字段 | 取数后的本体论推理入口 |
| `official_skills` | 4 客群 27 个 SKILL 清单与 URL 模式 | 行业报告模板顶层引用 |

字段条目带三个可选项，直接驱动省调用决策：`history_tool`（历史维度在需要时才多花 1 次调用）、`aggregate`（分诊工具，替代多个原子工具）、`cost`（该字段的省调用说明）。

用法：先按 `_readme.routing_algorithm` 的 8 步把自然语言映射成 `server → [tool]` 调用计划，计划第一步必须是 `entity_anchor`，风险类字段 ≥2 个维度时先走 `aggregate=true` 的分诊工具。

---

## 十、已知限制

1. **企业认证**：qcc-history 需企业实名认证后开通；未开通时历史维度全部不可用，尽调风险分级上限降至 C，图谱降级为当前快照图。
2. **翻页**：商标、专利等维度默认截断，翻页能力需向平台申请开通；返回 `提示` 字段即为截断证据，不得把前 30 条当全量。
3. **积分封顶**：同主体自然月封顶（个体工商户 20 / 企业 100 / 董监高人员 100），批量图谱铺点必须先单层扫描再定点下钻，避免月度封顶打爆。
4. **双锚依赖**：qcc-executive 必须"企业 + 人名"双参数；缺人名时只能降级为单部图。
5. **推断工具**：get_historical_registration 为命名惯例推断，其余工具均为官方 SKILL / 术语表 / CLI 实测来源，但官方未承诺工具名长期稳定，上线前应跑一次远端 tools/list 校准。
6. **数据覆盖**：以中国境内工商登记主体为主；境外主体、个人信息、实时股价行情不覆盖。
7. **本地 stdio 环境**：qcc-document-mcp 需 Node 20+，仅在本机可用，无法通过 streamableHttp 代理访问。

---

## 十一、边界与免责

- 本 Skill 输出为企查查公开数据的事实整理，不构成法律、投资、信贷、审计等专业意见。
- 风险分级（A / B / C / D）是基于本 Skill 调用结果的内部规则，不是企查查官方结论，最终决策需结合人工尽调。
- 官方 SKILL 的业务规则与报告模板以 agent.qcc.com 为唯一权威源，本 Skill 不复制其内容。
