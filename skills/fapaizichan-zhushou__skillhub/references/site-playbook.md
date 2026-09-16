# 法拍站点手册 Portal Playbook · 司法拍卖版

---

## ⛔ robots 合规矩阵（2026-09-03 实测 · 必读 · 决定渠道优先级）

**以下为对各法拍源 `robots.txt` 的真实抓取结果，不是推测。渠道优先级由这张表决定。**

| 站点 | robots.txt (`User-agent: *`) | 结论 | 处置 |
|------|------------------------------|------|------|
| **阿里法拍** `sf.taobao.com` | `Allow: /` | ✅ 全站允许 | **法拍自动抓取首选** |
| **公拍网** `gpai.net` | 禁 `/upload/`、`/tb/search.do`、`/sf/search.do`、`/sf/Search.do`、`/sf/noticeDetail.do`、`/api/`、`/bm/` 等 | ✅ **详情页 `sf/item2.do` 与首页 `/sf/` 未禁，可静态抓** | **自动抓取第二源** |
| **重庆联交所** `cquae.com` | 静态 977B SPA 空壳，但**后端有公开 JSON API（无需登录）** | ⚠️ 未声明静态规则 | ✅ **JSON API 直抓（浏览器非必需）**：`/api/trade/project/projectPageQuery` + `projectDetailQuery?id=` |
| **中拍平台** `caa123.org.cn` | 反调试 JS 挑战页 | ⚠️ 未声明 | 浏览器渲染，提取待验证 |
| **北京产权交易所** `cbex.com.cn` | JS 挑战（521） | ⚠️ 受限 | 浏览器渲染，提取待验证 |
| **工商银行融e购** `mall.icbc.com.cn` | 502 | ⚠️ 受限 | 暂不集成 |
| **上海联合产权交易所** `suaee.com` | 小壳页（SPA） | ⚠️ 未声明 | 浏览器渲染，提取待验证 |

> 注：**最高人民法院入围的七大网络司法拍卖平台**为：阿里拍卖、京东（按用户要求剔除）、
> 公拍网、中国拍卖行业协会网（中拍平台）、工商银行融e购、北京产权交易所等。
> 本技能据此集成阿里法拍 + 公拍网 + 重庆联交所等扩展源。

> 这张表会随站方调整而变。每次技能运行 `fetch_page.py` 都会重新拉取并缓存 robots（24h TTL），
> 若发现与下表不符，以**实时抓取结果为准**并回来更新本表。

---

> **版本提示**：法拍站改版频繁，本手册记录的是"截至编写时"的结构与经验值。
> 每次执行前必须跑探针验证；失效后按 SKILL.md §5 逆向修正，并把结果写进文末「实测修正区」。

---

## 0. 通用：城市域名规则

| 站点 | 域名模式 | 示例 |
|------|---------|------|
| 阿里法拍 | `sf.taobao.com` | 列表 `/item_list.htm`，详情 `/sf_item/{id}.htm` |
| 公拍网 | `www.gpai.net` | 司法 `/sf/`，详情 `/sf/item2.do?Web_Item_ID={id}` |
| 重庆联交所 | `www.cquae.com` | SPA，项目中心 `/#/project` 等前端路由 |
| 中拍平台 | `sf.caa123.org.cn` | 司法拍卖子域 |
| 北京产权交易所 | `www.cbex.com.cn` | 司法拍卖频道 |

---

## 1. 阿里司法拍卖 sf.taobao.com ⭐ 法拍自动首选

**数据质量**：★★★★★ | **反爬强度**：★★★★★（淘宝系最强） | **robots 状态**：✅ `Allow: /` 全站允许

### 关键 URL
```
列表   https://sf.taobao.com/item_list.htm?category=50025969&city={城市代码}&page={n}
详情   https://sf.taobao.com/sf_item/{itemId}.htm
```

### ✅ 2026-09-03 实测：静态抓取可行，数据结构已破解

