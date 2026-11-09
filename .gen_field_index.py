# -*- coding: utf-8 -*-
"""Generate skills/qcc-company/field-index.json.

Field -> MCP server -> tool routing index. 130 entries, every tool must
appear in the server table so the index stays verifiable offline.
"""
import json
from collections import defaultdict
from pathlib import Path

OUT = Path("skills/qcc-company/field-index.json")

C, R, I, O, H, X = "qcc-company", "qcc-risk", "qcc-ipr", "qcc-operation", "qcc-history", "qcc-executive"
LR, LC, TD, DO, DM = "qcc-legal-regulation", "qcc-legal-case", "qcc-tender", "qcc-document", "qcc-document-mcp"

SERVERS = [
    dict(server=C, name="企业基座", url="https://agent.qcc.com/mcp/company/stream", type="streamableHttp",
         domain="企业数据", gated=None, paged=False, role="实体锚定与工作流基础层；所有场景第一步"),
    dict(server=R, name="风控大脑", url="https://agent.qcc.com/mcp/risk/stream", type="streamableHttp",
         domain="企业数据", gated=None, paged=True, role="风险维度分诊与熔断；先扫后钻"),
    dict(server=I, name="知产引擎", url="https://agent.qcc.com/mcp/ipr/stream", type="streamableHttp",
         domain="企业数据", gated=None, paged=True, role="知识产权资产与数字资产矩阵"),
    dict(server=O, name="经营罗盘", url="https://agent.qcc.com/mcp/operation/stream", type="streamableHttp",
         domain="企业数据", gated=None, paged=True, role="经营动态与政府监管合规背书"),
    dict(server=H, name="历史存档", url="https://agent.qcc.com/mcp/history/stream", type="streamableHttp",
         domain="企业数据", gated="enterprise_auth", paged=True, role="时间维度穿透；洗白识别与治理稳定性"),
    dict(server=X, name="董监高画像", url="https://agent.qcc.com/mcp/executive/stream", type="streamableHttp",
         domain="企业数据", gated="person_name", paged=True, role="以人查风险；人-企业二部图展开"),
    dict(server=LR, name="法规检索", url="https://agent.qcc.com/mcp/regulation/stream", type="streamableHttp",
         domain="法律数据", gated=None, paged=True, role="法律法规全文检索；跨主体"),
    dict(server=LC, name="司法检索", url="https://agent.qcc.com/mcp/case/stream", type="streamableHttp",
         domain="法律数据", gated=None, paged=True, role="类案与立案检索；跨主体"),
    dict(server=TD, name="标讯检索", url="https://agent.qcc.com/mcp/tender/stream", type="streamableHttp",
         domain="标讯数据", gated=None, paged=True, role="全国招投标公告检索；跨主体"),
    dict(server=DO, name="远端文档解析", url="https://agent.qcc.com/mcp/document/stream", type="streamableHttp",
         domain="智能文档", gated="remote_url", paged=False, role="解析公网可访问 URL 的长文档"),
    dict(server=DM, name="本地文档解析",
         command="npx -y qcc-document-mcp", type="stdio", domain="智能文档",
         gated="local_stdio", paged=False, role="解析本机文件；唯一非 streamableHttp 入口，需 Node 20+"),
]

URL = "https://agent.qcc.com/mcp/{}/stream"


def e(field, tags, server, tool, anchor="entity", **kw):
    return dict(field=field, tags=tags, server=server, tool=tool, anchor=anchor, **kw)


