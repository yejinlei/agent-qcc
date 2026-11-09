-- ============================================================
-- qcc-super 数据模型：DuckDB 大宽表
-- 说明：
--   · firm_wide    : 1 行/企业，100+ 列汇总视图（Agent 快速扫描用）
--   · event_wide   : 1 行/事件，明细表（穿透、关联、评分用）
--   · tool_catalog : 169 工具字段目录（路由 + 审计用）
--   · mcp_call_log : 每次调用记录（审计追溯）
-- 幂等：全部 IF NOT EXISTS，可重复执行
-- ============================================================

-- ---------- 企业宽表 ----------
CREATE TABLE IF NOT EXISTS firm_wide (
  -- 逻辑主键（uscc 唯一，DuckDB 不支持表级 UNIQUE，去重在 ingest 层做）
  uscc                   VARCHAR,
  name                   VARCHAR,
  name_short             VARCHAR,
  legal_rep              VARCHAR,

  -- 工商信息（get_company_registration_info）
  reg_capital            DECIMAL(20,2),
  reg_capital_currency   VARCHAR,
  established_at         DATE,
  business_status        VARCHAR,          -- 存续/注销/吊销/迁出/吊销未注销
  company_type           VARCHAR,          -- 有限责任公司/股份有限公司/...
  registered_addr        VARCHAR,
  business_scope         TEXT,
  unified_credit_code    VARCHAR,          -- 与 uscc 同值，冗余便于查询
  approved_at            DATE,
  deregistered_at        DATE,

  -- 企业简介（get_company_profile）
  industry               VARCHAR,
  industry_qcc           VARCHAR,
  industry_chain         VARCHAR,
  company_profile        TEXT,

  -- 联系方式（get_contact_info）
  phone_main             VARCHAR,
  email_main             VARCHAR,
  website                VARCHAR,
  website_is_official    BOOLEAN,
  icp_record             VARCHAR,

  -- 上市信息（get_listing_info）
  is_listed              BOOLEAN,
  listing_exchange       VARCHAR,
  stock_code             VARCHAR,
  stock_short_name       VARCHAR,
  listing_date           DATE,
  total_market_cap       DECIMAL(20,2),
  total_shares           DECIMAL(20,2),
  issue_date             DATE,
  issue_amount           DECIMAL(20,2),

  -- 实际控制人 / 受益所有人（get_actual_controller / get_beneficial_owners）
  controller_name        VARCHAR,
  controller_type        VARCHAR,          -- person / firm
  controller_stake       DECIMAL(10,6),    -- 总持股比例
  controller_voting      DECIMAL(10,6),    -- 表决权比例
  ubo_name               VARCHAR,
  ubo_stake              DECIMAL(10,6),

  -- 股东与治理
  shareholder_count      INTEGER,
  executive_count        INTEGER,
  branch_count           INTEGER,
  investment_count       INTEGER,

  -- 财务数据（get_financial_data）
  revenue                DECIMAL(20,2),
  net_profit             DECIMAL(20,2),
  total_assets           DECIMAL(20,2),
  total_liabilities      DECIMAL(20,2),
  owner_equity           DECIMAL(20,2),
  current_ratio          DECIMAL(10,4),
  debt_to_asset_ratio    DECIMAL(10,4),
  gross_margin           DECIMAL(10,4),
  net_margin             DECIMAL(10,4),
  revenue_yoy            DECIMAL(10,4),
  asset_yoy              DECIMAL(10,4),

  -- 34 类风险扫描（get_company_risk_scan，批量工具，1 次调用覆盖）
  has_dishonest                  BOOLEAN,
  has_restricted_consumption     BOOLEAN,
  has_judgment_debtor            BOOLEAN,
  has_equity_freeze              BOOLEAN,
  has_business_exception         BOOLEAN,
  has_admin_penalty              BOOLEAN,
  has_tax_arrears                BOOLEAN,
  has_tax_abnormal               BOOLEAN,
  has_severe_illegality          BOOLEAN,
  has_environmental_penalty      BOOLEAN,
  has_product_sampling           BOOLEAN,
  has_court_notice               BOOLEAN,
  has_court_document             BOOLEAN,
  has_court_hearing              BOOLEAN,
  has_bankruptcy                 BOOLEAN,
  has_clearance                  BOOLEAN,
  has_admin_license              BOOLEAN,
  has_credit_rating              BOOLEAN,
  has_credit_guarantee           BOOLEAN,
  has_business_status_issue      BOOLEAN,
  has_equity_pledge              BOOLEAN,
  has_property_auction           BOOLEAN,
  has_pre_litigation             BOOLEAN,
  has_notice_announcement        BOOLEAN,
  has_annual_report_issue        BOOLEAN,
  has_default_record             BOOLEAN,
  has_overdue_bill               BOOLEAN,
  has_customs_dishonest          BOOLEAN,
  has_export_import_credit       BOOLEAN,
  has_disciplinary_list          BOOLEAN,
  has_related_risk               BOOLEAN,
  has_shareholder_risk           BOOLEAN,
  has_executive_risk             BOOLEAN,
  has_financial_risk             BOOLEAN,
  risk_count_total               INTEGER,

  -- 知识产权计数（qcc-ipr）
  patent_count            INTEGER,
  trademark_count         INTEGER,
  software_copyright_count INTEGER,
  work_copyright_count    INTEGER,
  icp_license_count       INTEGER,

  -- 事件计数（冗余汇总，便于快速筛选）
  penalty_count           INTEGER,
  court_case_count        INTEGER,
  dishonest_count         INTEGER,
  execution_count         INTEGER,
  tender_count            INTEGER,
  employee_count          INTEGER,

  -- 元数据
  updated_at              TIMESTAMP DEFAULT current_timestamp,
  mcp_call_count          INTEGER,
  cache_version           VARCHAR,

  -- JSON 兜底（原始完整返回，Schema 变更时不改建表）
  raw_registration        JSON,
  raw_profile             JSON,
  raw_financial           JSON,
  raw_risk_scan           JSON,
  raw_shareholder         JSON,
  raw_executive           JSON,
  raw_controller          JSON,
  raw_listing             JSON,
  raw_ipr                 JSON,
  raw_annual_report       JSON,
  raw_contact             JSON
);

