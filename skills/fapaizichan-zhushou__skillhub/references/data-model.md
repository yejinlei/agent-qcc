# 司法拍卖房源数据模型 Auction Listing Schema

跨源归一的唯一标准。所有法拍站点抓到的数据，必须先映射到这个模型，才能进入评分与报告。

**铁律**：任何字段拿不到就填 `null`，并在 `missing_fields[]` 里登记。**禁止用模型知识猜测或补全。**

本技能统一处理三类**拍卖 / 资产处置**标的（2026-09-03 用户调整）：
- **法拍**：司法拍卖（阿里法拍 / 公拍网 / 联交所司法拍卖服务中心）
- **国资**：国有资产处置 / 转让（重庆联交所、公拍网国资板块等）
- **社会拍卖**：社会委托 / 资产拍卖（非司法、非国有的公开拍卖）

普通二手房（贝壳/安居客/58/链家）**按用户要求不做交叉验证、不接入**——本技能只收拍卖/资产处置类标的。

---

## 1. 顶层结构

```json
{
  "demand_id": "bj-haidian-3room-600w-20260903",
  "generated_at": "2026-09-03T10:00:00+08:00",
  "demand_echo": "北京海淀，三居，80-120㎡，起拍总价 600 万以内，2005 年后，有小学学区",
  "assumptions": ["未指定楼层，默认中楼层优先", "未指定朝向，默认南北优先"],
  "listings": [ "……Listing 对象数组……" ],
  "stats": {
    "total_found": 42,
    "after_dedup": 28,
    "after_hard_filter": 15,
    "reported": 10,
    "by_source": {"sf.taobao.com": 9, "gpai.net": 4, "cquae.com": 2}
  },
  "failures": [
    {"site": "example.gov.cn", "reason": "robots.txt 全站 Disallow，已转人工取证", "action": "引导用户浏览器自取公告原文"}
  ]
}
```

---

## 2. Listing 对象

### 2.1 通用字段（所有法拍标的必填）

| 字段 | 类型 | 单位/格式 | 说明 |
|------|------|----------|------|
| `id` | string | - | 本技能生成的内部 ID（来源缩写 + 原站 ID） |
| `source` | enum | `sf.taobao.com` / `gpai.net` / `cquae.com`（重庆联交所）/ `caa123.org.cn`（中拍平台）/ `other` | 来源站点 |
| `listing_type` | enum | `法拍` / `国资` / `社会拍卖` | 房源类型：`法拍`=司法拍卖；`国资`=国有资产处置/转让；`社会拍卖`=社会委托/资产拍卖（非司法、非国资） |
| `detail_url` | string | URL | **原站详情页链接，必须是抓取所得的真实 URL** |
| `title` | string | - | 原站标题原文 |
| `city` | string | - | |
| `district` | string | - | 行政区 |
| `sub_district` | string | null | 板块/街道 |
| `community_name` | string | - | 小区名（法拍填楼盘/标的物名称） |
| `address` | string | null | 详细地址（法拍常只到小区，允许为 null） |
| `crawled_at` | string | ISO8601 | 抓取时间 |
| `price_total_wan` | number | 万元 | 法拍=起拍总价（见下，需区分起拍/评估/成交） |
| `area_sqm` | number | ㎡ | 建筑面积 |
| `layout` | string | `3室2厅1卫` | 户型（常为 null，法拍公告未必写明） |
| `rooms` | int | null | 室数，从 layout 解析（无则 null） |
| `floor_info` | string | `中楼层/共18层` | 原文字段（常为 null） |
| `floor_level` | enum | `低/中/高/顶层/底层/unknown` | 归一化 |
| `total_floors` | int | null | |
| `orientation` | string | `南 北` | 原文字段（常为 null） |
| `build_year` | int | null | 建成年份 |
| `property_type` | enum | `住宅/公寓/别墅/商住/办公/商铺/unknown` | |
| `decoration` | enum | `精装/简装/毛坯/豪装/unknown` | |
| `has_elevator` | bool | null | |
| `property_right` | string | null | 商品房/已购公房/经济适用房/限价房 等 |
| `tags` | string[] | - | 原站标签（近地铁、南北通透、学区房 等） |
| `description` | string | null | 房源描述原文（**清洗掉联系方式后**） |
| `images` | Image[] | - | 图片数组，见 2.2 |
| `layout_images` | Image[] | - | **户型图**（独立数组，重点交付物；法拍下多来自附件 PDF） |
| `metro_info` | object | null | `{lines:[], station:"", walk_min:int}` |
| `school_info` | object | null | `{level:"小学", names:[], note:""}` |
| `missing_fields` | string[] | - | 未获取到的关键字段名 |

