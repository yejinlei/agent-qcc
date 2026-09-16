#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
extract_page.py - 从已保存的 HTML 中提取结构化房源字段与图片 URL

主力路径：抠页面内嵌 JSON（__INITIAL_STATE__ / __NEXT_DATA__ / __NUXT__ / JSON-LD），
比解析 DOM 稳定得多。房产站改版后字段路径会变，本脚本会输出命中的路径，
便于按 SKILL.md §5 回写 references/site-playbook.md 的「实测修正区」。

用法：
  python extract_page.py --html raw/sf_list.html   --site ali-auction --out listings.json
  python extract_page.py --html raw/cquae_api.json --site cquae --out out.json        # 联交所 JSON API（法拍/国资/社会拍卖全量）
  python extract_page.py --html raw/cquae_api.json --site cquae --judicial-only --out out.json
  python extract_page.py --html raw/gpai_detail.html --site gpai --out out.json --base-url <详情URL>

退出码：
  0 成功（可能含未获取字段）
  5 未找到任何内嵌 JSON（说明站方改版或该页需 JS 渲染 -> 改用 agent-browser）
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import extract_json_after, dig, log, now_iso  # noqa: E402

# 内嵌 JSON 的常见挂载点
JSON_MARKERS = [
    ("__INITIAL_STATE__", "window.__INITIAL_STATE__"),
    ("__NUXT__", "window.__NUXT__"),
    ("__NEXT_DATA__", "__NEXT_DATA__"),
]

# 图片属性（含懒加载字段）
IMG_ATTRS = ["data-src", "data-original", "data-lazy-src", "data-url", "src", "href"]

# 户型图判定关键词
LAYOUT_KEYS = ["huxing", "layout", "floorplan", "floor_plan", "pingmian",
               "ajk_huxing", "户 型", "户型"]

# ------------------------------------------------------- 法拍阶段 / 状态识别
# 用户要求：法拍房必须标注「一拍/二拍/正在进行中」等词汇，方便直接读取。
# 拆成两个维度：
#   stage_round（拍次）：一拍 / 二拍 / 三拍 / 变卖 / 重新拍卖
#   status（进行状态）：即将开始 / 正在进行中 / 已结束 / 已成交 / 流拍 / 中止 / 撤回
# 命中不了就返回 None（禁止编造）。
ROUND_PATTERNS = [
    (r"重新拍卖", "重新拍卖"),
    (r"变卖", "变卖"),
    (r"(?:第)?三拍|三次拍卖", "三拍"),
    (r"(?:第)?二拍|二次拍卖", "二拍"),
    (r"(?:第)?一拍|首拍|第一次拍卖", "一拍"),
]
STATUS_PATTERNS = [
    (r"正在进行中|竞价中|出价中|进行中|正在拍卖", "正在进行中"),
    (r"即将开始|即将开拍|待开拍|报名中|预告", "即将开始"),
    (r"已成交|成交|已拍出", "已成交"),
    (r"已结束|拍卖结束|已流拍?结束", "已结束"),
    (r"流拍", "流拍"),
    (r"中止拍卖|中止", "中止"),
    (r"撤回|撤销", "撤回"),
]


def detect_stage_round(text):
    if not text:
        return None
    for pat, label in ROUND_PATTERNS:
        if re.search(pat, text):
            return label
    return None


def detect_auction_status(text):
    if not text:
        return None
    for pat, label in STATUS_PATTERNS:
        if re.search(pat, text):
            return label
    return None


# ------------------------------------------------------- 房源类型枚举
# 本技能统一覆盖三类**拍卖/资产处置**标的（按用户 2026-09-03 调整）：
#   - 法拍     ：司法拍卖（阿里法拍 / 公拍网 / 联交所司法拍卖服务中心）
#   - 国资     ：国有资产处置 / 转让（重庆联交所、公拍网国资板块等）
#   - 社会拍卖 ：社会委托 / 资产拍卖（非司法、非国有的公开拍卖）
# 普通二手房（贝壳/安居客/58/链家）按用户要求**不做交叉验证，不接入**。
# 联交所（cquae）的标的按 serviceOrgName 区分上述三类，默认全量抓取（--judicial-only 仅保留法拍）。