`https://sf.taobao.com/item_list.htm` 静态请求返回 **HTTP 200 / 265KB / 60 条拍品数据**，无需浏览器渲染即可拿到完整结构化数据。

**数据位置**（不是 `__INITIAL_STATE__`）：
```html
<script id="sf-item-list-data" type="text/json"> {"data":[ {...}, ... ]} </script>
```

**已验证字段**（`data[]` 每一项）：`id` / `itemUrl` / `title` / `picUrl` / `initialPrice`(起拍价) / `currentPrice`(当前价) / `consultPrice`(评估价) / `marketPrice` / `status` / `start`/`end`(毫秒时间戳) / `bidCount` / `applyCount` / `viewerCount` / `delayCount` / `supportLoans` / `buyRestrictions` / `sellOff`。

`extract_page.py --site ali-auction` 已内置映射，并自动把毫秒时间戳转 ISO8601、把 `//` 开头的 URL 补全为 `https:`。

**已验证筛选参数**：
- `?category=50025969` → 房产类目（房产占比从 24/60 提升到 53/60）
- `?page=N` → 分页，每页 60 条，可用

**⚠️ 未解决：城市 / 关键词筛选** —— `city=`/`q=` 拼接返回 0 条（依赖 JS 异步）。兜底：抓 `?category=50025969` 多页，在标题上做本地城市/区域过滤（标题含完整行政区划与街道地址）。

### 坑
- 详情页常有登录墙，未登录时价格/出价记录多为空白。
- 遇到滑块：**停，交人工**。不要尝试任何绕过。
- 公告与评估报告为附件（PDF/图片），户型图在附件里；详情页通常不给户型图。

---

## 2. 公拍网 gpai.net ⭐ 法拍自动第二源（2026-09-03 新验证）

**数据质量**：★★★★☆ | **反爬强度**：★★★☆☆ | **robots 状态**：✅ 详情页与首页允许（仅禁搜索/接口路径）

### 关键 URL
```
司法首页   https://www.gpai.net/sf/
详情页     https://www.gpai.net/sf/item2.do?Web_Item_ID={id}   ← 服务端渲染静态页，可静态抓取
```

### ✅ 2026-09-03 实测：详情页静态可达 + 正则提取 7/7

`https://www.gpai.net/sf/item2.do?Web_Item_ID=53006` 返回 **HTTP 200 / 113KB**，字段在正文文本里。
robots 禁了 `/sf/search.do`、`/sf/noticeDetail.do` 等，但 **`item2.do` 详情页未被禁**，可静态抓。

**已验证字段**（正则命中）：
| 字段 | 正则命中示例 |
|------|------|
| `title` | `<title>河北省廊坊市香河县宝海道1号理想嘉业小区50号楼2单元0103室房屋 - 拍品详情</title>` |
| `court` | `处置法院：香河县人民法院` |
| `start_price` | `起拍价： 307825 元` |
| `appraisal` | `评估价：439,750 元` |
| `deposit` | `保证金：40,000 元` |
| `increment` | `增价幅度：3000元` |
| `area` | `房产面积： 87.95平方米` |

`extract_page.py --site gpai` 已内置上述正则（数值自动去千分位逗号并转数字）。

### 列表入口策略
公拍网列表多为 JS 加载。可用路径：
1. 从首页 `/sf/` 或公告页里散落的 `item2.do` 链接顺藤摸瓜；
2. 或用户提供具体标的详情页 URL，直接抓详情。
**注意**：`/sf/search.do` 等搜索接口被 robots 禁止，不要走搜索接口。

### 特点
- 上海司法拍卖核心平台，覆盖阿里未覆盖的上海标的。
- 字段文本规整，提取稳定。

---

## 3. 人民法院诉讼资产网 www.rmfysszc.gov.cn（人工取证源）

**数据质量**：★★★★☆（权威） | **robots 状态**：❌ **`User-agent: *` → `Disallow: /` 全站禁止通配 UA**

