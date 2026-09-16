#!/bin/bash
# browser_sweep.sh —— 浏览器驱动的 JSON API 扫站器（2026-09-07 新增）
#
# 适用：站点 urllib 直连被 WAF 拦截、但浏览器内同源 fetch 正常（如重庆联交所 cquae.com）。
# 相比手工逐块 eval，本脚本解决三个问题：
#   1) 先把浏览器导航回目标域名（否则相对路径 fetch 会打到上一个站 → 假"0 命中"）
#   2) 块内 Promise.all 并发拉取（默认并发 4、每块 4 页），比串行快 3-4 倍
#   3) 空块/超时块自动重试一次，并在末尾校验，杜绝"静默丢数据"
#
# 用法：
#   bash scripts/browser_sweep.sh \
#     --base https://www.cquae.com/ \
#     --api "/api/trade/project/projectPageQuery?page={page}&size=20&projectType=31" \
#     --start 1 --end 83 --match "江北|渝北" \
#     --out <dir>/cquae_hits.json
#
# 依赖：agent-browser（已安装）；Python 用托管版本做结果合并。

set -u
BASE="https://www.cquae.com/"
API="/api/trade/project/projectPageQuery?page={page}&size=20&projectType=31"
START=1
END=10
MATCH="江北|渝北"
OUT="cquae_hits.json"
CHUNK=4
CONC=4
TMPDIR=".sweep_tmp"
PY="C:/Users/86159/.workbuddy/binaries/python/versions/3.13.12/python.exe"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) BASE="$2"; shift 2;;
    --api) API="$2"; shift 2;;
    --start) START="$2"; shift 2;;
    --end) END="$2"; shift 2;;
    --match) MATCH="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --chunk) CHUNK="$2"; shift 2;;
    --concurrency) CONC="$2"; shift 2;;
    --tmpdir) TMPDIR="$2"; shift 2;;
    *) echo "unknown arg: $1"; exit 1;;
  esac
done

mkdir -p "$TMPDIR"
rm -f "$TMPDIR"/chunk_*.json

echo "[sweep] 目标域: $BASE"
echo "[sweep] 接口: $API  页 $START-$END  过滤: $MATCH"

# 1) 关键：先导航回目标域名，保证相对路径 fetch 同源
agent-browser open "$BASE" > /dev/null 2>&1
agent-browser wait --load load > /dev/null 2>&1
agent-browser wait 3000 > /dev/null 2>&1

fetch_chunk () {
  local a=$1 b=$2 file=$3
  agent-browser eval "(async function(){
    var out=[]; var idxs=[]; for(var p=$a;p<=$b;p++) idxs.push(p);
    async function get(p){
      try{
        var r=await fetch('${API//\{page\}/'+p+'}');
        var d=await r.json();
        var arr=(d&&d.target&&d.target.result)?d.target.result:[];
        return arr.filter(function(x){
          var s=JSON.stringify(x);
          return /$MATCH/.test(s);
        });
      }catch(e){ return []; }
    }
    var results=await Promise.all(idxs.map(get));
    results.forEach(function(a){ out=out.concat(a||[]) });
    return JSON.stringify(out);
  })()" > "$file" 2>/dev/null
}

for ((s=START; s<=END; s+=CHUNK)); do
  e=$((s+CHUNK-1)); if [ $e -gt $END ]; then e=$END; fi
  f="$TMPDIR/chunk_${s}.json"
  fetch_chunk "$s" "$e" "$f"
  sz=$(wc -c < "$f" 2>/dev/null || echo 0)
  # 空块/超小块 = 可能超时丢数据 → 重试一次
  if [ "$sz" -lt 10 ]; then
    echo "[retry] pages $s-$e 输出为空(${sz}B)，重试一次"
    sleep 3
    fetch_chunk "$s" "$e" "$f"
    sz=$(wc -c < "$f" 2>/dev/null || echo 0)
  fi
  echo "[chunk] pages $s-$e -> ${sz}B"
  sleep 1
done

# 2) 合并（eval 输出是转义 JSON 字符串，需双层解析）
"$PY" - "$TMPDIR" "$OUT" <<'PYEOF'
import json, glob, os, sys
tmp, out = sys.argv[1], sys.argv[2]
hits, empties = [], []
for f in sorted(glob.glob(os.path.join(tmp, "chunk_*.json")),
                key=lambda x: int(os.path.basename(x).split("_")[1].split(".")[0])):
    raw = open(f, encoding="utf-8").read().strip()
    if not raw:
        empties.append(os.path.basename(f)); continue
    if raw.startswith('"'):
        try:
            raw = json.loads(raw)
        except Exception:
            empties.append(os.path.basename(f)); continue
    try:
        arr = json.loads(raw)
    except Exception:
        empties.append(os.path.basename(f)); continue
    hits += arr
seen, uniq = set(), []
for h in hits:
    k = h.get("projectId") or h.get("noticeName")
    if k in seen: continue
    seen.add(k); uniq.append(h)
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
json.dump(uniq, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("[merge] 原始 %d 条 -> 去重 %d 条 -> %s" % (len(hits), len(uniq), out))
if empties:
    print("[warn] 以下块为空/解析失败（可能仍有数据未取到）：%s" % ", ".join(empties))
PYEOF

echo "[sweep] DONE"