FIELDS = [
    # ---------- A 层 qcc-company（企业本体 / 图谱） ----------
    e("企业搜索", ["公司搜索", "主体检索", "品牌名", "简称", "别名", "消歧", "查哪家公司"], C, "get_company_by_query", "keyword"),
    e("企业名称核实", ["名称核实", "名称匹配", "全称校验", "名称有没有写错"], C, "verify_company_accuracy", "entity"),
    e("统一社会信用代码", ["信用代码", "uscc", "18位代码", "社会统一信用代码"], C, "get_company_registration_info"),
    e("登记状态", ["经营状态", "存续", "吊销", "注销", "在营", "还在不在营业"], C, "get_company_registration_info"),
    e("法定代表人", ["法代", "法人", "法定代表人姓名"], C, "get_company_registration_info"),
    e("注册资本", ["注册资金", "认缴", "注册金额"], C, "get_company_registration_info"),
    e("实缴资本", ["实缴", "实缴金额"], C, "get_company_registration_info"),
    e("成立日期", ["成立时间", "成立时间点", "注册日期"], C, "get_company_registration_info"),
    e("注册地址", ["地址", "住所", "注册地", "所在市"], C, "get_company_registration_info"),
    e("经营范围", ["经营范围", "业务范围", "做什么生意"], C, "get_company_registration_info"),
    e("企业规模", ["企业规模", "人员规模", "小微企业"], C, "get_company_registration_info"),
    e("企业简介", ["公司简介", "主营业务", "业务简介", "公司介绍"], C, "get_company_profile"),
    e("所属行业", ["行业", "行业分类", "属于哪个行业", "行业归属"], C, "get_company_profile"),
    e("股东信息", ["股东", "股东构成", "持股", "出资比例", "认缴出资"], C, "get_shareholder_info"),
    e("股权比例", ["持股比例", "占股", "股权占比"], C, "get_shareholder_info"),
    e("实际控制人", ["实控人", "实际控制", "最终控制人", "控制人是谁"], C, "get_actual_controller"),
    e("受益所有人", ["ubo", "受益人", "最终受益自然人", "25%阈值"], C, "get_beneficial_owners"),
    e("主要人员", ["董监高", "高管名单", "董事", "监事", "总经理", "管理团队", "ceo"], C, "get_key_personnel"),
    e("对外投资", ["对外投资", "子公司", "参股公司", "投资组合", "投资版图"], C, "get_external_investments"),
    e("分支机构", ["分公司", "分支机构", "全国布局", "网点"], C, "get_branches"),
    e("工商变更记录", ["变更记录", "工商变更", "变更历史", "变更事项", "法人变更"], C, "get_change_records"),
    e("财务数据", ["财务", "财务数据", "资产负债表", "资产负债率", "营收", "利润", "财务指标", "资不抵债"], C, "get_financial_data"),
    e("营业收入", ["营收", "收入", "营业额", "主营收入"], C, "get_financial_data"),
    e("净利润", ["净利润", "利润", "净利"], C, "get_financial_data"),
    e("资产负债率", ["负债率", "资产负债比", "偿债能力", "资不抵债"], C, "get_financial_data"),
    e("所有者权益", ["净资产", "所有者权益", "净资产额"], C, "get_financial_data"),
    e("年度信息", ["年报", "年度报告", "从业人数", "参保人数", "社保人数"], C, "get_annual_reports"),
    e("上市信息", ["上市", "股票代码", "总市值", "交易所", "总股本", "发行日期"], C, "get_listing_info"),
    e("联系方式", ["联系方式", "电话", "邮箱", "官网", "联系电话"], C, "get_contact_info"),
    e("税号开票信息", ["开票信息", "纳税人识别号", "开户行", "银行账号", "发票信息"], C, "get_tax_invoice_info"),

    # ---------- B 层 qcc-risk（风险本体 / 分诊 + 明细） ----------
    e("风险扫描", ["风险扫描", "风险总览", "风险面", "34类风险", "风险标签", "风险体检"], R, "get_company_risk_scan",
                 aggregate=True, truncatable=True),
    e("关联企业风险", ["关联风险", "关联方风险", "关联企业风险面", "风险关联方"], R, "get_company_related_risk_scan",
                 aggregate=True, truncatable=True),
    e("失信被执行人", ["失信", "失信被执行人", "老赖", "失信名单"], R, "get_dishonest_info",
                 history_tool="get_historical_dishonest"),
    e("被执行人", ["被执行", "判决债务人", "执行案"], R, "get_judgment_debtor_info",
                 history_tool="get_historical_judgment_debtor"),
    e("限制高消费", ["限高", "限制高消费"], R, "get_high_consumption_restriction",
                 history_tool="get_historical_high_consumption_ban"),
    e("限制出境", ["限出境", "限境", "限制出境"], R, "get_exit_restriction"),
    e("终本案件", ["终本", "终结本次执行"], R, "get_terminated_cases", history_tool="get_historical_terminated_cases"),
    e("立案信息", ["立案", "立案数", "立案日期", "涉诉数量"], R, "get_case_filing_info",
                 aggregate=True, truncatable=True),
    e("裁判文书", ["裁判文书", "裁判", "判决书", "判决文书"], R, "get_judicial_documents",
                 truncatable=True),
    e("裁判文书详情", ["文书详情", "判决结果", "审理进程", "判决内容"], R, "get_judicial_document_detail"),
    e("开庭公告", ["开庭公告", "开庭公告"], R, "get_court_notice"),
    e("庭审通知", ["庭审通知", "开庭时间"], R, "get_hearing_notice"),
    e("送达公告", ["送达公告", "送达文书"], R, "get_service_notice"),
    e("法院公告", ["法院公告", "公告送达"], R, "get_service_announcement"),
    e("司法拍卖", ["司法拍卖", "拍卖标的", "拍卖价", "资产拍卖"], R, "get_judicial_auction"),
    e("评估询价", ["评估询价", "财产评估", "评估价"], R, "get_valuation_inquiry"),
    e("经营异常", ["经营异常", "异常名录", "列入异常"], R, "get_business_exception",
                 history_tool="get_historical_business_exception"),
    e("严重违法", ["严重违法", "严重违法失信", "严重违法名单"], R, "get_serious_violation"),
    e("行政处罚", ["行政处罚", "处罚", "行政罚款"], R, "get_administrative_penalty",
                 history_tool="get_historical_admin_penalty"),
    e("环保处罚", ["环保处罚", "环保违法", "环保处罚"], R, "get_environmental_penalty"),
    e("纪律处分", ["纪律处分", "行业处分", "自律处分"], R, "get_disciplinary_list"),
    e("欠税公告", ["欠税", "欠税公告", "欠税金额"], R, "get_tax_arrears_notice",
                 history_tool="get_historical_tax_arrears"),
    e("税务异常", ["税务异常"], R, "get_tax_abnormal"),
    e("税收违法", ["税收违法", "税务违法", "税务失信"], R, "get_tax_violation"),
    e("违约信息", ["违约", "债务违约"], R, "get_default_info"),
    e("股权冻结", ["股权冻结", "冻结股权"], R, "get_equity_freeze", history_tool="get_historical_equity_freeze"),
    e("股权出质", ["股权出质", "出质股权", "股权质押登记"], R, "get_equity_pledge_info",
                 history_tool="get_historical_equity_pledge"),
    e("股权质押", ["股权质押", "上市质押", "平仓风险"], R, "get_stock_pledge_info"),
    e("动产抵押", ["动产抵押", "动产抵押登记"], R, "get_chattel_mortgage_info",
                 history_tool="get_historical_chattel_mortgage"),
    e("土地抵押", ["土地抵押", "土地抵押登记"], R, "get_land_mortgage_info",
                 history_tool="get_historical_land_mortgage"),
    e("担保信息", ["担保", "对外担保", "或有负债", "担保金额"], R, "get_guarantee_info"),
    e("破产重整", ["破产重整", "破产", "重整", "破产申请"], R, "get_bankruptcy_reorganization"),
    e("清算信息", ["清算", "清算组", "清算备案"], R, "get_liquidation_info"),
    e("注销备案", ["注销备案", "注销"], R, "get_cancellation_record_info"),
    e("简易注销", ["简易注销", "简易注销公告"], R, "get_simple_cancellation_info"),

    # ---------- C 层 qcc-ipr ----------
    e("商标", ["商标", "商标持有", "商标注册"], I, "get_trademark_info", paged_needs_apply=True,
                 history_tool="get_historical_trademark"),
    e("专利", ["专利", "专利持有", "发明专利", "专利申请"], I, "get_patent_info", paged_needs_apply=True,
                 history_tool="get_historical_patent"),
    e("作品著作权", ["作品著作权", "作品著作权"], I, "get_copyright_work_info"),
    e("软件著作权", ["软著", "软件著作权", "计算机软件著作权"], I, "get_software_copyright_info"),
    e("标准信息", ["标准", "参与标准", "标准制定"], I, "get_standard_info"),
    e("网络服务备案", ["icp备案", "icp", "app备案", "小程序备案", "公众号", "抖音", "数字资产", "算法备案"],
     I, "get_internet_service_info"),
    e("知产质押", ["知产出质", "知产质押", "知识产权质押"], I, "get_ipr_pledge",
                 history_tool="get_historical_ipr_pledge"),

    # ---------- D 层 qcc-operation ----------
    e("招投标", ["招投标", "中标", "投标", "招标", "招标记录", "中标记录"], O, "get_bidding_info"),
    e("资质证书", ["资质", "资质证书", "资质等级"], O, "get_qualifications"),
    e("行政许可", ["行政许可", "许可证", "许可事项"], O, "get_administrative_license",
                 history_tool="get_historical_admin_license"),
    e("电信许可", ["电信许可", "电信经营许可证"], O, "get_telecom_license"),
    e("信用评级", ["信用评级", "纳税信用", "a级纳税人", "信用等级"], O, "get_credit_evaluation"),
    e("进出口信用", ["海关信用", "海关信用等级", "高级认证", "一般认证", "进出口信用"], O, "get_import_export_credit"),
    e("招聘信息", ["招聘", "招聘岗位", "招聘规模", "招人"], O, "get_recruitment_info"),
    e("新闻舆情", ["舆情", "新闻", "负面舆情", "媒体报道"], O, "get_news_sentiment"),
    e("公告信息", ["公告", "上市公司公告", "重大事件披露"], O, "get_company_announcement"),
    e("融资历程", ["融资", "融资历程", "轮次", "投资方"], O, "get_financing_records"),
    e("荣誉信息", ["荣誉", "荣誉称号", "资质证书背书"], O, "get_honor_info"),
    e("双随机抽查", ["双随机", "双随机抽查", "抽查检查"], O, "get_random_check"),

    # ---------- E 层 qcc-history（全部 gated: enterprise_auth） ----------
    e("历史工商登记", ["历史工商", "工商历史", "历史注册资本", "历史经营范围"], H, "get_historical_registration",
                 gated="enterprise_auth"),
    e("历届法定代表人", ["历届法代", "法代变更历史", "法代换过几次", "历任法代"], H, "get_historical_legal_rep",
                 gated="enterprise_auth"),
    e("历任高管", ["历任高管", "高管历史", "历史董监高"], H, "get_historical_executives", gated="enterprise_auth"),
    e("历史股东", ["历史股东", "历史股东"], H, "get_historical_shareholders", gated="enterprise_auth"),
    e("历史对外投资", ["历史投资", "历史对外投资"], H, "get_historical_investments", gated="enterprise_auth"),
    e("历史失信", ["历史失信", "曾经的失信", "失信移除", "以前失信"], H, "get_historical_dishonest",
                 gated="enterprise_auth"),
    e("历史被执行", ["历史被执行", "以前被执行"], H, "get_historical_judgment_debtor", gated="enterprise_auth"),
    e("历史限高", ["历史限高", "以前限高"], H, "get_historical_high_consumption_ban", gated="enterprise_auth"),
    e("历史经营异常", ["历史经营异常", "异常名录移出"], H, "get_historical_business_exception",
                 gated="enterprise_auth"),
    e("历史行政处罚", ["历史行政处罚", "以前被罚"], H, "get_historical_admin_penalty", gated="enterprise_auth"),
    e("历史行政许可", ["历史行政许可", "许可证被吊销"], H, "get_historical_admin_license",
                 gated="enterprise_auth"),
    e("历史欠税", ["历史欠税", "以前的欠税"], H, "get_historical_tax_arrears", gated="enterprise_auth"),
    e("历史股权冻结", ["历史股权冻结", "股权已解冻"], H, "get_historical_equity_freeze", gated="enterprise_auth"),
    e("历史股权出质", ["历史股权出质", "出质已解除"], H, "get_historical_equity_pledge", gated="enterprise_auth"),
    e("历史动产抵押", ["历史动产抵押", "动产已解除"], H, "get_historical_chattel_mortgage", gated="enterprise_auth"),
    e("历史土地抵押", ["历史土地抵押", "土地已解除"], H, "get_historical_land_mortgage", gated="enterprise_auth"),
    e("历史知产质押", ["历史知产质押", "知产质押已解除"], H, "get_historical_ipr_pledge", gated="enterprise_auth"),
    e("历史商标", ["历史商标", "商标历史过户"], H, "get_historical_trademark", gated="enterprise_auth"),
    e("历史专利", ["历史专利", "专利转让历史"], H, "get_historical_patent", gated="enterprise_auth"),
    e("历史终本案件", ["历史终本", "终本案件结案"], H, "get_historical_terminated_cases", gated="enterprise_auth"),

    # ---------- F 层 qcc-executive（全部双锚 + person_name） ----------
    *[e(name, tags, X, tool, "entity+person", gated="person_name")
      for name, tags, tool in [
        ("高管职务", ["高管职务", "董监高职务"], "get_executive_positions"),
        ("法代任职企业", ["法代任职", "法人任职企业", "担任法代的企业"], "get_executive_legal_rep_roles"),
        ("高管投资任职", ["高管投资任职", "投资人任职"], "get_executive_investments"),
        ("控制企业", ["控制企业", "控制的企业", "名下企业"], "get_executive_controlled_companies"),
        ("关联企业", ["关联企业", "关联企业清单"], "get_executive_related_companies"),
        ("历任合伙人", ["历任合伙人", "合伙人"], "get_executive_historical_partners"),
        ("历任任职", ["历任任职", "高管任职历史"], "get_executive_historical_positions"),
        ("历届法代任职", ["历届法代任职", "法代任职历史"], "get_executive_historical_legal_rep_roles"),
        ("历史投资任职", ["历史投资任职"], "get_executive_historical_investments"),
        ("高管个人风险扫描", ["个人风险扫描", "高管风险扫描", "高管风险面", "18项个人风险"], "get_executive_risk_scan"),
        ("高管关联风险扫描", ["高管关联风险", "个人关联风险"], "get_executive_related_risk_scan"),
        ("高管失信", ["高管失信", "法代失信", "个人失信"], "get_executive_dishonest"),
        ("高管被执行", ["高管被执行", "个人被执行"], "get_executive_judgment_debtor"),
        ("高管限高", ["高管限高", "个人限高"], "get_executive_high_consumption_ban"),
        ("高管限出境", ["高管限出境", "个人限出境"], "get_executive_exit_restriction"),
        ("高管裁判文书", ["高管裁判文书", "个人裁判"], "get_executive_judicial_docs"),
        ("高管立案", ["高管立案", "个人立案"], "get_executive_case_filing"),
        ("高管开庭公告", ["高管开庭公告", "个人开庭公告"], "get_executive_court_notice"),
        ("高管庭审通知", ["高管庭审通知", "个人庭审通知"], "get_executive_hearing_notice"),
        ("高管送达公告", ["高管送达公告", "个人送达"], "get_executive_service_notice"),
        ("高管终本案件", ["高管终本", "个人终本"], "get_executive_terminated_cases"),
        ("高管诉前调解", ["高管诉前调解", "个人诉前调解"], "get_executive_pre_litigation_mediation"),
        ("高管行政处罚", ["高管行政处罚", "个人行政处罚"], "get_executive_admin_penalty"),
        ("高管股权冻结", ["高管股权冻结", "个人股权冻结"], "get_executive_equity_freeze"),
        ("高管股权出质", ["高管股权出质", "个人股权出质"], "get_executive_equity_pledge"),
        ("高管股权质押", ["高管股权质押", "个人股权质押"], "get_executive_stock_pledge"),
        ("高管税务违法", ["高管税务违法", "个人税务违法"], "get_executive_tax_violation"),
        ("高管评估询价", ["高管评估询价", "个人财产评估"], "get_executive_valuation_inquiry"),
        ("高管财产悬赏", ["财产悬赏", "悬赏公告", "财产线索"], "get_executive_property_reward_notice"),
        ("高管历史失信", ["高管历史失信", "个人历史失信"], "get_executive_historical_dishonest"),
        ("高管历史被执行", ["高管历史被执行", "个人历史被执行"], "get_executive_historical_judgment_debtor"),
        ("高管受益所有人", ["高管ubo", "个人受益所有人"], "get_executive_beneficial_owner"),
      ]],

    # ---------- G/H 层 跨主体检索 ----------
    e("法规检索", ["法规", "法律法规", "法律条文", "合规依据", "法条"], LR, "get_legal_regulation_search", "keyword"),
    e("类案检索", ["类案", "判例", "同类案例", "类案参考"], LC, "get_judicial_case_search", "keyword"),
    e("立案检索", ["立案检索", "跨主体立案", "案号检索", "按案号查", "类案立案"], LC, "get_judicial_case_search", "keyword",
                 cost="跨主体按案由/关键词检索用 get_judicial_case_search；以企业为锚的立案量用 qcc-risk 的 get_case_filing_info，两者不合并调用"),
    e("招投标检索", ["标讯检索", "招标公告", "找招标", "搜索标书", "按关键词找中标"], TD, "search_tenders", "keyword"),
    e("远端文档解析", ["解析在线文档", "url解析", "链接解析", "远程文档"], DO, "parse_document", "url",
                 gated="remote_url"),
    e("远端解析结果", ["远端解析结果", "文档解析结果"], DO, "get_parse_result", "url", gated="remote_url"),
    e("本地文档解析", ["本地文件解析", "解析pdf", "解析财报", "本地文档", "文件解析"], DM, "parse_document",
                 "file_path", gated="local_stdio"),
    e("本地解析结果", ["本地解析结果"], DM, "get_parse_result", "file_path", gated="local_stdio"),
]