> ⚠️ **2026-09-03 实测**：robots.txt 对通配 UA 为 `Disallow: /`（全站禁止）。
> 因此**默认不能自动抓取**，必须走「人工取证模式」（用户提供页面 HTML，技能只解析）。
> 解析模板本身已验证 7/7 字段，见下。

### 关键 URL（供用户自行打开后存 HTML）
```
详情   https://www.rmfysszc.gov.cn/statichtml/rm_xmdetail/{id}.shtml   ← 服务端渲染静态页
```

### ✅ 2026-09-03 实测：详情页解析 7/7（在用户提供的 HTML 上）

`/statichtml/rm_xmdetail/{id}.shtml` 是服务端渲染静态 HTML，字段在正文文本中。
`extract_page.py --site rmfysszc` 走 `text_fields` 正则兜底，命中：
`title` / `court` / `start_price`(起拍价) / `appraisal`(评估市场价值) / `deposit`(保证金) / `increment`(增价幅度) / `area`(建筑面积)。

### 获取方式（合规）
- **唯一方式（人工取证）**：用户在自己浏览器打开详情页 → `Ctrl+S` 存 HTML → 交给技能 `extract_page.py --site rmfysszc` 解析。
- ⛔ **自动抓取路径已取消**（2026-09-03 用户要求）：`fetch_page.py` 对该域名硬拦截（退出码 3 `DOMAIN_DENIED`），
  此前的「方式 B：用户授权 `--no-robots` 最小量抓取」已作废，即便用户事后主动要求自动抓取也拒绝并引导人工取证。
  `download_assets.py` 对该域名的公告/评估附件同样跳过下载（manifest 标注"请人工获取"）。

> 这是最高人民法院体系的**权威信息发布渠道**，适合作为交叉验证与"某标的是否真实存在"的核验源。
> 政务站从严：不发起任何程序化请求；用户人工浏览不受影响。

---

## 4. 重庆联合产权交易所（重庆联交所）www.cquae.com（JSON API 直抓档 ✅）

**数据质量**：★★★☆☆ | **反爬强度**：★☆☆☆☆（公开 JSON API，无需登录/无风控） | **robots 状态**：⚠️ 未声明静态规则

> **重大修正（2026-09-03 实测）**：本站虽是 Vue SPA（静态 977B 空壳），但**后端有公开 JSON API 且无需登录**。
> 因此**不需要浏览器**，用纯 Python（urllib）直接打 API 即可，比渲染 SPA 更稳更快。
> 浏览器仅作为"兜底/人工看样"手段，不再是主路径。

### 关键接口（实测可用，无需鉴权）

```
列表（核心入口）：
  GET https://www.cquae.com/api/trade/project/projectPageQuery?page=1&size=20&projectType=31
  → {state:"SUCCESS", target:{result:[...], totalPage, totalClause}}

详情：
  GET https://www.cquae.com/api/trade/project/projectDetailQuery?id=<projectId>
  → 返回单标的完整字段（含公告、附件等）

筛选项元信息（类型/价格/经济类型）：
  GET https://www.cquae.com/api/trade/project/projectIndexQueryParam
```

### 列表请求参数（projectPageQuery）
| 参数 | 取值 | 说明 |
|------|------|------|
| `page` | 1..N | 页码 |
| `size` | 整数 | 每页条数（实测可用 8/20） |
| `projectType` | 见下 | 按类型过滤（**司法拍卖无独立 type，见下文**） |
| `keyword` | 字符串 | ⚠️ 实测被忽略（返回默认列表），**不能**用它筛司法拍卖 |
| `province` / `priceMin` / `priceMax` / `orderBy` | 待验证 | 可选 |

`projectType` 取值（来自 projectIndexQueryParam）：
`1`=产(股)权转让 `2`=企业增资 `31`=房产 `32`=土地 `33`=设备 `34`=车辆 `35`=在建工程 `36`=船舶 `4`=租赁 `5`=意向招商

