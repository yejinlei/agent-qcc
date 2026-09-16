#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
property-hunter 采集速度基准测试
对比「旧方案（每页 2 次子进程：fetch_page.py + extract_page.py）」与
「新方案（batch_ali.py 进程内解析 + 缓存）」的纯解析开销差异。

用法：
  python scripts/benchmark.py --sample <sf_p1.html> --iters 15
说明：
  - 网络抓取耗时两边相同（或新方案有缓存收益），此处只隔离“Python 进程/解析开销”，
    这是代码层可优化的部分。
"""
import argparse, io, contextlib, subprocess, sys, time, os

def find_sample():
    # 优先用既有的缓存样本
    cands = [
        r"D:/WorkBuddyData/2026-09-03-19-54-09/outputs/auction-20260907-重庆江北渝北-50-70万/raw/sf_p1.html",
        r"D:/WorkBuddyData/2026-09-03-19-54-09/outputs/_ph_speedtest/run1/raw/sf_p1.html",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default=None, help="阿里列表页 HTML 样本")
    ap.add_argument("--iters", type=int, default=15, help="模拟页数（迭代次数）")
    ap.add_argument("--py", default=sys.executable, help="python 解释器")
    args = ap.parse_args()

    sample = args.sample or find_sample()
    if not sample or not os.path.exists(sample):
        print("[bench] 未找到样本 HTML，请 --sample 指定一个阿里列表页")
        return 2
    print("[bench] 样本: %s  (%d KB)" % (sample, os.path.getsize(sample)//1024))
    print("[bench] 模拟页数 iters=%d\n" % args.iters)

    # ---- 旧方案：每页 2 次子进程 ----
    # 用子进程跑 extract_page.py（等价旧流程的“提取”环节）
    t0 = time.time()
    for _ in range(args.iters):
        subprocess.run([args.py, "scripts/extract_page.py",
                        "--html", sample, "--site", "ali-auction",
                        "--out", os.devnull],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    old_dt = time.time() - t0

    # ---- 新方案：进程内 extract_listings ----
    sys.path.insert(0, "scripts")
    import extract_page
    html = open(sample, encoding="utf-8", errors="replace").read()
    t0 = time.time()
    for _ in range(args.iters):
        extract_page.extract_listings(html, "ali-auction")
    new_dt = time.time() - t0

    # ---- 换算到 40 页整轮 ----
    print("== 纯解析开销（不含网络）==")
    print("旧方案(子进程/页): %.3fs/页  → 40 页 ≈ %.1fs" % (old_dt/args.iters, old_dt/args.iters*40))
    print("新方案(进程内/页): %.3fs/页  → 40 页 ≈ %.1fs" % (new_dt/args.iters, new_dt/args.iters*40))
    speedup = old_dt/new_dt if new_dt else 0
    print("解析加速比: %.1fx" % speedup)

    # 旧方案每页还要额外 1 次 fetch_page.py 子进程（抓取），新方案同进程内抓取
    print("\n== 旧方案全链路（含抓取子进程）==")
    pages = 40
    old_full = old_dt/args.iters*pages  # 仅解析部分；再加等量的抓取子进程
    old_full *= 2  # fetch + extract 各一次子进程
    new_full = new_dt/args.iters*pages
    print("旧方案 40 页：每页 2×子进程 ≈ %.0fs（纯进程开销，不含网络）" % old_full)
    print("新方案 40 页：单进程 ≈ %.0fs（纯进程开销，不含网络）" % new_full)
    if old_full:
        print("进程启动开销节省 ≈ %.0f%%" % (100*(1-new_full/old_full)))
    return 0

if __name__ == "__main__":
    sys.exit(main())