def entry(f, extra):
    d = {"field": f["field"], "tags": f["tags"], "server": f["server"], "tool": f["tool"], "anchor": f["anchor"]}
    if "gated" in f:
        d["gated"] = f["gated"]
    if "aggregate" in f:
        d["aggregate"] = f["aggregate"]
    if "truncatable" in f:
        d["truncatable"] = f["truncatable"]
    if "paged_needs_apply" in f:
        d["paged_needs_apply"] = True
    if "history_tool" in f:
        d["history_tool"] = f["history_tool"]
    d.update(extra)
    return d


# ---- cost / save 规则：让索引能自己回答「多省了一次调用」 ----
RISK_DIMS = [f["tool"] for f in FIELDS if f["server"] == R and not f.get("aggregate")]
EXC_DIMS = [f["tool"] for f in FIELDS if f["server"] == X and f["tool"] != "get_executive_risk_scan"
            and f["tool"] != "get_executive_related_risk_scan"]
HIST_PAIRS = [f["tool"] for f in FIELDS if f.get("history_tool")]

FIELDS = [entry(f, {"cost": dict(save=RISK_DIMS[:], via=getattr(f, "x", None))}) if False else entry(f, {}) for f in FIELDS]

# annotate fields that pair current + historical snapshot
for f in FIELDS:
    if "history_tool" in f:
        f["cost"] = ("现状 + 历史各 1 次调用；只要现状快照就略过 history_tool（省 1 次调用，"
                     "且 qcc-history 需企业实名认证）")