### ⚠️ 三类标的如何区分（2026-09-03 落地）
本站**没有**"司法拍卖"这个 projectType，三类标的混在 房产/车辆/物权 等类型中，按
**`serviceOrgName`（服务机构）+ 标题** 用 `classify_listing_type()` 判别：
- **法拍**：`serviceOrgName` 含"司法拍卖"（如"重庆联交所集团司法拍卖服务中心"）
- **国资**：`serviceOrgName`/标题含"国资/国有/资产处置/产权交易/公共资源/央企"等
- **社会拍卖**：其余非司法、非国资的公开拍卖（设备/股权/废旧物资等）
**默认全量抓取**（不止法拍）；`--judicial-only` 才只留法拍。推荐做法：
```
1) 拉取多页（projectType=31 房产 / 34 车辆 / 33 设备 等物权类优先）
2) 逐条打 classify_listing_type() 打 listing_type（法拍/国资/社会拍卖）
3) 逐条打 projectDetailQuery?id= 拿完整信息
```
> 注：从网页搜索"司法拍卖"得到的旧路径 `/project/?gy=2&q=s` 现已 404，勿用。

### 列表响应字段映射（target.result[] → Listing）
| API 字段 | 含义 | 映射到 |
|----------|------|--------|
| `noticeName` | 标的名称 | `title` / `community_name` |
| `projectId` | 项目 ID | `id` 前缀 `cquae-` |
| `listingPrice` | 挂牌价（数值，单位见 `priceTypeStr`） | `price_total_wan`（priceTypeStr=万元 时直接用） |
| `priceTypeStr` | 价格单位 | 万元 / 万元/吨 等 |
| `deposit` | 保证金（元） | `auction.deposit_wan`（÷1e4） |
| `listStartDate` / `listEndDate` | 挂牌起止 | `auction.start_time` / `end_time` |
| `projectTag` | 标签数组 | `tags`（如 ["地方国资","房产转让","正式挂牌"]） |
| `serviceOrgName` | 服务机构 | 用于识别司法拍卖（含"司法拍卖"） |
| `sellerName` | 转让方 | `auction.court` 近似（非法院，谨慎标注） |
| `contactName` / `contactTelephone` | 联系人 | 仅人工联系用 |
| `webSiteImg` | 图片 JSON 串 | `images`（需 json.loads） |

### 提取模板（Python，直接调 API）
```python
import json, urllib.request, urllib.parse

def fetch_cquae(page=1, size=20, project_type=31):
    url = ("https://www.cquae.com/api/trade/project/projectPageQuery?"
           + urllib.parse.urlencode({"page": page, "size": size,
                                     "projectType": project_type}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Referer": "https://www.cquae.com/"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    return data["target"]["result"]

def classify(p):
    org = p.get("serviceOrgName") or ""
    title = p.get("noticeName") or ""
    if "司法拍卖" in org:
        return "法拍"
    if any(k in (org + title) for k in ("国资", "国有", "资产处置",
                                         "产权交易", "公共资源", "央企")):
        return "国资"
    return "社会拍卖"

items = fetch_cquae()
# 默认全量；如需仅法拍：judicial = [p for p in items if classify(p) == "法拍"]
typed = [(classify(p), p) for p in items]
# 再逐条 projectDetailQuery?id=<projectId> 取详情
```
> 该模板也已固化进 `scripts/extract_page.py`（站点别名 `cquae` / `重庆联交所`，
> 直接喂保存好的 API JSON 文件即可输出统一 Listing）。

---

## 5. 中拍平台 / 北交所 / 工行 / 上海联交所（扩展档，待验证）

| 站点 | 域名 | 状态（2026-09-03 实测） | 处置 |
|------|------|------------------------|------|
| 中拍平台 | `sf.caa123.org.cn` | 首页反调试 JS 挑战页 | 浏览器渲染，提取待验证 |
| 北京产权交易所 | `www.cbex.com.cn` | JS 挑战（HTTP 521） | 浏览器渲染，提取待验证 |
| 工商银行融e购 | `mall.icbc.com.cn` | HTTP 502 | 暂不集成 |
| 上海联合产权交易所 | `www.suaee.com` | 小壳页（SPA） | 浏览器渲染，提取待验证 |

