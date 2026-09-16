#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fetch_page.py - 合规抓取单个页面

内置：robots.txt 检查 / 按域名随机限速 / 真实浏览器 UA / 24h 本地缓存 /
      重试退避 / 风控页识别。

用法：
  python fetch_page.py --url "https://bj.ke.com/ershoufang/xxx.html" --out raw/detail.html
  python fetch_page.py --url "..." --out raw/d.html --min-delay 5 --max-delay 10   # 政务站从严
  python fetch_page.py --url "..." --out raw/d.html --force                        # 忽略 24h 缓存

退出码：
  0 成功
  2 被拦截（403/429/验证码/风控页）—— 需人工处理，禁止绕过
  3 robots.txt 禁止抓取该路径，或命中自动抓取域名黑名单（--no-robots 亦无效）
  4 网络失败或重试耗尽
  6 NEEDS_JS —— SPA 空壳页，静态抓取无效，需升级到 agent-browser 渲染
"""
import argparse
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (  # noqa: E402
    http_get, classify_response, wait_for_slot, robots_allows, ensure_dir,
    domain_of, is_denied_domain, log, cache_fresh,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True, help="原始 HTML 落盘路径")
    ap.add_argument("--min-delay", type=float, default=3.0)
    ap.add_argument("--max-delay", type=float, default=8.0)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--referer", default=None)
    ap.add_argument("--state-dir", default=".cache", help="限速/robots 缓存目录")
    ap.add_argument("--force", action="store_true", help="忽略 24h 缓存")
    ap.add_argument("--no-robots", action="store_true",
                    help="跳过 robots 检查（已停用场景：黑名单域名无论是否加此参数一律拦截）")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--backoff", type=int, default=30,
                    help="重试基础退避秒数（默认 30s，按 2 的倍数递增）")
    ap.add_argument("--min-length", type=int, default=1500, dest="min_length",
                    help="低于该字符数视为空壳页（默认 1500）")
    args = ap.parse_args()

    ensure_dir(os.path.dirname(os.path.abspath(args.out)))
    dom = domain_of(args.url)

    # 0) 域名黑名单硬拦截（用户要求彻底取消自动抓取路径的站点，--no-robots 亦无效）
    if is_denied_domain(args.url):
        log("[denied] %s 在自动抓取黑名单中（该路径已按用户要求取消），拒绝请求。" % dom)
        log("[action] 仅支持人工取证：你自己在浏览器打开页面 -> Ctrl+S 另存 HTML ->")
        log("[action] 再用 extract_page.py 本地解析（不发任何网络请求）。")
        print("DOMAIN_DENIED")
        return 3

    # 1) 缓存命中
    if not args.force and cache_fresh(args.out):
        log("[cache] 命中本地缓存，跳过请求：%s" % args.out)
        print("CACHED")
        return 0

    # 2) robots 检查
    if not args.no_robots:
        allowed, note = robots_allows(args.url, args.state_dir)
        log("[robots] %s -> %s" % (args.url, note))
        if not allowed:
            log("[blocked] robots.txt 明确禁止抓取该路径，放弃。请改用其他渠道。")
            print("ROBOTS_DISALLOW")
            return 3

    # 3) 限速 + 请求
    #    仅对网络错误/5xx 退避重试；403/429 与 SPA 空壳页重试毫无意义，直接返回。
    status, headers, text = 0, {}, ""
    kind, why = "BLOCKED", "未发起请求"
    for attempt in range(args.retries + 1):
        if attempt > 0:
            backoff = args.backoff * (2 ** (attempt - 1))
            log("[retry] 第 %d 次重试，退避 %ds" % (attempt, backoff))
            time.sleep(backoff)
        wait_for_slot(args.state_dir, dom, args.min_delay, args.max_delay)
        status, headers, text = http_get(
            args.url, timeout=args.timeout, referer=args.referer
        )
        kind, why = classify_response(status, text, min_len=args.min_length)
        if kind == "OK":
            break
        if kind == "NEEDS_JS":
            log("[needs-js] %s —— %s" % (args.url, why))
            log("[action] 静态抓取对该页无效。请改用 agent-browser 渲染页面后导出 DOM，")
            log("[action] 或让用户提供页面快照（人工取证模式）。不要重复静态请求。")
            print("NEEDS_JS: %s" % why)
            return 6
        if status in (403, 429, 401):
            log("[blocked] HTTP %s，不重试（重试会加剧风控）" % status)
            break

    # 4) 结果判定
    kind, why = classify_response(status, text, min_len=args.min_length)
    if kind != "OK":
        if status == 0:
            log("[fail] %s —— 网络错误：%s" % (args.url, why))
            print("NETWORK_FAIL: %s" % why)
            return 4
        log("[blocked] %s —— %s" % (args.url, why))
        log("[action] 请人工在浏览器中确认（可能需要过验证或登录），或使用其他渠道。")
        log("[action] 严禁绕过验证码 / 逆向签名 / 使用打码平台。")
        print("BLOCKED: %s" % why)
        return 2

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    log("[ok] HTTP %s, %d 字符 -> %s" % (status, len(text), args.out))
    print("OK %s %d" % (args.out, len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
