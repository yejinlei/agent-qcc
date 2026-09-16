"""
_common.py - Property Hunter 脚本公共库

仅使用 Python 标准库，无第三方依赖。
提供：真实浏览器 UA 池、按域名限速、带 Referer 的 GET、robots.txt 缓存检查、
      拦截页识别、JSON 平衡括号提取、编码检测。

合规约束（对应 SKILL.md R3/R4/R5）：
  - UA 池只含真实浏览器 UA，禁止伪装搜索引擎爬虫
  - 按域名串行限速，间隔随机
  - 抓取前检查 robots.txt
"""

import gzip
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from datetime import datetime

# ---------------------------------------------------------------- 常量

# 真实浏览器 UA（R5：禁止使用 Googlebot / Baiduspider 等爬虫标识）
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
]

# 风控 / 验证页特征词（命中即判定被拦截）
BLOCK_MARKERS = [
    "验证码", "滑动验证", "安全验证", "访问验证", "请完成验证", "人机验证",
    "访问受限", "请求过于频繁", "操作过于频繁", "请输入验证码",
    "captcha", "robot check", "access denied", "are you a robot",
    "风控", "非法请求", "请开启JavaScript并刷新",
]

# 风控网关 / 反爬跳转（返回 200 但内容只是跳转脚本，比普通空壳页更隐蔽）
GATEWAY_MARKERS = [
    "antibot", "verifycode", "xxzlGateway", "callback.58.com",
    "captcha.58.com", "passport.58.com", "wafcheck", "/antibot/",
]

# SPA 空壳页特征（内容由 JS 渲染，静态抓取拿到的是空壳，需升级到浏览器渲染 SPA_MARKERS 判定）
SPA_MARKERS = [
    '<div id="app"', '<div id="root"', '<div id="__nuxt"', 'id="__next"',
    'window.__NUXT__', '__NEXT_DATA__', 'chunk-vendors', 'build.js',
    'bundle.js', 'app.js', 'nuxt', 'data-server-rendered',
]

# 自动抓取域名黑名单（用户 2026-09-03 要求：彻底取消这些站的自动抓取路径，
# --no-robots 授权方式一并作废，硬拦截不可覆盖）。
# 人民法院诉讼资产网 robots 全站 Disallow，仅保留「人工取证」：
# 用户自己浏览器打开页面另存 HTML -> extract_page.py --site rmfysszc 本地解析。
DENY_DOMAINS = [
    "rmfysszc.gov.cn",  # 人民法院诉讼资产网
]

DEFAULT_STATE_DIR = ".cache"
ROBOTS_TTL = 24 * 3600
CACHE_TTL = 24 * 3600


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def domain_of(url):
    return urllib.parse.urlparse(url).netloc.lower()


def is_denied_domain(url):
    """判断 URL 是否命中自动抓取黑名单（硬拦截，--no-robots 亦无效）。"""
    dom = domain_of(url)
    return any(dom == d or dom.endswith("." + d) for d in DENY_DOMAINS)


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def state_path(state_dir, name):
    ensure_dir(state_dir)
    return os.path.join(state_dir, name)


# ---------------------------------------------------------------- 限速

def wait_for_slot(state_dir, domain, min_delay=3.0, max_delay=8.0):
    """按域名串行限速：距上次请求不足间隔则 sleep，成功后写入新时间戳。"""
    fp = state_path(state_dir, "lasthit_%s.txt" % re.sub(r"[^a-z0-9._-]", "_", domain))
    now = time.time()
    last = 0.0
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                last = float(f.read().strip() or 0)
        except Exception:
            last = 0.0
    wait = random.uniform(min_delay, max_delay)
    if last > 0:
        remain = (last + wait) - now
        if remain > 0:
            log("[rate-limit] %s 等待 %.1fs" % (domain, remain))
            time.sleep(remain)
    with open(fp, "w", encoding="utf-8") as f:
        f.write("%.3f" % time.time())


# ---------------------------------------------------------------- robots

