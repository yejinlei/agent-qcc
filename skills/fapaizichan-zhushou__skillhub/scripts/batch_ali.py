# -*- coding: utf-8 -*-
"""
batch_ali.py —— 阿里法拍多页批量采集器（2026-09-07 新增，提速模块）

相比"每页两次 subprocess 调 fetch_page.py + extract_page.py"的旧方式：
  · 单进程内完成 抓取+解析+过滤+去重，省掉每页 ~1s 的子进程启动开销
  · 请求间隔可配（默认 2.0–4.0s；政务/法院站请手动调大到 5–10s）
  · --early-stop N：连续 N 页 0 命中则提前收工（非重庆城市可省掉几十页无效抓取）
  · 24h 缓存复用 + 断点续采（已存在 sf_pN.json 直接跳过）
  · 边采边用 ph_core 做准确度过滤（城市感知区县 / 住宅判定 / 按 sf_item ID 去重）

用法：
  python scripts/batch_ali.py --out-dir <dir> --start 1 --end 40 \
      --city 重庆 --districts 江北区,渝北区 --min-price 35 --max-price 70 \
      --min-interval 2.0 --max-interval 4.0 --early-stop 6
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _common as C
import extract_page
import ph_core

ALI_LIST = "https://sf.taobao.com/item_list.htm?category={cat}&page={page}"


def item_id(it):
    m = re.search(r"/sf_item/(\d+)\.htm", (it.get("detail_url") or ""))
    return m.group(1) if m else (it.get("id") or it.get("title"))


def price_wan(it):
    """阿里列表起拍价单位是元；若源站改版给出万元会 <10000，做单位兜底。"""
    sp = it.get("start_price")
    if not isinstance(sp, (int, float)):
        return None
    return round(sp / 10000, 2) if sp > 10000 else round(sp, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=40)
    ap.add_argument("--category", default="50025969", help="阿里房产类目，默认 50025969")
    ap.add_argument("--city", default="重庆")
    ap.add_argument("--districts", default="", help="逗号分隔，留空表示不过滤区县")
    ap.add_argument("--min-price", type=float, default=0)
    ap.add_argument("--max-price", type=float, default=0, help="0 表示不限")
    ap.add_argument("--residential", action="store_true", help="仅保留住宅类（剔除车位/商铺/负层等）")
    ap.add_argument("--min-interval", type=float, default=2.0)
    ap.add_argument("--max-interval", type=float, default=4.0)
    ap.add_argument("--early-stop", type=int, default=6, help="连续 N 页 0 命中则停止，0=不早停")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--force", action="store_true", help="忽略 24h 缓存重抓")
    args = ap.parse_args()

    raw = os.path.join(args.out_dir, "raw")
    os.makedirs(raw, exist_ok=True)
    state_dir = args.state_dir or os.path.join(args.out_dir, ".cache")
    districts = [d for d in args.districts.split(",") if d]

    hits, seen = [], set()
    stats = {"pages_ok": 0, "pages_cache": 0, "pages_fail": 0, "total_items": 0,
             "dups": 0, "filtered_out": 0, "started": time.time()}
    empty_streak = 0
    robots_checked = False

    for page in range(args.start, args.end + 1):
        hpath = os.path.join(raw, "sf_p%d.html" % page)
        jpath = os.path.join(raw, "sf_p%d.json" % page)

        if os.path.exists(jpath) and not args.force and C.cache_fresh(jpath):
            try:
                d = json.load(open(jpath, encoding="utf-8"))
                items = d.get("listings", []) if isinstance(d, dict) else d
                stats["pages_cache"] += 1
            except Exception:
                items = None
        else:
            items = None

        if items is None:
            url = ALI_LIST.format(cat=args.category, page=page)
            if not robots_checked:
                allowed, note = C.robots_allows(url, state_dir)
                C.log("[robots] %s -> %s" % (url, note))
                if not allowed:
                    C.log("[blocked] robots 禁止，放弃该站")
                    break
                robots_checked = True
            C.wait_for_slot(state_dir, "sf.taobao.com", args.min_interval, args.max_interval)
            status, _h, text = C.http_get(url, timeout=25)
            kind, why = C.classify_response(status, text)
            if kind != "OK":
                C.log("[page %d] %s：%s" % (page, kind, why))
                stats["pages_fail"] += 1
                if kind == "BLOCKED":
                    C.log("[abort] 被风控拦截，停止该站（红线 R1：不重试硬刚）")
                    break
                continue
            with open(hpath, "w", encoding="utf-8") as f:
                f.write(text)
            res, _ = extract_page.extract_listings(text, "ali-auction")
            if not res:
                stats["pages_fail"] += 1
                C.log("[page %d] 解析失败（可能改版）" % page)
                continue
            items = res["listings"]
            with open(jpath, "w", encoding="utf-8") as f:
                json.dump({"listings": items}, f, ensure_ascii=False)
            stats["pages_ok"] += 1

        stats["total_items"] += len(items)
        page_hits = 0
        for it in items:
            title = ph_core.clean_title(it.get("title"))
            if not title:
                continue
            if districts:
                d, why = ph_core.match_district(title, districts, args.city)
                if not d:
                    continue
            if args.residential:
                ok, _why = ph_core.is_residential(title)
                if not ok:
                    continue
            p = price_wan(it)
            if args.min_price and (p is None or p < args.min_price):
                continue
            if args.max_price and (p is not None and p > args.max_price):
                continue
            iid = item_id(it)
            if iid in seen:
                stats["dups"] += 1
                continue
            seen.add(iid)
            ap_ = it.get("appraisal")
            rec = {
                "source": "阿里法拍(sf.taobao.com)", "listing_type": "法拍",
                "item_id": iid, "title": title,
                "district": (ph_core.match_district(title, districts, args.city)[0]
                             if districts else None),
                "start_price_wan": p,
                "eval_price_wan": round(ap_ / 10000, 2) if isinstance(ap_, (int, float)) and ap_ > 10000 else (ap_ if isinstance(ap_, (int, float)) else None),
                "current_price_wan": round(it["current_price"] / 10000, 2) if isinstance(it.get("current_price"), (int, float)) and it["current_price"] > 10000 else None,
                "discount_pct": None,
                "status": ph_core.status_from_dates(it.get("status"), it.get("start_time"), it.get("end_time")),
                "end_time": it.get("end_time"), "start_time": it.get("start_time"),
                "bid_count": it.get("bid_count"), "source_url": it.get("detail_url"),
                "support_loan": it.get("support_loan"), "page": page,
            }
            if rec["start_price_wan"] and rec["eval_price_wan"]:
                rec["discount_pct"] = round(rec["start_price_wan"] / rec["eval_price_wan"] * 100, 1)
            hits.append(rec)
            page_hits += 1

        C.log("[page %d] items=%d hits=%d (cum %d)" % (page, len(items), page_hits, len(hits)))
        if args.early_stop:
            empty_streak = empty_streak + 1 if page_hits == 0 else 0
            if empty_streak >= args.early_stop and page >= args.start + 9:
                C.log("[early-stop] 连续 %d 页 0 命中，提前收工（第 %d 页）" % (empty_streak, page))
                break

    out = os.path.join(args.out_dir, "ali_hits.json")
    json.dump({"stats": stats, "hits": hits}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    stats["seconds"] = round(time.time() - stats["started"], 1)
    C.log("[done] 用时 %.1fs，抓取 %d 页(缓存 %d/失败 %d)，原始 %d 条，命中 %d 条，去重 %d 条"
          % (stats["seconds"], stats["pages_ok"], stats["pages_cache"],
             stats["pages_fail"], stats["total_items"], len(hits), stats["dups"]))
    print(json.dumps(stats, ensure_ascii=False))
    print("OUT %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
