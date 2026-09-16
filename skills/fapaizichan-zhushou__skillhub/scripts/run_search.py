# -*- coding: utf-8 -*-
"""
run_search.py —— 一键检索编排（2026-09-07 新增）

把 2026-09-07 手工串联的「阿里批量采集 → 联交所扫站 → 标准化 → 评分 → 双版本报告」
固化成一条命令，减少来回工具调用（整体交付时间从几十分钟降到一次执行）。

用法：
  python scripts/run_search.py --config req.json --out-dir <dir> [--ali-pages 40]
                               [--run-cquae-sweep] [--cquae-hits <file>] [--skip-ali]

req.json 示例：
{
  "city": "重庆",
  "districts": ["江北区", "渝北区"],
  "price_min_wan": 35, "price_max_wan": 70,
  "budget_total_wan": 70,
  "residential": true,
  "tax_rate": 0.10,
  "top_n": 15,
  "sources": { "ali": true, "cquae": true }
}
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ph_core  # noqa: E402
import batch_ali  # noqa: E402
import gen_reports  # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")
SOE_KW = ["银行", "集团", "粮食", "期货", "建投", "化学", "国企", "国资", "国有",
          "担保", "储备", "中转库", "高科", "投资"]


def est_cost(price, tax):
    return [round(price * 1.05, 1), round(price * (1 + tax), 1)]


def band_of(price, budget):
    if price < budget * 0.75: return "A(低总价安全区)"
    if price <= budget * 0.93: return "B(主力段)"
    return "C(临界,易超预算)"


def round_guess(disc):
    if disc is None: return None
    if disc >= 66: return "一拍(推测)"
    if disc >= 50: return "二拍(推测)"
    return "变卖/三拍(推测)"


def from_ali(hit, cfg):
    p = hit.get("start_price_wan")
    ev = hit.get("eval_price_wan")
    disc = round(p / ev * 100, 1) if (p and ev) else None
    it = {
        "fingerprint": ph_core.fingerprint(hit["title"], p),
        "listing_type": "法拍",
        "source": "阿里法拍(sf.taobao.com)",
        "source_url": hit.get("source_url"),
        "title": hit["title"],
        "district": hit.get("district"),
        "start_price_wan": p,
        "eval_price_wan": ev,
        "discount_pct": disc,
        "round_guess": round_guess(disc),
        "price_note": "起拍价≥评估价，异常折价，须核对法院公告" if disc and disc >= 100 else None,
        "current_price_wan": hit.get("current_price_wan"),
        "status": hit.get("status") or "未获取",
        "end_time": hit.get("end_time"),
        "est_cost_wan": est_cost(p, cfg["tax_rate"]) if p else None,
        "area_sqm": None, "layout": None, "court": None,
        "support_loan": hit.get("support_loan"),
        "fetched_at": TODAY,
        "_missing": ["面积", "户型", "法院", "保证金", "清场责任（阿里详情页需登录）"],
    }
    return it


def from_cquae(h, cfg):
    name = (h.get("noticeName") or "").strip()
    p = h.get("listingPrice")
    if not isinstance(p, (int, float)):
        return None
    org = h.get("serviceOrgName") or ""
    seller = h.get("sellerName") or ""
    if "司法拍卖" in org:
        lt = "法拍"
    elif any(k in seller for k in SOE_KW):
        lt = "国资"
    else:
        lt = "社会拍卖"
    ok, why = ph_core.is_residential(name)
    if cfg.get("residential", True) and not ok:
        return None
    end = (h.get("listEndDate") or "")[:10]
    status = ph_core.status_from_dates("PUBLICING", h.get("listStartDate"), h.get("listEndDate"))
    it = {
        "fingerprint": ph_core.fingerprint(name, p),
        "listing_type": lt,
        "source": "重庆联交所(cquae.com)",
        "source_url": "https://www.cquae.com/#/projectDetail?id=%s" % h.get("projectId"),
        "title": ph_core.clean_title(name),
        "district": ph_core.match_district(name, cfg["districts"], cfg["city"])[0],
        "start_price_wan": round(p, 2),
        "eval_price_wan": None,
        "discount_pct": None,
        "round_guess": None,
        "price_note": None,
        "current_price_wan": None,
        "status": status,
        "end_time": h.get("listEndDate"),
        "est_cost_wan": est_cost(p, cfg["tax_rate"]),
        "area_sqm": None, "layout": None, "court": None,
        "support_loan": None,
        "seller": seller,
        "fetched_at": TODAY,
        "_missing": ["面积", "户型", "评估价"],
    }
    return it


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--ali-pages", type=int, default=40)
    ap.add_argument("--skip-ali", action="store_true")
    ap.add_argument("--run-cquae-sweep", action="store_true", help="调用 browser_sweep.sh 采集联交所")
    ap.add_argument("--cquae-hits", default=None, help="已有联交所命中 JSON（跳过扫站）")
    ap.add_argument("--ali-interval", default="2.0,4.0")
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    cfg.setdefault("tax_rate", 0.10)
    cfg.setdefault("top_n", 15)
    cfg.setdefault("residential", True)
    os.makedirs(args.out_dir, exist_ok=True)

    outdir = args.out_dir
    items = []
    source_notes = []

    # 1) 阿里法拍
    if not args.skip_ali and cfg.get("sources", {}).get("ali", True):
        lo, hi = args.ali_interval.split(",")
        sys.argv = ["batch_ali.py", "--out-dir", outdir, "--start", "1", "--end", str(args.ali_pages),
                    "--city", cfg["city"], "--districts", ",".join(cfg["districts"]),
                    "--min-price", str(cfg.get("price_min_wan", 0)),
                    "--max-price", str(cfg.get("price_max_wan", 0)),
                    "--min-interval", lo, "--max-interval", hi]
        if cfg.get("residential", True):
            sys.argv.append("--residential")
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            batch_ali.main()
        ali_file = os.path.join(outdir, "ali_hits.json")
        if os.path.exists(ali_file):
            d = json.load(open(ali_file, encoding="utf-8"))
            for h in d["hits"]:
                it = from_ali(h, cfg)
                if it: items.append(it)
            st = d.get("stats", {})
            note = "阿里法拍 %d 页：命中 %d 条" % (args.ali_pages, len(d["hits"]))
            if isinstance(st, dict):
                parts = []
                if st.get("pages_ok"): parts.append("成功 %d 页" % st["pages_ok"])
                if st.get("pages_cache"): parts.append("缓存 %d 页" % st["pages_cache"])
                if st.get("pages_fail"): parts.append("失败 %d 页" % st["pages_fail"])
                if st.get("total_items"): parts.append("原始 %d 条" % st["total_items"])
                if st.get("dups"): parts.append("去重 %d" % st["dups"])
                if parts: note += "（" + "；".join(parts) + "）"
            source_notes.append(note)
        else:
            source_notes.append("阿里法拍：未产出结果")

    # 2) 重庆联交所
    if cfg.get("sources", {}).get("cquae", False):
        hits_file = args.cquae_hits
        if args.run_cquae_sweep and not hits_file:
            import subprocess
            sweep = os.path.join(HERE, "browser_sweep.sh")
            hits_file = os.path.join(outdir, "cquae_hits.json")
            match = "|".join(d[:-1] if d.endswith("区") else d for d in cfg["districts"])
            subprocess.run(["bash", sweep, "--base", "https://www.cquae.com/",
                            "--api", "/api/trade/project/projectPageQuery?page={page}&size=20&projectType=31",
                            "--start", "1", "--end", "83", "--match", match,
                            "--out", hits_file], check=False)
        if hits_file and os.path.exists(hits_file):
            raw = json.load(open(hits_file, encoding="utf-8"))
            n = 0
            for h in raw:
                t = (h.get("noticeName") or "")
                d, why = ph_core.match_district(t, cfg["districts"], cfg["city"])
                if not d:
                    continue
                p = h.get("listingPrice")
                if not isinstance(p, (int, float)):
                    continue
                if cfg.get("price_min_wan") and p < cfg["price_min_wan"]:
                    continue
                if cfg.get("price_max_wan") and p > cfg["price_max_wan"]:
                    continue
                it = from_cquae(h, cfg)
                if it:
                    it["district"] = d
                    items.append(it); n += 1
            source_notes.append("重庆联交所：命中 %d 条（过滤后）" % n)

    # 3) 去重 + 评分
    items = ph_core.merge_cross_source(items)
    for it in items:
        it["band"] = band_of(it["start_price_wan"], cfg["budget_total_wan"])
        it["score"] = ph_core.score(it, cfg["budget_total_wan"])
    items.sort(key=lambda x: -x["score"])

    listings = {
        "meta": {
            "query": cfg,
            "fetched_at": TODAY,
            "source_notes": source_notes,
            "total": len(items),
        },
        "listings": items,
    }
    lp = os.path.join(outdir, "listings.json")
    json.dump(listings, open(lp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    gen_reports.render(lp, os.path.join(outdir, "report.md"),
                       os.path.join(outdir, "report.html"), cfg)
    print("[run_search] 命中 %d 条 -> %s" % (len(items), outdir))
    for n in source_notes:
        print("  -", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