def robots_allows(url, state_dir, user_agent="*", timeout=15):
    """
    检查 robots.txt。返回 (allowed: bool, note: str)

    判定原则（保守合规，但避免误杀可用政务源）：
      - robots.txt 不可达（异常）           → 视为允许
      - robots.txt 返回非 200（如 403/406） → 视为允许（政务站常见，按"未声明"处理，从严限速）
      - robots.txt 内容非标准（不含 UA/Disallow/Allow/Sitemap 任一关键字）
                                          → 视为允许
      - 仅当真正拿到合法 robots 且明确 Disallow 时 → 禁止
    这样既遵守了"明确禁止就禁止"，又不会因站点 robots 异常而把整站判死。
    """
    parsed = urllib.parse.urlparse(url)
    robots_url = "%s://%s/robots.txt" % (parsed.scheme, parsed.netloc)
    dom = domain_of(url)
    cache = state_path(state_dir, "robots_%s.txt" % re.sub(r"[^a-z0-9._-]", "_", dom))
    body = None
    if os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) < ROBOTS_TTL:
        with open(cache, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
    else:
        try:
            status, _headers, raw = http_get(
                robots_url, timeout=timeout,
                extra_headers={"Accept": "text/plain, */*"},
            )
            if status != 200:
                return True, "robots.txt 返回 HTTP %s，按惯例视为允许" % status
            body = raw
            with open(cache, "w", encoding="utf-8") as f:
                f.write(body)
        except Exception as e:
            return True, "robots.txt 不可达(%s)，按惯例视为允许" % type(e).__name__

    # 内容合法性校验：像样的 robots 至少含 user-agent / disallow / allow / sitemap 之一
    if not re.search(r"(?im)^\s*(user-agent|disallow|allow|sitemap)\b", body or ""):
        return True, "robots.txt 内容异常/非标准，按惯例视为允许"

    rp = urllib.robotparser.RobotFileParser()
    rp.parse(body.splitlines())
    path = parsed.path or "/"
    if parsed.query:
        path = path + "?" + parsed.query
    try:
        ok = rp.can_fetch(user_agent, path)
    except Exception:
        return True, "robots.txt 解析失败，视为允许"
    return ok, ("robots.txt 允许" if ok else "robots.txt 禁止抓取该路径")


# ---------------------------------------------------------------- HTTP

def decode_bytes(raw, header_charset=None):
    if header_charset:
        try:
            return raw.decode(header_charset, errors="replace")
        except Exception:
            pass
    m = re.search(rb'<meta[^>]+charset=["\']?\s*([\w-]+)', raw[:4096], re.I)
    if m:
        try:
            return raw.decode(m.group(1).decode("ascii", "ignore"), errors="replace")
        except Exception:
            pass
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def http_get(url, timeout=20, referer=None, extra_headers=None, accept="text/html"):
    """
    带 UA / Referer / gzip 解压的 GET。返回 (status, headers, text)
    """
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            enc = resp.headers.get("Content-Encoding", "").lower()
            if "gzip" in enc:
                raw = gzip.decompress(raw)
            elif "deflate" in enc:
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            charset = resp.headers.get_content_charset()
            return resp.status, dict(resp.headers), decode_bytes(raw, charset)
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read()
        except Exception:
            pass
        return e.code, dict(e.headers or {}), decode_bytes(raw)
    except Exception as e:
        return 0, {}, "[NETWORK_ERROR] %s: %s" % (type(e).__name__, e)


def http_get_bytes(url, timeout=30, referer=None, accept="*/*", extra_headers=None):
    """
    GET 返回原始字节（下载图片/PDF 用）。返回 (raw_bytes, content_type, error)
    """
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    }
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get("Content-Type", ""), None
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read()
        except Exception:
            pass
        return raw, "", "HTTP %s" % e.code
    except Exception as e:
        return b"", "", "%s: %s" % (type(e).__name__, e)


def classify_response(status, text, min_len=1500):
    """
    响应分类（三态，缺一不可）：
      ("BLOCKED", reason)  —— 被风控/限流，停止并交人工，禁止重试
      ("NEEDS_JS", reason) —— SPA 空壳页，需升级到 agent-browser 渲染，重试静态抓取无意义
      ("OK", "")           —— 正常
    """
    if status in (403, 429, 401):
        return "BLOCKED", "HTTP %s" % status
    if status == 0:
        return "BLOCKED", "网络错误"

    low = (text or "").lower()
    for mk in BLOCK_MARKERS:
        if mk.lower() in low:
            return "BLOCKED", "命中风控特征词：%s" % mk
    for mk in GATEWAY_MARKERS:
        if mk.lower() in low:
            return "BLOCKED", ("命中风控网关（%s）—— 站点要求带真实浏览器指纹的请求，"
                               "静态抓取不可行，需 agent-browser；若仍要求验证则交人工" % mk)

    if len(text or "") < min_len:
        # 短页面：区分「SPA 空壳」与「风控空壳」
        if any(m.lower() in low for m in SPA_MARKERS):
            return "NEEDS_JS", "SPA 空壳页(%d 字符)，内容由 JS 渲染" % len(text or "")
        return "BLOCKED", "页面过短(%d 字符)且无 SPA 特征，疑似风控空壳页" % len(text or "")

    if status >= 500:
        return "BLOCKED", "服务端错误 HTTP %s" % status
    return "OK", ""


def looks_blocked(status, text):
    """兼容旧调用：仅判断是否为 BLOCKED。"""
    kind, why = classify_response(status, text)
    return (kind == "BLOCKED"), why


# ---------------------------------------------------------------- 文本工具

def extract_json_after(text, marker, opener="{"):
    """
    从 marker 之后提取一个平衡的 JSON 对象。返回 (obj, end_index) 或 (None, -1)
    """
    idx = text.find(marker)
    if idx < 0:
        return None, -1
    start = text.find(opener, idx + len(marker))
    if start < 0:
        return None, -1
    depth = 0
    in_str = False
    esc = False
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    blob = text[start:i + 1]
                    try:
                        return json.loads(blob), i + 1
                    except Exception:
                        return None, i + 1
        i += 1
    return None, -1


def dig(obj, path):
    """按 'a.b.0.c' 路径取值，取不到返回 None。"""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def now_iso():
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def safe_filename(s, maxlen=80):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(s or "")).strip()
    s = re.sub(r"\s+", "_", s)
    return s[:maxlen] or "unnamed"


def cache_fresh(path, ttl=CACHE_TTL):
    return os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl
