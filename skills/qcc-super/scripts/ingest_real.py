"""采集真实企业数据入库（从 .env 读 token，走 MCP HTTP）"""
import asyncio, sys, json, os, hashlib
from pathlib import Path

# 读 .env 并注入到 os.environ（不依赖 pydantic）
env_path = Path("f:/src/agent-qcc/.env")
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip())

TOKEN = os.environ["AGENT_QCC_QCC_AUTH_TOKEN"]
print(f"token prefix: {TOKEN[:8]}...")

import httpx
import duckdb
import time
sys.path.insert(0, "f:/src/agent-qcc")
from src.agent_qcc.qcc import parse_sse_stream

DB_PATH = "f:/src/agent-qcc/skills/qcc-super/data/qcc.db"
COMPANY_URL = "https://agent.qcc.com/mcp/company/stream"
RISK_URL = "https://agent.qcc.com/mcp/risk/stream"
TARGET_NAME = sys.argv[1] if len(sys.argv) > 1 else "浙江胶友科技有限公司"
RAW_SUBDIR = sys.argv[2] if len(sys.argv) > 2 else f"raw_{TARGET_NAME}"

def headers(sess=None):
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {TOKEN}",
    }
    if sess:
        h["Mcp-Session-Id"] = sess
    return h

def parse_sse(text):
    from src.agent_qcc.qcc import parse_sse_stream
    return parse_sse_stream(text) or {}

async def init_session(url, cookie_jar):
    """Init MCP session; extract sl-session cookie into cookie_jar dict."""
    async with httpx.AsyncClient(timeout=45.0) as c:
        r = await c.post(url, json={
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qcc-super-ingest", "version": "0.1.0"},
            },
        }, headers=headers())
        # 提取 sl-session cookie
        for cookie_header in r.headers.get_list("set-cookie"):
            if "sl-session=" in cookie_header:
                val = cookie_header.split("sl-session=")[1].split(";")[0]
                cookie_jar["sl-session"] = val
        # 也尝试拿 Mcp-Session-Id（备用）
        sid = r.headers.get("Mcp-Session-Id")
        if sid:
            cookie_jar["Mcp-Session-Id"] = sid
        # Acknowledge initialized
        ack_headers = headers()
        ack_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_jar.items())
        if sid:
            ack_headers["Mcp-Session-Id"] = sid
        await c.post(url, json={
            "jsonrpc": "2.0", "method": "notifications/initialized"
        }, headers=ack_headers)
        return cookie_jar

def cookie_header(jar):
    return "; ".join(f"{k}={v}" for k, v in jar.items() if k != "Mcp-Session-Id")

async def call_tool(url, jar, name, args):
    async with httpx.AsyncClient(timeout=60.0) as c:
        h = headers()
        h["Cookie"] = cookie_header(jar)
        if jar.get("Mcp-Session-Id"):
            h["Mcp-Session-Id"] = jar["Mcp-Session-Id"]
        r = await c.post(url, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }, headers=h)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "body": r.text[:500]}
        ct = r.headers.get("content-type", "")
        data = parse_sse(r.text) if "text/event-stream" in ct else r.json()
        # 调试：打印完整响应
        print(f"  [{name}] raw response: {json.dumps(data, ensure_ascii=False)[:800]}")
        result = data.get("result", data) if isinstance(data, dict) else data
        if isinstance(result, dict) and "content" in result:
            texts = []
            for item in result["content"]:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
            joined = "\n".join(texts).strip()
            try:
                return json.loads(joined)
            except Exception:
                return joined
        return result