# annotate risk detail fields with the triage shortcut
for f in FIELDS:
    if f["server"] == R and not f.get("aggregate") and "cost" not in f:
        f["cost"] = "仅在 get_company_risk_scan 对应维度 count > 0 时下钻（count = 0 时省 1 次调用）"
for f in FIELDS:
    if f["server"] == X and f["tool"] not in ("get_executive_risk_scan", "get_executive_related_risk_scan") \
            and "cost" not in f:
        f["cost"] = "仅在 get_executive_risk_scan 对应维度 count > 0 时下钻（count = 0 时省 1 次调用）"

PACKAGES = [
    dict(id="entity_anchor", name="实体锚定（强制前置）", servers=[C],
         tools=["get_company_by_query", "verify_company_accuracy", "get_company_registration_info"],
         cover=["企业搜索", "企业名称核实", "统一社会信用代码"], note="任何计划的 Step 0；品牌名/简称必须先消歧"),
    dict(id="profile_onepager", name="一页纸画像", servers=[C, O, I, R],
         tools=["get_company_registration_info", "get_company_profile", "get_shareholder_info",
                "get_actual_controller", "get_key_personnel", "get_financial_data", "get_annual_reports",
                "get_bidding_info", "get_recruitment_info", "get_news_sentiment", "get_financing_records",
                "get_credit_evaluation", "get_patent_info", "get_trademark_info",
                "get_software_copyright_info", "get_internet_service_info", "get_ipr_pledge",
                "get_company_risk_scan"],
         cover=["企业简介", "股东信息", "实际控制人", "主要人员", "财务数据", "年度信息", "招投标", "招聘信息",
                "新闻舆情", "融资历程", "信用评级", "专利", "商标", "软件著作权", "网络服务备案", "知产质押",
                "风险扫描"],
         note="风险只到分诊层；需要明细时对 count>0 的维度按 risk_drill 下钻"),
    dict(id="risk_drill", name="尽调风控深挖（先扫后钻）", servers=[R],
         tools=["get_company_risk_scan"] + RISK_DIMS,
         cover=["风险扫描"] + [f["field"] for f in FIELDS if f["server"] == R and not f.get("aggregate")],
         note="扫描工具必须第一个调用；只对命中维度下钻。禁止跳过扫描逐个调用明细"),
    dict(id="history_whitewash", name="历史存档洗白识别", servers=[H],
         tools=[f["tool"] for f in FIELDS if f["server"] == H],
         cover=[f["field"] for f in FIELDS if f["server"] == H], gated="enterprise_auth",
         note="未开通时跳过；风险分级上限降至 C"),
    dict(id="related_graph", name="法人关联公司图谱", servers=[C, X, H, R],
         tools=["get_shareholder_info", "get_actual_controller", "get_external_investments", "get_branches",
                "get_change_records", "get_key_personnel", "get_executive_legal_rep_roles",
                "get_executive_investments", "get_executive_related_companies", "get_executive_historical_partners",
                "get_historical_shareholders", "get_historical_legal_rep", "get_historical_executives",
                "get_historical_investments", "get_company_related_risk_scan"],
         cover=["股东信息", "实际控制人", "对外投资", "分支机构", "工商变更记录", "主要人员", "法代任职企业",
                "高管投资任职", "关联企业", "历任合伙人", "历史股东", "历届法定代表人", "历任高管",
                "历史对外投资", "关联企业风险"],
         gated="enterprise_auth",
         note="Step 3 需人名双锚；qcc-history 未开通时降级为企业-企业单部图"),
    dict(id="executive_profile", name="董监高个人风险画像", servers=[X, R],
         tools=["get_executive_risk_scan", "get_executive_related_risk_scan", "get_executive_dishonest",
                "get_executive_judgment_debtor", "get_executive_high_consumption_ban",
                "get_executive_exit_restriction", "get_executive_controlled_companies",
                "get_executive_positions", "get_executive_judicial_docs",
                "get_company_risk_scan", "get_company_related_risk_scan"],
         cover=["高管职务", "控制企业", "关联企业", "高管个人风险扫描", "高管关联风险扫描", "高管失信",
                "高管被执行", "高管限高", "高管限出境", "高管裁判文书", "关联企业风险"],
         gated="person_name",
         note="必须先有企业全称 + 人名双锚；人名从 get_key_personnel / get_actual_controller 取"),
    dict(id="cross_entity", name="跨主体检索", servers=[LR, LC, TD],
         tools=["get_legal_regulation_search", "get_judicial_case_search", "get_case_filing_info",
                "search_tenders"],
         cover=["法规检索", "类案检索", "立案检索", "招投标检索"],
         note="以主题/案由为锚，非企业为锚；官方 27 个 SKILL 未直接引用这 4 个 server，可独立调用"),
    dict(id="document_parse", name="长文档解析", servers=[DO, DM],
         tools=["parse_document", "get_parse_result"],
         cover=["远端文档解析", "远端解析结果", "本地文档解析", "本地解析结果"],
         note="远端需公网 URL；本机文件走 stdio，需 Node 20+"),
]