def classify_listing_type(org, title=""):
    """根据服务机构名 + 标题，判定房源类型（法拍 / 国资 / 社会拍卖）。"""
    if "司法拍卖" in org:
        return "法拍"
    if any(k in (org + title) for k in ("国资", "国有", "资产处置",
                                         "产权交易", "公共资源", "央企")):
        return "国资"
    return "社会拍卖"


# ------------------------------------------------------- 字段映射表
# --- 已实测验证的结构（2026-09-03）--------------------------------------
# 阿里法拍 sf.taobao.com：列表页数据在 <script id="sf-item-list-data" type="text/json">
# 内的 {"data":[...]} 数组中，每项含 id / itemUrl / title / picUrl / initialPrice /
# currentPrice / consultPrice(评估价) / marketPrice / status / start / end(毫秒时间戳) /
# viewerCount / bidCount / applyCount / delayCount / supportLoans / buyRestrictions / sellOff
#
# 其余站点的字段路径为初始模板，尚未在真实页面验证，使用时务必先跑 --probe + --dump-keys。
SITE_CONF = {
    "ali-auction": {
        "list_path": "data",
        "item_fields": {
            "id":            ["id"],
            "title":         ["title"],
            "detail_url":    ["itemUrl"],
            "pic_url":       ["picUrl"],
            "start_price":   ["initialPrice"],
            "current_price": ["currentPrice"],
            "appraisal":     ["consultPrice"],
            "market_price":  ["marketPrice"],
            "status":        ["status"],
            "start_time":    ["start"],
            "end_time":      ["end"],
            "bid_count":     ["bidCount"],
            "apply_count":   ["applyCount"],
            "viewer_count":  ["viewerCount"],
            "delay_count":   ["delayCount"],
            "support_loan":  ["supportLoans"],
            "buy_limit":     ["buyRestrictions"],
            "sell_off":      ["sellOff"],
        },
        "detail_fields": {},
        "ts_fields": ["start_time", "end_time"],   # 毫秒时间戳 → ISO8601
    },
    "rmfysszc": {
        # 该源详情页为服务端渲染的静态 HTML，字段在正文文本里（非 JSON）。
        # 走 text_fields 正则兜底（见 extract_text_fields）。
        "detail_fields": {},
        "text_fields": {
            "title":       r'<title>\s*(.*?)(?:的公告|的变卖|的公告-|\s*-)',
            "court":       r'([一-龥]{2,}人民法院)',
            "start_price": r'起拍价[：:]\s*([\d,]+)\s*元',
            "appraisal":   r'评估市场价值[：:]\s*([\d,]+)\s*元',
            "deposit":     r'保证金[^0-9]{0,8}([\d,]+)\s*元',
            "increment":   r'增价幅度[^0-9]{0,8}([\d,]+)\s*元',
            "area":        r'建筑面积\s*[：:]\s*([\d.]+)\s*平方米',
        },
    },
    "gpai": {
        # 公拍网（上海司法拍卖等）详情页：服务端渲染静态 HTML，数据在正文文本里。
        # 详情页地址形如 https://www.gpai.net/sf/item2.do?Web_Item_ID={id}
        # 实测（2026-09-03）：HTTP 200 / 113KB，字段文本规整，可直接正则提取。
        # ⚠ robots 禁 /sf/search.do、/sf/noticeDetail.do、/api/ 等；
        #    但 item2.do 详情页未被禁，可直接静态抓。列表入口从首页 /sf/ 或公告页里的
        #    item2.do 链接顺藤摸瓜。
        "detail_fields": {},
        "text_fields": {
            "title":       r'<title>\s*([^<\n]+?)\s*-\s*拍品详情',
            "court":       r'处置法院[：:]\s*([一-龥]+?人民法院)',
            "start_price": r'(?:一拍)?起拍价[：:]\s*([\d,]+)\s*元',
            "appraisal":   r'评估价[：:]\s*([\d,]+)\s*元',
            "deposit":     r'保证金[：:]\s*([\d,]+)\s*元',
            "increment":   r'(?:增|加)价幅度[：:]\s*([\d,]+)\s*元',
            "area":        r'房产面积[：:]\s*([\d.]+)\s*平方米',
        },
    },
}

SITE_ALIASES = {
    "ali-auction": "ali-auction", "sf": "ali-auction", "taobao": "ali-auction",
    "阿里法拍": "ali-auction", "淘宝司法": "ali-auction", "sf.taobao": "ali-auction",
    "rmfysszc": "rmfysszc", "court": "rmfysszc", "诉讼资产网": "rmfysszc",
    "人民法院诉讼资产网": "rmfysszc",
    "gpai": "gpai", "公拍网": "gpai", "上海公拍": "gpai", "gpai.net": "gpai",
    "cquae": "cquae", "重庆联交所": "cquae", "cquae.com": "cquae",
}