以上站点列入"扩展档"：需要时走 `agent-browser` 渲染，首次使用必须先 `--probe` 验证提取模板，并把结果写回本表。

---

## 6. 反爬强度速查表

| 站点 | robots 状态 | 强度 | 建议策略 | 单日上限 |
|------|------------|------|---------|---------|
| 阿里法拍 | ✅ Allow: / | 极高 | **静态抓列表即可**，详情/登录墙再上浏览器 | 150 页 |
| 公拍网 | ✅ 详情允许 | 中 | **静态抓 item2.do 详情即可** | 150 页 |
| 诉讼资产网 | ❌ Disallow: / + 黑名单硬拦截 | 低（但政务） | **仅人工取证模式**（自动抓取路径已按用户要求取消，`--no-robots` 无效） | **0 页（不自动抓）** |
| 重庆联交所 | ⚠️ 未声明 | SPA 但有公开 JSON API | **API 直抓**（projectPageQuery + projectDetailQuery，无需登录） | 150 页 |
| 中拍平台 | ⚠️ 未声明 | 反爬挑战 | 浏览器渲染，提取待验证 | 50 页 |
| 北交所 | ⚠️ 受限 | JS 挑战 | 浏览器渲染，提取待验证 | 50 页 |

**通用反爬处理原则**：
1. HTTP 200 ≠ 成功，必须校验页面是否含预期数据节点；
2. 连续 2 次异常 → 该站本次任务直接放弃，不硬刚；
3. 一次任务不要跨站并发，**按站点串行执行**；
4. 所有请求必须经过 `scripts/fetch_page.py`，不要在别处裸发请求。

---

## 7. 实测修正区（重要 · 持续积累）

每次探针失败或改版后，把新映射写在这里。格式：
`| 日期 | 站点 | 页面类型 | 字段/规则 | 旧 → 新 | 原因 |`

