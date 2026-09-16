#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
download_assets.py - 下载户型图 / 实拍图 / 法拍公告附件

输入为 extract_page.py 产出的 JSON（支持单个对象或数组，也支持清单列表），
输出：assets/ 下的图片与 PDF + manifest.json（记录 原图URL / 本地路径 / 类型 / 时间）。

合规：限速下载、保留水印、不修改图片内容、不采集任何个人信息。

用法：
  python download_assets.py --json listing.json --dir assets/
  python download_assets.py --json listing.json --dir assets/ --layout-only
  python download_assets.py --json listing.json --dir assets/ --prefix "中海枫涟-3室"
  python download_assets.py --json listing.json --dir assets/ --min-delay 2 --max-delay 5

退出码：0 完成（个别失败会记录在 manifest 中）
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    http_get_bytes, wait_for_slot, ensure_dir, domain_of, is_denied_domain,
    safe_filename, log, now_iso,
)

EXTS = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif",
    "application/pdf": ".pdf",
}

# 图片防盗链：部分 CDN 校验 Referer，取原图失败时尝试回退到带尺寸的 URL


def guess_ext(url, content_type):
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in EXTS:
        return EXTS[ct]
    path = urllib.parse.urlparse(url).path.lower()
    for e in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"):
        if path.endswith(e):
            return ".jpg" if e == ".jpeg" else e
    return ".jpg"


def strip_size_suffix(url):
    """贝壳/安居客图片常带 _400x300 尺寸后缀，去掉可拿原图（失败时回退原 URL）。"""
    return re.sub(r"_\d{2,4}x\d{2,4}\.(jpg|jpeg|png|webp)$", r".\1", url, flags=re.I)


def load_items(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = []
    buckets = data if isinstance(data, list) else [data]
    for d in buckets:
        if not isinstance(d, dict):
            continue
        if "url" in d:
            items.append(d)
            continue
        base_url = d.get("detail_url")
        seen = set()
        for im in (d.get("layout_images") or []) + (d.get("images") or []) \
                + (d.get("attachments") or []):
            if not isinstance(im, dict) or not im.get("url"):
                continue
            if im["url"] in seen:
                continue
            seen.add(im["url"])
            it = dict(im)
            it.setdefault("detail_url", base_url)
            items.append(it)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--dir", default="assets")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--layout-only", action="store_true", help="只下载户型图")
    ap.add_argument("--no-orig-size", action="store_true",
                    help="不尝试去掉尺寸后缀（默认会尝试取原图）")
    ap.add_argument("--min-delay", type=float, default=1.5)
    ap.add_argument("--max-delay", type=float, default=4.0)
    ap.add_argument("--state-dir", default=".cache")
    ap.add_argument("--limit", type=int, default=60, help="单次最多下载数量")
    args = ap.parse_args()

    ensure_dir(args.dir)
    items = load_items(args.json)
    if args.layout_only:
        items = [i for i in items if i.get("is_layout_plan") or i.get("type") == "户型图"]
    items = items[:args.limit]
    log("[info] 待下载 %d 个资源" % len(items))

    manifest, ok_n, fail_n = [], 0, 0
    for idx, im in enumerate(items, 1):
        url = im.get("url")
        if not url:
            continue
        # 域名黑名单硬拦截：该域名的附件不做任何网络请求，提示用户人工下载。
        if is_denied_domain(url):
            log("[denied] %s 在自动抓取黑名单中，跳过下载，请人工获取该附件。" % url)
            fail_n += 1
            manifest.append({
                "url": url, "requested_url": url, "local_path": None,
                "type": im.get("type", "未分类"), "ok": False,
                "error": "域名黑名单（自动抓取已取消），请在你自己的浏览器打开该链接人工下载",
                "crawled_at": now_iso(),
            })
            continue
        candidates = [url] if args.no_orig_size else [strip_size_suffix(url), url]
        referer = im.get("detail_url") or ("https://%s/" % domain_of(url))

        raw, ctype, err, used = b"", "", None, url
        for cand in candidates:
            wait_for_slot(args.state_dir, domain_of(cand), args.min_delay, args.max_delay)
            raw, ctype, err = http_get_bytes(
                cand, timeout=30, referer=referer,
                accept="image/avif,image/webp,image/*,application/pdf,*/*;q=0.8",
            )
            used = cand
            if not err and not ctype.startswith("text/html") and len(raw) >= 1024:
                break
            # 原图失败则回退到带尺寸后缀的原始 URL，继续下一次循环

        if err or ctype.startswith("text/html") or len(raw) < 1024:
            reason = err or "非图片响应或内容过小(%d 字节)" % len(raw)
            log("[fail] %s -> %s" % (used, reason))
            fail_n += 1
            manifest.append({
                "url": url, "requested_url": used, "local_path": None,
                "type": im.get("type", "未分类"), "ok": False,
                "error": reason, "crawled_at": now_iso(),
            })
            continue

        ext = guess_ext(used, ctype)
        name = "%s%s-%02d%s" % (
            (args.prefix + "-" if args.prefix else ""),
            safe_filename(im.get("type", "img")), idx, ext,
        )
        path = os.path.join(args.dir, name)
        with open(path, "wb") as f:
            f.write(raw)
        ok_n += 1
        log("[ok] %s -> %s (%d 字节)" % (im.get("type", "img"), path, len(raw)))
        manifest.append({
            "url": url, "requested_url": used, "local_path": path,
            "type": im.get("type", "未分类"),
            "is_layout_plan": bool(im.get("is_layout_plan")),
            "has_watermark": True,  # 房产站图片一律视为有水印，不得去除
            "bytes": len(raw), "ok": True, "crawled_at": now_iso(),
        })

    mpath = os.path.join(args.dir, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    log("[done] 成功 %d，失败 %d，清单 -> %s" % (ok_n, fail_n, mpath))
    return 0


if __name__ == "__main__":
    sys.exit(main())