CREATE INDEX IF NOT EXISTS idx_firm_uscc ON firm_wide(uscc);
CREATE INDEX IF NOT EXISTS idx_firm_name ON firm_wide(name);
CREATE INDEX IF NOT EXISTS idx_firm_status ON firm_wide(business_status);
CREATE INDEX IF NOT EXISTS idx_firm_risk ON firm_wide(risk_count_total);
CREATE INDEX IF NOT EXISTS idx_firm_updated ON firm_wide(updated_at);


-- ---------- 事件宽表（一对多明细）----------
-- 所有多行返回的 MCP 工具结果，统一落到这里，raw_json 兜底
CREATE TABLE IF NOT EXISTS event_wide (
  id                       INTEGER,
  uscc                     VARCHAR NOT NULL,
  name                     VARCHAR,

  -- 事件分类（对齐 34 类风险扫描）
  event_type               VARCHAR NOT NULL,     -- dishonest/penalty/court_document/...
  event_subtype            VARCHAR,               -- 更细分类
  occurred_at              DATE,
  effective_at             DATE,
  ended_at                 DATE,
  amount                   DECIMAL(20,2),
  currency                 VARCHAR DEFAULT 'CNY',
  severity                 VARCHAR,               -- HIGH / MEDIUM / LOW
  status                   VARCHAR,               -- 在册/已解除/已结案

  -- 案件/处罚/执行共用字段
  case_no                  VARCHAR,               -- 案号 / 处罚决定书号
  authority                VARCHAR,                -- 处罚机关 / 执行法院 / 受理法院
  reason                   VARCHAR,                -- 事由
  content                  VARCHAR,                -- 处罚内容 / 判决结果
  plaintiff                VARCHAR,
  defendant                VARCHAR,
  third_party              VARCHAR,

  -- 来源
  source_tool              VARCHAR,               -- MCP 工具名
  source_server            VARCHAR,               -- MCP server 名
  discovered_at            TIMESTAMP DEFAULT current_timestamp,

  -- 原始返回
  raw_json                 JSON
);

CREATE INDEX IF NOT EXISTS idx_event_uscc ON event_wide(uscc);
CREATE INDEX IF NOT EXISTS idx_event_type ON event_wide(event_type);
CREATE INDEX IF NOT EXISTS idx_event_time ON event_wide(occurred_at);
CREATE INDEX IF NOT EXISTS idx_event_severity ON event_wide(severity);
CREATE INDEX IF NOT EXISTS idx_event_source ON event_wide(source_tool);