OFFICIAL_SKILLS = [
    ("banking", "银行·合规风控"), ("invest", "投资·尽调"), ("legal", "法律·法务"), ("supply", "采购·供应链"),
]

doc = {
    "schema": "qcc-field-index/v1",
    "skill": "qcc-company",
    "skill_version": "2.0.0",
    "generated": "2026-09-16",
    "snapshot_source": "https://agent.qcc.com/skill/v1/{banking,invest,legal,supply}/README.md · https://agent.qcc.com/skill/QCC-MCP-TERMINOLOGY.md · 12 个官方 SKILL.md 实测抓取",
    "_readme": {
        "what": "字段 → MCP Server → 工具 的路由索引。目标：一段自然语言查询，覆盖最多的有效字段，消耗最少的 MCP 调用（积分）。",
        "tables": ["entity_anchor", "servers", "fields", "packages", "conflicts", "cost_rules", "ontology_hooks"],
        "routing_algorithm": [
            "1. 从自然语言提取字段关键词，在 fields[].tags 里匹配（大小写与中英文变体都在 tags 内，不做模糊归一）。",
            "2. 每个命中的 field 取 canonical tool。若 field 带 history_tool 且用户明确要历史维度，追加该历史工具（两次调用覆盖现状+历史两层，省得单独找历史工具名）。",
            "3. 按 server 聚合选中的工具形成调用计划。同 server 内多次 tools/call 共享一次 initialize 握手与 Mcp-Session-Id，聚合能同时省握手开销与积分。",
            "4. 若字段集被某个 package.cover 完全覆盖且该 package.servers 数不大于手动聚合结果 → 走 package，省掉规划开销。",
            "5. 若字段集只是某个 package 的子集 → 不走 package，手动聚合更省。这是最常见情形，也是本索引的核心价值。",
            "6. 计划第一步永远是 entity_anchor 里的锚定工具，先拿到 18 位统一社会信用代码；名称微差必须停下纠错，不得带错误主体继续调用。",
            "7. 风险类字段命中 ≥2 个维度时，先调用 aggregate=true 的扫描工具一次分诊，只对 count>0 的维度下钻；count=0 的维度逐字引用强语义负向结论，不再调用原子工具。",
            "8. 任何工具的返回若带 摘要/提示 字段，按 truncatable=true 处理：引用摘要原文、注明「已展示前 N 条 / 累计 M 条」，不把子集当全量。",
        ],
        "field_entry_fields": {
            "field": "业务字段名（用户会这么说的话）",
            "tags": "同义词/缩写/变体，Route 阶段做关键词匹配用",
            "server": "所属 MCP Server",
            "tool": "权威工具名（canonical）",
            "history_tool": "可选。历史存档维度工具（qcc-history 独有）",
            "anchor": "entity=企业全称或18位信用代码 · entity+person=企业全称+董监高姓名双锚 · keyword=主题关键词跨主体检索 · url=公网文档链接 · file_path=本机文件路径",
            "gated": "可选。person_name=需先取到人名 · enterprise_auth=需企业实名认证 · remote_url=需公网 URL · local_stdio=需 Node 20+ 本机",
            "aggregate": "可选。true=聚合分诊工具，一次返回多维度命中计数，不含明细",
            "truncatable": "可选。true=返回走上下文脱水（摘要+提示），拿到的是截断子集",
            "paged_needs_apply": "可选。true=翻页能力需向平台申请开通，默认只返回前 30 条",
            "cost": "可选。该字段的省调用策略说明",
        },
        "cost_rules": {
            "credits": "注册赠 500 积分（30 天）；每日赠 普通 100 / VIP 150 / SVIP 200（当日有效）；充值 1 元 = 10 积分。",
            "monthly_cap": "同主体自然月积分封顶：个体工商户 20 / 企业 100 / 董监高人员 100。批量图谱铺点必须先单层扫描再定点下钻，否则封顶被打爆后当主体当月全部不可用。",
            "top_savers": [
                "get_company_risk_scan 一次分诊替代 32 个原子风险工具 → 未命中维度每维省 1 次调用",
                "get_executive_risk_scan 一次分诊替代 30 个个人风险工具 → 同上",
                "field.history_tool 内联 → 省一次字段名查找，且历史维度只在需要时多花 1 次调用",
                "year 参数留空取全量 → 避免逐年循环（实测曾逐年调用到 1976，单次运行 60+ 次冗余）",
                "同 server 工具合并到一次会话 → 省 initialize 握手",
                "官方 SKILL.md 已安装则本地读 → 省重复 fetch",
            ],
            "forbidden": [
                "跳过扫描直接散弹枪调用原子风险工具",
                "关联风险扫描后自动逐个下钻每个关联企业（单层预警，只对「有风险关联方」单点下钻）",
                "带 year 参数逐年循环调用同一工具",
                "对 count=0 的维度继续调用原子工具求证",
                "用推测值代替 anchor（如猜人名、猜信用代码）",
            ],
        },
        "verification_note": "servers.tools 共 129 个工具位 / 127 个唯一工具名（parse_document 与 get_parse_result 在 qcc-document 与 qcc-document-mcp 各占一位）。company/risk/ipr/operation/history/executive 六层来自官方 SKILL.md 与 QCC-MCP-TERMINOLOGY.md 实测，legal-regulation/legal-case/tender/document 四层来自官方 CLI 命令实测。官方未承诺工具名长期稳定，上线前建议跑一次远端 tools/list 校准。fields 引用的每个 tool 都出现在 servers.tools 内。",
    },
    "entity_anchor": {
        "primary_key": "18 位统一社会信用代码",
        "tools": ["get_company_by_query", "verify_company_accuracy", "get_company_registration_info"],
        "rule": "业务工具只认真正的企业全称或 18 位统一社会信用代码。品牌名/简称先消歧；全称有微差时停下纠错。锚定结果（全称+信用代码+登记状态）写入上下文，后续全部调用复用同一主键。",
    },
    "servers": {},
    "fields": FIELDS,
    "packages": PACKAGES,
    "conflicts": [
        dict(tool="get_case_filing_info",
             variants=[{"server": R, "anchor": "entity", "meaning": "以企业为锚的立案信息（立案量 / 案由分布 / 最近立案日期）"}],
             related=[{"server": LC, "tool": "get_judicial_case_search", "anchor": "keyword",
                       "meaning": "跨主体类案检索，按案由或关键词"}],
             rule="不是跨 Server 重名，而是语义相邻、禁止混用：查「这家公司的立案量」用 qcc-risk.get_case_filing_info，查「某案由有哪些案例」用 qcc-legal-case.get_judicial_case_search"),
        dict(tool="parse_document",
             variants=[{"server": DO, "anchor": "url", "meaning": "远端解析公网 URL"},
                       {"server": DM, "anchor": "file_path", "meaning": "本地 stdio 解析本机文件"}],
             rule="按输入形态选择：有 URL 走 qcc-document，有本机文件走 qcc-document-mcp"),
        dict(tool="get_parse_result",
             variants=[{"server": DO, "meaning": "远端解析结果"}, {"server": DM, "meaning": "本地解析结果"}],
             rule="与 parse_document 成对使用，Task ID 由对应的 parse_document 返回"),
        dict(tool="get_change_records",
             variants=[{"server": C, "meaning": "变更事件流"},
                       {"server": H, "meaning": "get_historical_* 分维度历史存档"}],
             rule="语义重叠但不等价，不可互相替代"),
        dict(tool="search_tenders",
             variants=[{"server": TD, "anchor": "keyword", "meaning": "全国标讯检索"},
                       {"server": O, "tool": "get_bidding_info", "meaning": "以企业为锚的招投标记录"}],
             rule="查「这家公司中标了什么」用 qcc-operation.get_bidding_info；查「某主题有哪些标」用 qcc-tender.search_tenders"),
    ],
    "ontology_hooks": {
        "purpose": "取数之后用本体论推理挖风险。下面只定义关系与推理入口，规则细节见 SKILL.md 第六节。",
        "entities": {
            "Enterprise": ["entity_id(=18位统一社会信用代码)", "full_name", "registration_status", "legal_rep",
                           "actual_controller", "industry", "source_server"],
            "Person": ["person_name", "role(法代/董事/监事/高管/股东/实控人)", "source_server"],
        },
        "relations": {
            "持股": {"from": "Enterprise|Person", "to": "Enterprise", "weight": "持股比例",
                     "tools": ["get_shareholder_info", "get_historical_shareholders"]},
            "控制": {"from": "Person|Enterprise", "to": "Enterprise", "weight": "穿透层级",
                     "tools": ["get_actual_controller", "get_executive_controlled_companies"]},
            "任职": {"from": "Person", "to": "Enterprise", "weight": "职务",
                     "tools": ["get_key_personnel", "get_executive_positions", "get_historical_executives"]},
            "同一法代": {"from": "Person", "to": "Enterprise", "weight": "任职区间",
                         "tools": ["get_executive_legal_rep_roles", "get_historical_legal_rep"]},
            "共同股东": {"from": "Person|Enterprise", "to": "Enterprise", "weight": "共同持股比例",
                         "tools": ["get_shareholder_info", "get_historical_shareholders"]},
            "投资": {"from": "Enterprise", "to": "Enterprise", "weight": "持股比例",
                     "tools": ["get_external_investments", "get_historical_investments"]},
            "合伙": {"from": "Person", "to": "Person|Enterprise", "weight": "任职区间",
                     "tools": ["get_executive_historical_partners"]},
            "分支": {"from": "Enterprise", "to": "Enterprise", "weight": None,
                     "tools": ["get_branches"]},
            "担保": {"from": "Enterprise", "to": "Enterprise", "weight": "担保金额",
                     "tools": ["get_guarantee_info", "get_equity_pledge_info", "get_stock_pledge_info"]},
            "资产限制": {"from": "Enterprise", "to": "Asset", "weight": "冻结/质押份额",
                         "tools": ["get_equity_freeze", "get_chattel_mortgage_info", "get_land_mortgage_info",
                                   "get_judicial_auction"]},
        },
        "risk_inference": [
            dict(rule="关联破产传导", trigger="实控人控制的多家企业中任一家处于破产重整/清算",
                 evidence_fields=["实际控制人", "控制企业", "破产重整", "清算信息"], effect="关联企业风险 +0.3×控制权比例"),
            dict(rule="壳公司识别", trigger="法代变更≥3次/2年 且 注册地址变更≥2次 且 参保人数=0",
                 evidence_fields=["历届法定代表人", "工商变更记录", "年度信息"], effect="风险等级 → D"),
            dict(rule="风险出清（洗白）", trigger="当前视图干净但历史有失信/被执行/经营异常，且集中在最近 12~24 个月",
                 evidence_fields=["失信被执行人", "被执行人", "经营异常", "历史工商登记"],
                 effect="标注疑似风险出清，需关注时效；分级上限 C"),
            dict(rule="股东风险传导", trigger="持股股东存在失信/被执行/限高",
                 evidence_fields=["股东信息", "失信被执行人", "被执行人", "限制高消费"],
                 effect="风险传导概率 × 持股比例"),
            dict(rule="系统性风险主体", trigger="同一自然人控制的 N 家企业中 M 家存在终局风险",
                 evidence_fields=["控制企业", "关联企业", "高管个人风险扫描"],
                 effect="判定为系统性风险主体，禁止只评估单家"),
            dict(rule="资不抵债", trigger="资产负债率 > 100%", evidence_fields=["资产负债率"], effect="风险等级 → C 及以上"),
            dict(rule="知产腾挪", trigger="商标/专利集中过户或知产质押",
                 evidence_fields=["商标", "专利", "知产质押", "历史商标"], effect="关注资产腾挪与关联交易"),
            dict(rule="经营断崖", trigger="招聘规模骤降 或 舆情集中负面 或 资质过期",
                 evidence_fields=["招聘信息", "新闻舆情", "资质证书"], effect="经营活力降级"),
            dict(rule="资质失效", trigger="行政许可/资质证书过期或被吊销",
                 evidence_fields=["行政许可", "资质证书"], effect="准入资格失效，一票否决"),
        ],
        "edge_fields": ["source", "target", "relation_type", "weight", "period_start", "period_end",
                        "is_historical", "evidence_tool(=server.tool)", "evidence_date",
                        "risk_flags", "risk_count", "risk_detail_drilled", "negative_conclusion",
                        "summary_quote", "truncation_hint"],
    },
    "official_skills": {
        "note": "官方 4 客群 27 个 SKILL 由「业务规则 + MCP 工具清单 + 报告输出格式」构成，负责行业报告模板；本索引负责取数路由。需要模板时 fetch 官方 URL，禁止自行翻译猜测 SKILL ID。",
        "groups": {g: dict(label=label,
                           readme="https://agent.qcc.com/skill/v1/{}/README.md".format(g),
                           skill_url="https://agent.qcc.com/skill/v1/{}/{}/SKILL.md".format(g, "<skill-id>"))
                   for g, label in OFFICIAL_SKILLS},
        "full_list": {
            "banking": ["kyb-verification-qcc", "litigation-analysis-qcc", "credit-due-diligence-qcc",
                        "counterparty-risk-qcc", "ubo-screening-qcc", "trade-finance-compliance-qcc",
                        "credit-monitoring-qcc", "equity-structure-qcc", "executive-background-qcc",
                        "business-health-scan-qcc", "guarantor-check-qcc", "bankruptcy-monitor-qcc"],
            "invest": ["ic-memo-qcc", "history-evolution-qcc", "strip-profile-qcc", "competitor-analysis-qcc",
                       "fundraising-tracker-qcc", "ip-asset-inventory-qcc"],
            "legal": ["contract-review", "contract-party-check-qcc", "labor-compliance",
                      "ip-infringement-alert", "debt-recovery-assessment-qcc", "license-validation"],
            "supply": ["vendor-assess", "supplier-annual-check-qcc", "new-supplier-screening"],
        },
        "unreferenced_servers": ["qcc-legal-regulation", "qcc-legal-case", "qcc-tender", "qcc-document"],
    },
}