> 注：法拍标的的 `price_total_wan` 字段语义为**起拍价**；评估价/成交价放在 `auction.*` 子对象里，二者不要混淆。

### 2.2 Image 对象

```json
{
  "url": "https://sf-item.taobao.com/xxx.jpg",
  "local_path": "assets/理想嘉业-50号楼-01.jpg",
  "type": "户型图 | 室内实拍 | 小区环境 | 周边配套 | 公告附件",
  "is_layout_plan": true,
  "has_watermark": true,
  "crawled_at": "2026-09-03T10:00:00+08:00"
}
```

**户型图判定启发式**（命中任一即标记为户型图）：
- URL 或 alt 含：`huxing` / `layout` / `floorplan` / `户型` / `pingmian`
- 位于页面"户型图"区块内（父容器 class/id 含上述关键词）
- 图片为线稿风格（边长比接近 1:1 且色彩数低）—— 兜底手段，准确率一般
- 法拍场景下，评估报告 PDF 内页截图/提取图优先列入 `layout_images`

---

## 3. 法拍补充字段（核心）

| 字段 | 说明 |
|------|------|
| `auction.court` | 处置法院 |
| `auction.case_no` | 案件号 |
| `auction.stage_round` | **拍次**：一拍 / 二拍 / 三拍 / 变卖 / 重新拍卖（用户要求必标） |
| `auction.status` | **进行状态**：即将开始 / 正在进行中 / 已结束 / 已成交 / 流拍 / 中止 / 撤回（用户要求必标） |
| `auction.is_judicial` | 是否司法拍卖（联交所等混合源用 `serviceOrgName` 含"司法拍卖"判定） |
| `auction.appraisal_price_wan` | 评估价 |
| `auction.start_price_wan` | 起拍价 |
| `auction.current_price_wan` | 当前价（≠ 成交价，延时竞价中会变动） |
| `auction.deposit_wan` | 保证金 |
| `auction.increment_wan` | 加价幅度 |
| `auction.start_time` / `auction.end_time` | 开拍 / 结束时间 |
| `auction.delay_rule` | 延时竞价规则 |
| `auction.clear_house` | 是否负责清场交付 |
| `auction.tax_bearer` | 税费承担方 |
| `auction.occupancy` | 占用情况 |
| `auction.lease` | 租赁情况（有无长期租约） |
| `auction.property_cert_no` | 权证号 —— **仅记录"有/无"，不记录证号原文** |
| `auction.attachments` | 公告/评估报告附件（PDF 中常含户型图） |
| `auction.risk_score` | 风险分，见 legal-auction.md |
| `auction.estimated_total_cost_wan` | 综合成本测算 |

---

## 3.5 国资 / 社会拍卖 补充字段（非司法拍卖标的）

`listing_type` 为 `国资` 或 `社会拍卖` 的标的，复用 §2.1 通用字段；其 `auction` 子对象语义与法拍不同（无法院、无案号），按以下约定填充：