| 日期 | 站点 | 页面类型 | 字段/规则 | 旧 → 新 | 原因 |
|------|------|---------|----------|--------|------|
| 2026-09-03 | sf.taobao.com | 全站 | robots | 未知 → `Allow: /` | 实测 |
| 2026-09-03 | gpai.net | 详情页 | robots 范围 | 未知 → 禁 search/noticeDetail，但 `item2.do`/`/sf/` 允许 | 实测 |
| 2026-09-03 | gpai.net | 详情页 | 数据位置/提取 | 未知 → 服务端渲染静态页 `item2.do`，正则 7/7 | 实测破解 |
| 2026-09-03 | rmfysszc.gov.cn | 全站 | robots | `Disallow: /*`（旧认知）→ `User-agent: *` `Disallow: /`（全站禁止） | 实测，须人工取证 |
| 2026-09-03 | rmfysszc.gov.cn | 详情页 | 解析 | 未知 → 服务端渲染静态页，正则 7/7（需用户提供 HTML） | 实测破解 |
| 2026-09-03 | rmfysszc.gov.cn | 全站 | 抓取路径 | 「用户授权 `--no-robots` 最小量抓取」可用 → **彻底取消**（`_common.DENY_DOMAINS` 黑名单 + `fetch_page.py` 硬拦截退出码 3 + `download_assets.py` 附件跳过），仅保留人工取证解析 | 用户明确要求"自动抓取取消掉这条路径" |
| 2026-09-03 | cquae.com | 全站 | 架构 | 未知 → Vue SPA（977B 空壳）需浏览器渲染 → **实为公开 JSON API 直抓（无需登录）** | 实测破解（浏览器非必需） |
| 2026-09-03 | cquae.com | 列表 API | 接口 | 未知 → `GET /api/trade/project/projectPageQuery?page&size&projectType` | 实测（performance 资源抓包 + fetch 验证） |
| 2026-09-03 | cquae.com | 全量 feed | 业务规则 | 以为只含国资、**司法拍卖过滤假设成立** → 实证 `projectType=31` 全量 0 条司法拍卖（均国资/央企房产转让）；**扭转：联交所本就以国资/社会拍卖为主，按用户要求默认全量抓 法拍/国资/社会拍卖，靠 `classify_listing_type` 打类型** | 用户明确"直接抓国资/社会拍卖房源" |
| 2026-09-03 | cquae.com | 详情 API | 接口 | 未知 → `GET /api/trade/project/projectDetailQuery?id=<projectId>` | 实测（返回"项目不存在"证明路由/参数正确） |
| 2026-09-03 | cquae.com | 司法拍卖定位 | 业务规则 | 以为有独立栏目/项目类型 → **无 projectType，靠 serviceOrgName 含"司法拍卖"过滤** | 实测（keyword 参数被忽略，projectType 有效） |
| 2026-09-03 | cquae.com | 房产 feed 司法拍卖 | 业务规则 | **以为 serviceOrgName 含"司法拍卖"可筛出法拍** → 实证 `projectType=31` 全量 1580 条**0 条司法拍卖**（均为国资/央企/地方行政房产转让，`projectTag`=房产转让/地方国资/央企项目），重庆联交所房产接口**不含法拍房**，司法拍卖过滤假设不成立 | 全量抓 projectType=31 验证，重庆住房法拍搜索应直接走阿里法拍 + 诉讼资产网人工取证 |
| 2026-09-03 | _common.robots_allows | 判定逻辑 | 406/非200 处理 | 非200错误页当 robots 解析（误判禁止）→ 非200/非标准内容一律视为允许 | 修复：避免误杀可用政务源（如曾误判诉讼资产网） |
| 2026-09-03 | sf.taobao.com | 列表页 | 数据位置 | 未知 → `<script id="sf-item-list-data">` 的 `{"data":[...]}` | 实测破解，60 条/页 |
| 2026-09-03 | sf.taobao.com | 列表页 | 筛选参数 | 未知 → `category=50025969`(房产)/`page=N` 有效；`city=/q=` 无效 | 实测 |
| 2026-09-03 | sf.taobao.com | 列表页 | 北京筛选参数 | `province=11`/`city=110105`/`keyword=朝阳区`/`province=北京` → 全部 BLOCKED（896-930 字符风控空壳页） | 实测；北京需求与上海同结论——阿里无法程序化按城市筛选 |
| 2026-09-03 | sf.taobao.com | 全国流 | 地域分布 | 2400 条（40 页）中重庆 1792 + 渝地无前缀约 600 ≈ 99%，上海 4/河南 2/安徽 1/江苏 1/**北京 0**（含"北京"的 2 条实为重庆北碚"北京路"街道） | 北京朝阳 800-1200 万任务实测；北京法拍主平台是京东（已剔除）+北交所（521 拦截） |
| 2026-09-03 | cbex.com.cn / paimai.caa123.org.cn | 首页 | 浏览器渲染 | 北交所渲染后 title/body 全空（521 挑战对无头浏览器同样生效）；中拍平台 body 仅"安全提示"图片 | 实测；两站浏览器渲染档均不可用，只能人工浏览 |
| 2026-09-03 | gpai.net | /sf/ 首页 | 北京覆盖 | 首页文本"上海"36 次/"北京"1 次（页脚）/"朝阳"0，item2.do 北京链接 0 | 公拍网对北京无覆盖，仅上海 |

> **待验证项（首次真实使用时优先补齐）**：
> 1. 阿里法拍城市/关键词筛选（`city=`/`q=` 拼接返回 0 条，需浏览器逆向或用标题本地过滤兜底）
> 2. 中拍平台 / 北交所 / 上海联交所 浏览器渲染后的提取模板
> 3. 阿里法拍详情页静态可达性（含公告/评估报告附件下载）
> 4. 重庆联交所 `projectDetailQuery` 返回的完整字段结构（已确认路由，待用真实 projectId 跑通并映射）
>
> 首次真实使用后，请务必回来填充这张表 —— 这张表的质量，决定了这个技能半年后还灵不灵。