-- ---------- 股权关系表（穿透推理专用）----------
CREATE TABLE IF NOT EXISTS equity_edge (
  id                       INTEGER,
  holder_uscc              VARCHAR,               -- 股东（企业）
  holder_name              VARCHAR,
  holder_type              VARCHAR,               -- firm / person
  holder_person_id         VARCHAR,               -- 自然人标识（脱敏哈希）
  target_uscc              VARCHAR NOT NULL,      -- 被投企业
  target_name              VARCHAR,
  stake_ratio              DECIMAL(10,6),         -- 持股比例
  subscribed_capital       DECIMAL(20,2),         -- 认缴出资额
  paid_in_capital          DECIMAL(20,2),         -- 实缴出资额
  investment_date          DATE,
  is_direct                BOOLEAN DEFAULT TRUE,  -- 是否直接持股
  source_tool              VARCHAR,               -- get_shareholder_info / get_external_investments
  raw_json                 JSON,
  updated_at               TIMESTAMP DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_equity_holder ON equity_edge(holder_uscc);
CREATE INDEX IF NOT EXISTS idx_equity_target ON equity_edge(target_uscc);
CREATE INDEX IF NOT EXISTS idx_equity_holder_type ON equity_edge(holder_type);


-- ---------- 统一边表（穿透/关联分析专用） ----------
-- 归并 5 种关系：equity / legal_rep / controller / ubo / executive
-- 一行 = 一条关系，direction 明确语义：
--   firm_to_firm    : subject=holder,  object=target (equity_edge)
--   person_to_firm  : subject=person:<姓名>, object=firm (legal_rep / controller / ubo / executive)
CREATE TABLE IF NOT EXISTS firm_relation (
  id                       INTEGER,
  subject_id               VARCHAR NOT NULL,      -- holder_uscc OR person:<姓名>
  subject_name             VARCHAR,
  subject_type             VARCHAR NOT NULL,      -- 'Enterprise' | 'Person'
  object_uscc              VARCHAR NOT NULL,      -- 目标企业 uscc
  object_name              VARCHAR,
  relation_type            VARCHAR NOT NULL,      -- equity / legal_rep / controller / ubo / executive
  edge_type                VARCHAR NOT NULL,      -- hasShareholder / hasLegalRep / hasController / hasUBO / hasExecutive
  direction                VARCHAR NOT NULL,      -- firm_to_firm / person_to_firm
  weight                   DECIMAL(10,6) DEFAULT 1.0,  -- equity=stake_ratio, 其他=1.0
  role                     VARCHAR,               -- 高管的 role（董事长/总经理/监事...）
  source_table             VARCHAR NOT NULL,      -- equity_edge / firm_wide / executive
  source_id                INTEGER,               -- 源表主键 id
  is_direct                BOOLEAN DEFAULT TRUE,
  discovered_at            TIMESTAMP DEFAULT current_timestamp,
  updated_at               TIMESTAMP DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_rel_subject ON firm_relation(subject_id);
CREATE INDEX IF NOT EXISTS idx_rel_object ON firm_relation(object_uscc);
CREATE INDEX IF NOT EXISTS idx_rel_type ON firm_relation(relation_type);
CREATE INDEX IF NOT EXISTS idx_rel_edge ON firm_relation(edge_type);


-- ---------- 董监高表 ----------
CREATE TABLE IF NOT EXISTS executive (
  id                       INTEGER,
  uscc                     VARCHAR NOT NULL,
  name                     VARCHAR NOT NULL,
  role                     VARCHAR,               -- 法代/董事长/总经理/董事/监事/高管
  appointed_at             DATE,
  resigned_at              DATE,
  is_active                BOOLEAN DEFAULT TRUE,
  source_tool              VARCHAR,
  raw_json                 JSON
);

CREATE INDEX IF NOT EXISTS idx_exec_uscc ON executive(uscc);
CREATE INDEX IF NOT EXISTS idx_exec_name ON executive(name);
CREATE INDEX IF NOT EXISTS idx_exec_role ON executive(role);


-- ---------- 工具目录表（169 工具字段清单）----------
CREATE TABLE IF NOT EXISTS tool_catalog (
  id                       INTEGER,
  server                   VARCHAR NOT NULL,
  tool_name                VARCHAR NOT NULL,
  title                    VARCHAR,
  description_clean        TEXT,
  description_full         TEXT,
  return_field_count       INTEGER,
  return_fields            VARCHAR,               -- 字段名以 | 分隔
  input_params_json        JSON,
  update_frequency         VARCHAR,
  data_source              VARCHAR,
  is_batch                 BOOLEAN DEFAULT FALSE, -- 批量工具
  efficiency               DECIMAL(5,2) DEFAULT 1.0,
  created_at               TIMESTAMP DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_tool_server ON tool_catalog(server);
CREATE INDEX IF NOT EXISTS idx_tool_name ON tool_catalog(tool_name);


-- ---------- MCP 调用日志（审计）----------
CREATE TABLE IF NOT EXISTS mcp_call_log (
  id                       INTEGER,
  uscc                     VARCHAR,
  search_key               VARCHAR,
  server                   VARCHAR,
  tool_name                VARCHAR,
  input_params             JSON,
  response_hash            VARCHAR,
  cache_hit                BOOLEAN,
  called_at                TIMESTAMP DEFAULT current_timestamp,
  latency_ms               INTEGER
);

CREATE INDEX IF NOT EXISTS idx_log_uscc ON mcp_call_log(uscc);
CREATE INDEX IF NOT EXISTS idx_log_tool ON mcp_call_log(tool_name);
CREATE INDEX IF NOT EXISTS idx_log_time ON mcp_call_log(called_at);