for s in SERVERS:
    tools = [f["tool"] for f in FIELDS if f["server"] == s["server"]]
    extra = []
    if s["server"] == C:
        extra = []  # all covered by fields
    doc["servers"][s["server"]] = {
        "name": s["name"],
        "url": s.get("url"),
        "command": s.get("command"),
        "type": s["type"],
        "domain": s["domain"],
        "headers": None if s["type"] == "stdio" else {"Authorization": "Bearer YOUR_API_KEY"},
        "env": {"QCC_DOCUMENT_AUTHORIZATION": "Bearer YOUR_API_KEY"} if s["type"] == "stdio" else None,
        "gated": s["gated"],
        "paged": s["paged"],
        "role": s["role"],
        "tool_count": len(dict.fromkeys(tools)),
        "tools": list(dict.fromkeys(tools)),
    }

# ---- verify ----
all_tools = {t for sid, d in doc["servers"].items() for t in d["tools"]}
missing = [(f["field"], f["tool"]) for f in FIELDS if f["tool"] not in all_tools]
assert not missing, missing
assert all(all(t in all_tools for t in p["tools"]) for p in PACKAGES)

json.dump(doc, OUT.open("w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("fields:", len(FIELDS), "| unique tools:", len(all_tools),
      "| servers:", len(doc["servers"]), "| packages:", len(PACKAGES))
for sid, d in doc["servers"].items():
    print(" ", sid, d["tool_count"])