def find_blobs(html):
    """找出页面内所有候选内嵌 JSON。"""
    found = {}
    for name, marker in JSON_MARKERS:
        obj, _ = extract_json_after(html, marker)
        if obj is not None:
            found[name] = obj
    # JSON-LD
    lds = []
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.S | re.I
    ):
        try:
            lds.append(json.loads(m.group(1).strip()))
        except Exception:
            pass
    if lds:
        found["JSON-LD"] = lds
    # <script id="xxx" type="text/json|application/json"> 内联块
    # （阿里法拍列表页用 id="sf-item-list-data"，数据在 {"data":[...]} 里）
    for m in re.finditer(
        r'<script[^>]*\bid=["\']([\w-]+)["\'][^>]*\btype=["\'](?:text|application)/json["\']'
        r'[^>]*>(.*?)</script>',
        html, re.S | re.I
    ):
        try:
            found["script#%s" % m.group(1)] = json.loads(m.group(2).strip())
        except Exception:
            pass
    # script 内裸 JSON（兜底：形如 <script>window.xxx = {"a":1}</script>）
    return found


def collect_keys(obj, prefix="", depth=0, out=None, limit=400):
    if out is None:
        out = []
    if depth > 5 or len(out) >= limit:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (prefix, k) if prefix else str(k)
            out.append(p)
            collect_keys(v, p, depth + 1, out, limit)
    elif isinstance(obj, list) and obj:
        collect_keys(obj[0], prefix + ".0", depth + 1, out, limit)
    return out


def extract_images(html, base_url=None):
    """提取图片候选，并标记户型图。"""
    seen = {}
    # 1) img / a 标签里的属性
    for m in re.finditer(r"<(?:img|a)\b[^>]*>", html, re.I):
        tag = m.group(0)
        for attr in IMG_ATTRS:
            am = re.search(attr + r'\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if not am:
                continue
            url = am.group(1).strip()
            if not url or url.startswith("data:") or url.startswith("javascript:"):
                continue
            if not re.match(r"^https?://", url) and base_url:
                url = urljoin_simple(base_url, url)
            if not re.search(r"\.(jpg|jpeg|png|webp|gif|bmp)(\?|$)", url, re.I):
                continue
            if url in seen:
                continue
            low = (tag + " " + url).lower()
            is_layout = any(k.lower() in low for k in LAYOUT_KEYS)
            seen[url] = {
                "url": url,
                "type": "户型图" if is_layout else "未分类",
                "is_layout_plan": is_layout,
                "evidence": "tag" if is_layout else "",
            }
            break
    # 2) 页面文本中的 CDN 图片（JS 数组里的，标签扫描抓不到）
    for m in re.finditer(
        r'(https?:)?//[^\s"\'<>\\)]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\s"\'<>\\)]*)?',
        html, re.I
    ):
        url = m.group(0)
        if url.startswith("//"):
            url = "https:" + url
        if url in seen:
            continue
        # 窗口收紧，避免蹭到相邻节点的关键词造成误判
        ctx = html[max(0, m.start() - 60):m.end() + 30].lower()
        is_layout = any(k.lower() in ctx for k in LAYOUT_KEYS)
        seen[url] = {
            "url": url,
            "type": "户型图" if is_layout else "未分类",
            "is_layout_plan": is_layout,
            "evidence": "context" if is_layout else "",
        }
    return list(seen.values())


def urljoin_simple(base, rel):
    if rel.startswith("//"):
        return "https:" + rel
    if rel.startswith("/"):
        m = re.match(r"(https?://[^/]+)", base or "")
        return (m.group(1) if m else "") + rel
    return rel


def extract_text_fields(html, text_fields):
    """兜底：从服务端渲染的纯文本里用正则抠字段（法院法拍网等）。
    返回 (命中字段 dict, 命中路径 dict)。数值会去掉千分位逗号并转 int/float。"""
    # 去脚本/样式，便于按整段文本匹配
    body = re.sub(r"<script[\s\S]*?</script>", " ", html)
    body = re.sub(r"<style[\s\S]*?</style>", " ", body)
    plain = re.sub(r"<[^>]+>", " ", body)
    got, paths = {}, {}
    for fname, pat in text_fields.items():
        m = re.search(pat, html, re.S) or re.search(pat, plain)
        if not m:
            continue
        val = m.group(1) if m.groups() else m.group(0)
        val = val.strip()
        # 数值清洗：去掉千分位逗号后尝试转数字
        if fname in ("start_price", "appraisal", "deposit", "increment", "area"):
            num = re.sub(r"[^\d.]", "", val)
            if num:
                try:
                    val = float(num) if "." in num else int(num)
                except ValueError:
                    pass
        if val:
            got[fname] = val
            paths[fname] = "regex:" + pat[:40]
    return got, paths


def parse_cquae_json(text, judicial_only=False):
    """解析重庆联交所 projectPageQuery 的 JSON 响应，输出统一 Listing。

    接口：GET https://www.cquae.com/api/trade/project/projectPageQuery?page&size&projectType
    响应：{"state":"SUCCESS","target":{"result":[...], "totalPage":N}}
    联交所标的按服务机构名（serviceOrgName）分三类：
      法拍（含"司法拍卖"）/ 国资（含"国资/国有/资产处置"等）/ 社会拍卖（其余）。
    默认全量抓取；--judicial-only 仅保留法拍。
    """
    data = json.loads(text)
    target = data.get("target", data)
    arr = target.get("result") if isinstance(target, dict) else None
    if not isinstance(arr, list):
        arr = data.get("result") or []
    listings, missing_all = [], set()
    for it in arr:
        if not isinstance(it, dict):
            continue
        org = it.get("serviceOrgName") or ""
        title = it.get("noticeName") or ""
        ltype = classify_listing_type(org, title)
        is_jud = (ltype == "法拍")
        if judicial_only and not is_jud:
            continue
        pid = str(it.get("projectId", ""))
        price = it.get("listingPrice")
        unit = it.get("priceTypeStr", "")
        price_wan = price if (isinstance(price, (int, float)) and "万元" in unit) else None
        deposit_yuan = it.get("deposit") or 0
        try:
            deposit_wan = round(deposit_yuan / 1e4, 4) if deposit_yuan else None
        except Exception:
            deposit_wan = None
        imgs = []
        raw = it.get("webSiteImg")
        if isinstance(raw, str) and raw.strip() not in ("", "[]"):
            try:
                imgs = [u for u in json.loads(raw) if isinstance(u, str)]
            except Exception:
                imgs = []
        got = {
            "id": "cquae-" + pid,
            "source": "cquae.com",
            "listing_type": ltype,
            "title": it.get("noticeName"),
            "detail_url": "https://www.cquae.com/api/trade/project/projectDetailQuery?id=" + pid,
            "community_name": it.get("noticeName"),
            "price_total_wan": price_wan,
            "tags": it.get("projectTag") or [],
            "auction": {
                "court": org if is_jud else None,
                "stage_round": detect_stage_round(title),
                "status": detect_auction_status(title),
                "start_price_wan": price_wan,
                "deposit_wan": deposit_wan,
                "start_time": it.get("listStartDate"),
                "end_time": it.get("listEndDate"),
                "service_org": org,
                "is_judicial": is_jud,
            },
            "images": [{"url": u, "type": "标的图片", "is_layout_plan": False} for u in imgs],
            "missing_fields": [],
        }
        miss = [k for k in ("title", "price_total_wan") if not got.get(k)]
        got["missing_fields"] = miss
        missing_all.update(miss)
        listings.append(got)
    return listings, sorted(missing_all)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True)
    ap.add_argument("--site", default="auto")
    ap.add_argument("--out", default=None)
    ap.add_argument("--base-url", default=None, help="详情页 URL，用于解析相对路径与来源标记")
    ap.add_argument("--dump-keys", action="store_true", help="打印内嵌 JSON 的键名（改版排查用）")
    ap.add_argument("--probe", action="store_true", help="探针模式：只报告解析是否成功")
    ap.add_argument("--judicial-only", action="store_true",
                    help="（cquae 源）只保留 serviceOrgName 含'司法拍卖'的标的")
    args = ap.parse_args()

    if not os.path.exists(args.html):
        log("[error] 文件不存在：%s" % args.html)
        return 5

    raw = open(args.html, "rb").read()
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("gb18030", errors="replace")

    result, blobs = extract_listings(html, args.site, args.base_url, args.judicial_only)
    if result is None:
        return 5
    listings = result["listings"]
    fields = result["fields"]
    missing = result["missing_fields"]
    images = result["images"]
    layout_imgs = result["layout_images"]
    mode = result["mode"]
    if args.dump_keys:
        for name, obj in blobs.items():
            keys = collect_keys(obj)
            log("\n=== %s (%d keys) ===" % (name, len(keys)))
            for k in keys[:200]:
                log("  " + k)

    out_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_json)
        log("[ok] -> %s" % args.out)
    else:
        print(out_json)

    if mode == "list":
        log("[summary] 列表模式：解析出 %d 条；图片 %d 张，其中户型图 %d 张"
            % (len(listings), len(images), len(layout_imgs)))
    else:
        denom = len(conf.get("detail_fields", {})) or len(conf.get("text_fields", {}))
        log("[summary] 详情模式：字段命中 %d/%d；图片 %d 张，其中户型图 %d 张"
            % (len(fields), denom, len(images), len(layout_imgs)))
    if missing:
        log("[summary] 未获取字段（禁止编造）：%s" % ", ".join(missing))

    if args.probe:
        denom = len(conf.get("detail_fields", {})) or len(conf.get("text_fields", {}))
        ok = (len(listings) > 0) if mode == "list" else (
            len(fields) >= max(1, denom // 3))
        log("[probe] %s" % ("PASS 解析模板有效" if ok else "FAIL 疑似改版，需逆向修正"))
        return 0 if ok else 5
    return 0

def extract_listings(html, site_name="ali-auction", base_url=None, judicial_only=False):
    """可复用提取入口（供 batch_ali.py / run_search.py 直接调用，避免子进程开销）。
    返回 (result_dict, blobs)；失败返回 (None, None)。"""
    raw_site = site_name.strip().lower()

    site = SITE_ALIASES.get(site_name.strip().lower(), site_name)
    if site not in SITE_CONF:
        site = "ali-auction"
    conf = SITE_CONF[site]

    # —— 重庆联交所 JSON API 直抓分支 ——
    # 输入若是 JSON（API 响应），且站点为 cquae（或 auto + JSON），直接解析，跳过 HTML 流程。
    stripped = html.strip()
    if stripped[:1] in ("{", "[") and (site_name.strip().lower() in ("cquae", "重庆联交所")
                                      or site_name.strip().lower() == "auto"):
        try:
            listings, miss = parse_cquae_json(stripped, judicial_only=judicial_only)
        except Exception as e:
            log("[error] cquae JSON 解析失败：%s" % e)
            return (None, None)
        result = {
            "source": "cquae.com",
            "detail_url": base_url,
            "extracted_at": now_iso(),
            "mode": "list-json",
            "listings": listings,
            "counts": {
                "listings": len(listings),
                "images": sum(len(x.get("images", [])) for x in listings),
                "layout_images": 0,
            },
            "missing_fields": miss,
        }
        out_json = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(out_json)
            log("[ok] -> %s" % args.out)
        else:
            print(out_json)
        log("[summary] cquae JSON：解析出 %d 条（司法拍卖过滤=%s），图片 %d 张"
            % (len(listings), judicial_only, result["counts"]["images"]))
        return 0

    blobs = find_blobs(html)
    has_text_fallback = bool(conf.get("text_fields"))
    if not blobs and not has_text_fallback:
        log("[warn] 未找到内嵌 JSON。该页可能由 JS 动态渲染，或页面是风控空壳页。")
        log("[warn] 建议：改用 agent-browser 渲染后再导出 DOM，或检查 HTML 实际内容。")
        return (None, None)
    if not blobs:
        log("[info] 无内嵌 JSON，将走 text_fields 文本正则兜底（如法院法拍网详情页）。")

    def map_fields(obj, fmap, ts_fields=()):
        """按候选路径依次尝试，取第一个非空值。"""
        got, paths = {}, {}
        for fname, candidate_paths in fmap.items():
            for p in candidate_paths:
                v = dig(obj, p)
                if v not in (None, "", [], {}):
                    if fname in ts_fields and isinstance(v, (int, float)) and v > 1e12:
                        from datetime import datetime, timezone, timedelta
                        v = datetime.fromtimestamp(
                            v / 1000, timezone(timedelta(hours=8))
                        ).replace(microsecond=0).isoformat()
                    if fname in ("detail_url", "pic_url") and isinstance(v, str) \
                            and v.startswith("//"):
                        v = "https:" + v
                    got[fname] = v
                    paths[fname] = p
                    break
        return got, paths, [k for k in fmap if k not in got]

    def blob_name_of(target):
        for k, b in blobs.items():
            if b is target:
                return k
        return "?"

    listings, fields, hit_path, missing = [], {}, {}, []
    mode = "detail"

    # 列表模式：站点配置指定了 list_path，且能取到数组（如阿里法拍）
    for blob in blobs.values():
        lp = conf.get("list_path")
        if not lp:
            continue
        arr = dig(blob, lp)
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            mode = "list"
            imap = conf.get("item_fields", {})
            ts = conf.get("ts_fields", ())
            for it in arr:
                got, paths, miss = map_fields(it, imap, ts)
                got["_source_blob"] = blob_name_of(blob)
                got["_missing_fields"] = miss
                # 统一标注法拍阶段 / 状态（用户要求：一拍/二拍/正在进行中 等）
                title_txt = got.get("title") or ""
                got["listing_type"] = "法拍"
                got["auction"] = {
                    "stage_round": detect_stage_round(title_txt),
                    "status": detect_auction_status(title_txt),
                    "start_price_wan": got.get("start_price"),
                    "appraisal_price_wan": got.get("appraisal"),
                    "current_price_wan": got.get("current_price"),
                    "market_price_wan": got.get("market_price"),
                    "start_time": got.get("start_time"),
                    "end_time": got.get("end_time"),
                    "bid_count": got.get("bid_count"),
                    "apply_count": got.get("apply_count"),
                    "is_judicial": True,
                }
                listings.append(got)
            hit_path = {"_list_path": "%s -> %s" % (blob_name_of(blob), lp)}
            missing = sorted({m for it in listings for m in it.get("_missing_fields", [])})
            break

    # 详情模式：回退到 detail_fields
    if mode == "detail":
        fmap = conf.get("detail_fields", {})
        for blob in blobs.values():
            fields, paths, missing = map_fields(blob, fmap)
            if fields:
                hit_path = {k: "%s -> %s" % (blob_name_of(blob), v)
                            for k, v in paths.items()}
                break
        # 兜底：服务端渲染的纯文本页面（法院法拍网等）走正则
        if not fields and conf.get("text_fields"):
            tfields, tpaths = extract_text_fields(html, conf["text_fields"])
            if tfields:
                fields, hit_path = tfields, tpaths
                missing = [k for k in conf["text_fields"] if k not in fields]

        # 详情模式统一标注：类型 / 法拍阶段 / 状态（用户要求：一拍/二拍/正在进行中 等）
        if fields:
            det_text = (fields.get("title") or "") + "\n" + html[:4000]
            if fields.get("court"):
                ltype = "法拍"
            elif any(k in det_text for k in ("国资", "国有", "资产处置")):
                ltype = "国资"
            else:
                ltype = "社会拍卖"
            fields["listing_type"] = ltype
            fields["auction"] = {
                "stage_round": detect_stage_round(det_text),
                "status": detect_auction_status(det_text),
                "start_price_wan": fields.get("start_price"),
                "appraisal_price_wan": fields.get("appraisal"),
                "deposit_wan": fields.get("deposit"),
                "increment_wan": fields.get("increment"),
                "area_sqm": fields.get("area"),
                "court": fields.get("court"),
                "is_judicial": (ltype == "法拍"),
            }

    images = extract_images(html, base_url)
    layout_imgs = [i for i in images if i["is_layout_plan"]]
    for it in listings:                       # 列表页 JSON 里的图片并入清单
        if it.get("pic_url") and not any(i["url"] == it["pic_url"] for i in images):
            images.append({"url": it["pic_url"], "type": "标的图片",
                           "is_layout_plan": False, "evidence": "json"})

    result = {
        "source": site,
        "detail_url": base_url,
        "extracted_at": now_iso(),
        "mode": mode,
        "blobs_found": list(blobs.keys()),
        "listings": listings,
        "fields": fields,
        "field_paths": hit_path,
        "missing_fields": missing,
        "images": images,
        "layout_images": layout_imgs,
        "counts": {
            "listings": len(listings), "images": len(images),
            "layout_images": len(layout_imgs),
        },
    }

    return (result, blobs)



if __name__ == "__main__":
    sys.exit(main())