async def main():
    print(f"[1/5] Initialize MCP sessions...")
    comp_jar = await init_session(COMPANY_URL, {})
    risk_jar = await init_session(RISK_URL, {})
    print(f"  company cookie: sl-session={'yes' if 'sl-session' in comp_jar else 'NO'}")
    print(f"  risk cookie: sl-session={'yes' if 'sl-session' in risk_jar else 'NO'}")

    print(f"\n[2/5] Call get_company_registration_info...")
    t0 = time.time()
    reg = await call_tool(COMPANY_URL, comp_jar, "get_company_registration_info", {"searchKey": TARGET_NAME})
    print(f"  took {(time.time()-t0)*1000:.0f}ms, keys={list(reg.keys())[:10] if isinstance(reg,dict) else type(reg).__name__}")

    print(f"\n[3/5] Call get_company_profile...")
    t0 = time.time()
    prof = await call_tool(COMPANY_URL, comp_jar, "get_company_profile", {"searchKey": TARGET_NAME})
    print(f"  took {(time.time()-t0)*1000:.0f}ms, keys={list(prof.keys())[:10] if isinstance(prof,dict) else type(prof).__name__}")

    print(f"\n[4/5] Call get_company_risk_scan...")
    t0 = time.time()
    risk = await call_tool(RISK_URL, risk_jar, "get_company_risk_scan", {"searchKey": TARGET_NAME})
    print(f"  took {(time.time()-t0)*1000:.0f}ms, keys={list(risk.keys())[:10] if isinstance(risk,dict) else type(risk).__name__}")

    print(f"\n[5/5] Write to DuckDB...")
    # 保存原始响应（按企业名分区）
    out = Path(f"f:/src/agent-qcc/skills/qcc-super/data/{RAW_SUBDIR}")
    out.mkdir(parents=True, exist_ok=True)
    out.joinpath("registration.json").write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    out.joinpath("profile.json").write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    out.joinpath("risk_scan.json").write_text(json.dumps(risk, ensure_ascii=False, indent=2), encoding="utf-8")

    # 简单提取字段并写库
    def dig(d, *ks, default=None):
        for k in ks:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d

    # USCC（支持中英文字段名）
    uscc = ""
    for key in ("统一社会信用代码", "unifiedSocialCreditCode", "socialCreditCode", "creditCode", "uscc"):
        if isinstance(reg, dict) and reg.get(key):
            uscc = reg[key]
            break
    # 若还拿不到，从响应文本正则兜底
    if not uscc:
        import re
        m = re.search(r'(?:统一社会信用代码|"creditCode"|"uscc")["\s:]*([0-9A-Z]{18})', json.dumps(reg, ensure_ascii=False))
        if m: uscc = m.group(1)
    uscc = uscc or f"ZJ_{hashlib.md5(TARGET_NAME.encode()).hexdigest()[:16].upper()}"

    print(f"  extracted uscc={uscc}")

    conn = duckdb.connect(DB_PATH)
    # 更新/插入
    try:
        conn.execute("DELETE FROM firm_wide WHERE name = ?", [TARGET_NAME])
    except Exception: pass
    
    # 提取字段（支持中英文）
    def pick(d, *keys, default=None):
        for k in keys:
            if isinstance(d, dict) and d.get(k):
                return d[k]
        return default
    
    legal_rep = pick(reg, "法定代表人", "legalRepresentative", "legalRep", default=None)
    business_status = pick(reg, "登记状态", "businessStatus", "regStatus", default="存续")
    company_type = pick(reg, "企业类型", "companyType", "type", default=None)
    industry = pick(prof, "企查查行业", "industry", "国标行业", default=None)
    
    conn.execute("""
        INSERT INTO firm_wide (uscc, name, legal_rep, business_status, company_type,
                               industry, updated_at, raw_registration, raw_profile, raw_risk_scan,
                               mcp_call_count)
        VALUES (?, ?, ?, ?, ?, ?, current_timestamp, ?, ?, ?, 3)
    """, [
        uscc, TARGET_NAME,
        legal_rep,
        business_status,
        company_type,
        industry,
        json.dumps(reg, ensure_ascii=False),
        json.dumps(prof, ensure_ascii=False),
        json.dumps(risk, ensure_ascii=False),
    ])
    conn.commit()
    row = conn.execute("SELECT uscc, name, business_status, mcp_call_count FROM firm_wide WHERE name = ?", [TARGET_NAME]).fetchone()
    print(f"  inserted: {row}")
    conn.close()
    print("\nDONE - 浙江胶友科技有限公司 已入库")

if __name__ == "__main__":
    asyncio.run(main())