| 字段 | 说明 |
|------|------|
| `auction.is_judicial` | `false`（国资/社会拍卖均非司法拍卖） |
| `auction.court` | `null`（无处置法院） |
| `auction.service_org` | 服务机构 / 拍卖行（联交所 `serviceOrgName` 或页面"委托方/拍卖机构"） |
| `auction.entrust_party` | 委托方 / 转让方（国资填国资主体，社会拍卖填产权人） |
| `auction.stage_round` | `null`（一拍/二拍仅司法拍卖有；国资/社会拍卖用 `status` 表达阶段） |
| `auction.status` | **进行状态**：挂牌中 / 竞价中 / 已成交 / 已流标 / 已结束（用户要求必标，方便读取） |
| `auction.start_price_wan` | 挂牌价 / 起拍价（万元） |
| `auction.appraisal_price_wan` | 评估价（若有） |
| `auction.deposit_wan` | 保证金 |
| `auction.start_time` / `auction.end_time` | 挂牌起止 / 竞价起止时间 |
| `auction.asset_type` | 资产类型：房产 / 商铺 / 车位 / 车辆 / 设备 / 股权 / 债权 / 其他 |
| `auction.occupancy` / `auction.lease` | 占用 / 租赁情况（国资房产常带租约，需提示） |

> 国资/社会拍卖同样遵守 R6（不碰个人信息）、R8（零编造）。委托人/买受人姓名电话一律不入库。

---

## 3.6 类型判定规则（代码与本文档一致）

- **法拍**：服务机构名 / 标题含"司法拍卖"（联交所）、或页面含"处置法院"（公拍网）。
- **国资**：服务机构名 / 标题含"国资 / 国有 / 资产处置 / 产权交易 / 公共资源 / 央企"等。
- **社会拍卖**：其余非司法、非国资的公开拍卖标的。
- 命中不了就留 `null`（禁止编造）；阶段标记缺失同理。

---

## 4. 去重与真实性规则

### 4.1 去重指纹
```python
fingerprint = sha1(f"{city}|{community_name}|{area_sqm}|{layout}|{auction.case_no}|{auction.court}")
```
- 同指纹 → 保留信息完整度最高的那条，其余记入 `duplicates[]`。
- **跨平台同房源**：标的名称 + 法院 + 案号 + 面积 一致 → 判为同一套，合并各平台附件与出价信息。

### 4.2 真实性校验（法拍）
| 规则 | 标记 |
|------|------|
| 起拍价 > 评估价 | `⚠️ 数据异常，需人工复核` |
| 关键信息（法院/起拍价/面积）缺失 ≥ 3 项 | `⚠️ 公告信息过简，需线下核实` |
| 同一标的在不同平台案号/面积不一致 | `⚠️ 跨平台信息冲突，需核对` |
| 附件 URL 无法下载或返回非 PDF/图片 | `⚠️ 附件缺失` |

被标记的标的**不直接淘汰**，但评分按 legal-auction.md 风险表扣减，并在报告中显式标注。

---

## 5. 匹配度评分（默认权重）

```json
{
  "weights": {
    "price_fit": 25,
    "area_layout_fit": 20,
    "commute": 20,
    "school_amenity": 10,
    "age_floor_orientation": 10,
    "authenticity": 10,
    "source_credibility": 5
  },
    "source_credibility_score": {
    "sf.taobao.com": 5,
    "gpai.net": 4.5,
    "cquae.com": 4,
    "caa123.org.cn": 4,
    "other": 3
  },
  "auction_adjust": {
    "discount_bonus_max": 15,
    "penalty_no_clear_house": -15,
    "penalty_buyer_all_tax": -10,
    "penalty_long_lease": -15,
    "penalty_title_defect": -20
  }
}
```

**硬门槛**（任一不满足即淘汰，不进排序）：
城市 · 起拍总价上限 · 面积下限 · 户型室数 · 法拍开拍时间窗口（未开拍/已结束均不进推荐）

---

## 6. 输出文件约定

```
outputs/property-{YYYYMMDD}-{需求关键词}/
├── report.md          # 人读报告
├── listings.json      # 结构化数据（本 Schema）
├── demand.json        # 需求卡片
├── assets/            # 户型图、实拍图、法拍公告附件
│   └── manifest.json  # 图片清单（URL / 本地路径 / 类型 / 时间）
└── raw/               # 原始 HTML 快照（保留 7 天）
```

报告末尾必须附 **数据可信度声明**：
- 各渠道命中数与失败原因（含 robots 受限转人工取证的说明）
- `missing_fields` 汇总
- 数据时效提醒（>7 天需复核）
- 明确写出"以下信息未获取，需线下或登录核实"的清单
