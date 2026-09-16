# -*- coding: utf-8 -*-
"""
gen_reports.py —— 双版本报告渲染器（report.md + report.html，同源同内容）

用法：
  python scripts/gen_reports.py --listings listings.json --out-dir <dir> [--config req.json]
或作为库：render(listings_path, md_path, html_path, cfg)
"""
import argparse
import html
import json
import os
import sys
from collections import Counter

CSS = """
:root{--fg:#1f2933;--muted:#5f6b76;--line:#e4e7eb;--bg:#f7f9fa;--card:#fff;--blue:#155e9c;--red:#c0392b;--green:#1e8449}
*{box-sizing:border-box}
body{font-family:"Microsoft YaHei",-apple-system,sans-serif;color:var(--fg);background:var(--bg);margin:0;padding:0 0 60px;line-height:1.6}
.wrap{max-width:1200px;margin:0 auto;padding:0 16px}
header{background:linear-gradient(135deg,#0f3a5f,#155e9c);color:#fff;padding:26px 0 20px;margin-bottom:18px}
header h1{margin:0 0 6px;font-size:21px}
header p{margin:0;opacity:.92;font-size:13px}
.banner{background:#fff8e1;border:1px solid #f0d47a;color:#7a5b00;padding:10px 14px;border-radius:8px;margin:14px 0;font-size:14px}
h2{font-size:18px;border-left:4px solid var(--blue);padding-left:10px;margin:28px 0 12px}
h3{font-size:15px;margin:18px 0 8px;color:var(--blue)}
table{border-collapse:collapse;width:100%;background:var(--card);font-size:13px;border-radius:8px;overflow:hidden}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
thead th{background:#eef3f8;position:sticky;top:0;white-space:nowrap}
.num{white-space:nowrap;font-variant-numeric:tabular-nums}
.t{min-width:220px}
.warn{color:var(--red);font-weight:600}
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;color:#fff;margin-right:4px;white-space:nowrap}
.t-fa{background:#c0392b}.t-gz{background:#155e9c}.t-sh{background:#7d3c98}.t-q{background:#5f6b76}.t-s{background:#1e8449}
.scroll{overflow-x:auto;border-radius:8px}
.kv th{background:#f4f6f8;width:110px;white-space:nowrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}
.card h4{margin:0 0 8px;font-size:15px}
.badges{margin-bottom:10px}
.risk{color:var(--red);font-size:12px;margin:8px 0 0}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
.kpi{flex:1;min-width:140px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;text-align:center}
.kpi b{display:block;font-size:23px;color:var(--blue)}
.kpi span{font-size:12px;color:var(--muted)}
.note{background:#eef6ee;border:1px solid #bcd9bc;color:#22531f;padding:10px 14px;border-radius:8px;font-size:13px;margin:10px 0}
ul{line-height:1.85}
a{color:var(--blue)}
@media(max-width:700px){.t{min-width:170px}table{font-size:12px}.kpi b{font-size:19px}}
"""


def fnum(x, nd=2):
    if x is None:
        return "未获取"
    return f"{x:,.{nd}f}" if isinstance(x, (int, float)) else str(x)


def esc(x):
    return html.escape(str(x)) if x is not None else "未获取"


def table_rows(items, start=1):
    out = []
    for r, it in enumerate(items, start):
        w = ' class="warn"' if it.get("price_note") else ""
        d = f"{it['discount_pct']}%" if it.get("discount_pct") is not None else "—"
        cost = (f"{fnum(it['est_cost_wan'][0])}–{fnum(it['est_cost_wan'][1])}"
                if it.get("est_cost_wan") else "—")
        out.append(
            f'<tr><td>{r}</td><td class="t">{esc(it["title"])}</td><td>{esc(it["district"])}</td>'
            f'<td><span class="tag {tag_cls(it["listing_type"])}">{esc(it["listing_type"])}</span></td>'
            f'<td class="num">{fnum(it["start_price_wan"])}</td>'
            f'<td class="num">{fnum(it["eval_price_wan"])}</td>'
            f'<td class="num"{w}>{d}{"⚠️" if it.get("price_note") else ""}</td>'
            f'<td class="num">{cost}</td><td>{esc(it["status"])}</td>'
            f'<td>{esc((it.get("end_time") or "")[:10])}</td>'
            f'<td><a href="{esc(it["source_url"])}" target="_blank">链接</a></td></tr>')
    return "\n".join(out)


def tag_cls(t):
    return {"法拍": "t-fa", "国资": "t-gz", "社会拍卖": "t-sh"}.get(t, "t-q")


def md_row(r, it):
    cost = (f"{fnum(it['est_cost_wan'][0])}–{fnum(it['est_cost_wan'][1])}"
            if it.get("est_cost_wan") else "—")
    d = f"{it['discount_pct']}%" if it.get("discount_pct") is not None else "—"
    return (f"| {r} | {it['title'][:48]} | {it['district']} | {it['listing_type']} | "
            f"{fnum(it['start_price_wan'])} | {fnum(it['eval_price_wan'])} | {d} | {cost} | "
            f"{it['status']} | {(it.get('end_time') or '')[:10]} | [详情]({it['source_url']}) |")


def card_md(idx, it):
    L = [f"#### {idx}. {it['title']}",
         f"- **类型**：{it['listing_type']}（{it['source'].split('(')[0]}） | **区域**：{it['district']}",
         f"- **起拍/挂牌价**：{fnum(it['start_price_wan'])} 万元 | **评估价**：{fnum(it['eval_price_wan'])} 万元"
         + (f" | **折价率**：{it['discount_pct']}%" if it.get("discount_pct") else "")
         + (f" ⚠️ {it['price_note']}" if it.get("price_note") else ""),
         f"- **状态**：{it['status']} | **截止**：{(it.get('end_time') or '')[:16]}"]
    if it.get("est_cost_wan"):
        L.append(f"- **综合成本预估**：{fnum(it['est_cost_wan'][0])} – {fnum(it['est_cost_wan'][1])} 万元（含税费预估，未含欠费/清场）")
    L.append(f"- **轮次**：{it.get('round_guess') or '未获取'} | **评分**：{it['score']}")
    if it.get("seller"):
        L.append(f"- **转让方**：{it['seller']}")
    if it.get("deposit_wan"):
        L.append(f"- **保证金**：{fnum(it['deposit_wan'],1)} 万元")
    L.append(f"- **面积/户型**：未获取（须实地看样/阅卷核实）")
    L.append(f"- **详情链接**：{it['source_url']}")
    L.append(f"- **风险提示**：法拍须核实占用/租约/税费承担/欠费；面积户型未核实前切勿直接出价。")
    return "\n".join(L)


def card_html(idx, it):
    return f"""<div class="card"><h4>{idx}. {esc(it['title'])}</h4>
<div class="badges"><span class="tag {tag_cls(it['listing_type'])}">{esc(it['listing_type'])}</span>
<span class="tag t-q">{esc(it['district'])}</span><span class="tag t-s">评分 {it['score']}</span></div>
<table class="kv">
<tr><th>起拍/挂牌价</th><td>{fnum(it['start_price_wan'])} 万元</td><th>评估价</th><td>{fnum(it['eval_price_wan'])} 万元</td></tr>
<tr><th>折价率</th><td>{(str(it['discount_pct'])+'%') if it.get('discount_pct') is not None else '未获取'}{(' ⚠️ '+esc(it['price_note'])) if it.get('price_note') else ''}</td><th>状态</th><td>{esc(it['status'])}</td></tr>
<tr><th>截止</th><td>{esc((it.get('end_time') or '')[:16])}</td><th>轮次</th><td>{esc(it.get('round_guess') or '未获取')}</td></tr>
<tr><th>综合成本预估</th><td colspan="3">{fnum(it['est_cost_wan'][0])} – {fnum(it['est_cost_wan'][1])} 万元</td></tr>
{f"<tr><th>转让方</th><td colspan='3'>{esc(it['seller'])}</td></tr>" if it.get('seller') else ''}
{f"<tr><th>保证金</th><td colspan='3'>{fnum(it['deposit_wan'],1)} 万元</td></tr>" if it.get('deposit_wan') else ''}
<tr><th>面积/户型</th><td colspan="3">未获取（须实地看样/阅卷核实）</td></tr>
<tr><th>详情链接</th><td colspan="3"><a href="{esc(it['source_url'])}" target="_blank">{esc(it['source_url'])}</a></td></tr>
</table><p class="risk">⚠️ 风险提示：法拍须核实占用/租约/税费承担/欠费；面积户型未核实前切勿直接出价。</p></div>"""


def render(listings_path, md_path, html_path, cfg=None):
    cfg = cfg or {}
    D = json.load(open(listings_path, encoding="utf-8"))
    L = D["listings"]
    meta = D.get("meta", {})
    city = cfg.get("city", "")
    dts = cfg.get("districts", [])
    budget = cfg.get("budget_total_wan", 0)
    top_n = cfg.get("top_n", 15)
    tax = cfg.get("tax_rate", 0.10)

    by_type = Counter(i["listing_type"] for i in L)
    by_dist = Counter(i["district"] for i in L)
    by_band = Counter(i["band"] for i in L)
    top = L[:top_n]
    non_ali = [i for i in L if not i["source"].startswith("阿里")]
    # 各区 Top（两区/多区均衡参考）
    per_dist = []
    for d in dts:
        sub = sorted([i for i in L if i["district"] == d], key=lambda x: -x["score"])[:10]
        if sub:
            per_dist.append((d, sub))
    picks = top[:6] + ([sorted(non_ali, key=lambda x: -x["score"])[0]] if non_ali else [])

    dstr = city + "、" .join(dts)
    title = f"{city}{'·'.join(x[:-1] if x.endswith('区') else x for x in dts)} 拍卖房源检索报告"
    head_md = f"""# {title}
**预算口径：总成本（含税费）≤ {budget} 万 | {'住宅' if cfg.get('residential') else '不限用途'} | 法拍 + 国资 + 社会拍卖**

> ⚠️ 注意：如遇收集房源不精确，请直接使用页面末尾的「复问模板」重新提问。

## 一、需求回述与抓取范围
| 项 | 内容 |
|---|---|
| 需求 | {dstr} |
| 预算 | 起拍/挂牌 {cfg.get('price_min_wan','不限')} – {cfg.get('price_max_wan','不限')} 万；总成本（含税费）≤ {budget} 万 |
| 抓取时间 | {meta.get('fetched_at','')} |
| 渠道 | {'；'.join(meta.get('source_notes', [])) or '见下'} |

## 二、结论摘要
**命中 {len(L)} 套**（{' / '.join(f'{k} {v}' for k, v in by_type.items())}）。

**三条关键判断：**
1. A 段（低总价安全区）{by_band.get('A(低总价安全区)',0)} 套、B 段（主力）{by_band.get('B(主力段)',0)} 套、C 段（临界易超预算）{by_band.get('C(临界,易超预算)',0)} 套——**优先 A/B 段**。
2. 区域分布：{' / '.join(f'{k} {v}' for k, v in by_dist.items())}。
3. 法拍折价 ≤57% 的标的多为二拍，安全边际最高；国资/社会拍卖无评估价参照，但无清场不确定性。

## 三、候选清单
### 3.1 综合评分 Top {top_n}
| # | 标的 | 区域 | 类型 | 起拍/挂牌(万) | 评估(万) | 折价 | 成本预估(万) | 状态 | 截止 | 链接 |
|---|---|---|---|---|---|---|---|---|---|---|"""
    rows_md = [md_row(r, it) for r, it in enumerate(top, 1)]
    sec_md = []
    for d, sub in per_dist:
        sec_md.append(f"\n### {d} Top {len(sub)}（区域均衡参考）\n"
                      "| # | 标的 | 区域 | 类型 | 起拍/挂牌(万) | 评估(万) | 折价 | 成本预估(万) | 状态 | 截止 | 链接 |\n|---|---|---|---|---|---|---|---|---|---|---|\n"
                      + "\n".join(md_row(r, it) for r, it in enumerate(sub, 1)))
    if non_ali:
        sec_md.append(f"\n### 国资/社会拍卖候选（{len(non_ali)} 条）\n"
                      "| # | 标的 | 区域 | 类型 | 起拍/挂牌(万) | 评估(万) | 折价 | 成本预估(万) | 状态 | 截止 | 链接 |\n|---|---|---|---|---|---|---|---|---|---|---|\n"
                      + "\n".join(md_row(r, it) for r, it in enumerate(sorted(non_ali, key=lambda x: -x["score"]), 1)))
    cards_md = "\n## 四、逐套详情卡\n\n" + "\n\n".join(
        card_md(i, it) for i, it in enumerate(picks, 1))
    tail_md = f"""
## 五、被淘汰但值得说的
1. C 段（起拍接近预算上限）标的竞价+税费后几乎必然超 {budget} 万，建议盯流拍后的三拍/变卖节点。
2. 折价率 100%（起拍价=评估价）的异常标的已原样保留并标注 ⚠️，需核对法院公告，不做"智能修正"。
3. 非住宅类（车位/商铺/办公/负层储藏等）已按标题规则剔除，不纳入候选。

## 六、数据可信度声明
- 采集源与命中：{'；'.join(meta.get('source_notes', [])) or '—'}
- 共性缺口：面积、户型、楼层、法院/转让细节在列表层缺失，**必须逐套核实**；低价段多为老小区小户型，若有明确户型要求应优先中段价位标的。
- 税费口径：按成交价 5%–{tax:.0%} 预估综合税费（买受人承担口径），实际以法院公告/交易文件为准。
- 时效：抓取于 {meta.get('fetched_at','')}，法拍数据变化快，超过 7 天须复核；所有价格为源站原文，未做任何"智能修正"。

## 七、法拍房三条硬提示（参拍前必读）
1. **法院只对权利现状负责，通常不负责清场交付**——参拍前务必实地看样、核实占用/租约。
2. **税费承担看公告**，常见"一切税费由买受人承担"。
3. **资格与限购**：参拍前确认购房资格与最新政策；法拍贷支持以公告为准。

---
## 八、复问向导（提问模板）
> 提问公式 = **指定源 + 明确筛选条件 + 指定产出 + 是否交叉核验**
1. 源：阿里法拍 / 重庆联交所 / 公拍网（自动抓）
2. 条件：地域（如 {city}{dts[0] if dts else ''}）+ 用途（住宅/商铺/车位）+ 价格（起拍 X-Y 万 / 总成本 ≤Z 万）+ 户型（90平三房）
3. 产出：只要链接起拍价 / 完整尽调报告 / 导出表格或 HTML
4. 交叉核验：单平台 / 多平台比对同一标的
"""
    md = head_md + "\n" + "\n".join(rows_md) + "\n" + "\n".join(sec_md) + "\n" + cards_md + tail_md
    open(md_path, "w", encoding="utf-8").write(md)

    # ---------- HTML ----------
    kpis = "".join(f'<div class="kpi"><b>{v}</b><span>{esc(k)}</span></div>'
                   for k, v in [("命中总数", len(L)), *by_type.items(), *by_dist.items()])
    secs = ""
    for d, sub in per_dist:
        secs += (f'<h3>{esc(d)} Top {len(sub)}（区域均衡参考）</h3><div class="scroll"><table>'
                 '<thead><tr><th>#</th><th>标的</th><th>区域</th><th>类型</th><th>起拍/挂牌(万)</th>'
                 '<th>评估(万)</th><th>折价</th><th>成本预估(万)</th><th>状态</th><th>截止</th><th>链接</th>'
                 f'</tr></thead><tbody>{table_rows(sub)}</tbody></table></div>')
    if non_ali:
        secs += (f'<h3>国资/社会拍卖候选（{len(non_ali)} 条）</h3><div class="scroll"><table>'
                 '<thead><tr><th>#</th><th>标的</th><th>区域</th><th>类型</th><th>起拍/挂牌(万)</th>'
                 '<th>评估(万)</th><th>折价</th><th>成本预估(万)</th><th>状态</th><th>截止</th><th>链接</th>'
                 f'</tr></thead><tbody>{table_rows(sorted(non_ali, key=lambda x: -x["score"]))}</tbody></table></div>')
    doc = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0"><title>{esc(title)}</title>
<style>{CSS}</style></head><body>
<header><div class="wrap"><h1>{esc(title)}</h1>
<p>预算：总成本（含税费）≤ {budget} 万 ｜ {'住宅' if cfg.get('residential') else '不限用途'} ｜ 法拍+国资+社会拍卖 ｜ 抓取 {meta.get('fetched_at','')}</p></div></header>
<div class="wrap">
<div class="banner">⚠️ 注意：如遇收集房源不精确，请直接使用页面末尾的「复问模板」重新提问。</div>
<h2>二、结论摘要</h2><div class="kpis">{kpis}</div>
<ul><li>A 段（低总价安全区）{by_band.get('A(低总价安全区)',0)} 套、B 段（主力）{by_band.get('B(主力段)',0)} 套、C 段（临界易超预算）{by_band.get('C(临界,易超预算)',0)} 套——<b>优先 A/B 段</b>。</li>
<li>区域分布：{' / '.join(f'{k} {v}' for k, v in by_dist.items())}。</li>
<li>法拍折价 ≤57% 的多为二拍，安全边际最高；国资/社会拍卖无评估价参照但无清场风险。</li></ul>
<h2>三、候选清单</h2><h3>3.1 综合评分 Top {top_n}</h3>
<div class="scroll"><table><thead><tr><th>#</th><th>标的</th><th>区域</th><th>类型</th><th>起拍/挂牌(万)</th><th>评估(万)</th><th>折价</th><th>成本预估(万)</th><th>状态</th><th>截止</th><th>链接</th></tr></thead>
<tbody>{table_rows(top)}</tbody></table></div>{secs}
<h2>四、逐套详情卡</h2>{''.join(card_html(i, it) for i, it in enumerate(picks, 1))}
<h2>五、被淘汰但值得说的</h2>
<ul><li>C 段标的竞价+税费后几乎必然超 {budget} 万，建议盯流拍后的三拍/变卖节点。</li>
<li>折价率 100% 的异常标的已原样保留并标注 ⚠️，需核对法院公告。</li>
<li>非住宅类（车位/商铺/办公/负层储藏等）已按标题规则剔除。</li></ul>
<h2>六、数据可信度声明</h2>
<div class="note">采集源与命中：{'；'.join(meta.get('source_notes', [])) or '—'}<br>
共性缺口：面积、户型、楼层、法院/转让细节在列表层缺失，<b>必须逐套核实</b>。<br>
税费口径：按成交价 5%–{tax:.0%} 预估（买受人承担口径），实际以公告为准。<br>
时效：抓取于 {meta.get('fetched_at','')}，超过 7 天须复核；价格为源站原文，未做"智能修正"。</div>
<h2>七、法拍房三条硬提示</h2>
<ul><li><b>法院只对权利现状负责，通常不负责清场交付</b>——实地看样、核实占用/租约。</li>
<li><b>税费承担看公告</b>，常见"一切税费由买受人承担"。</li>
<li><b>资格与限购</b>：参拍前确认购房资格与最新政策；法拍贷以公告为准。</li></ul>
<h2>八、复问向导（提问模板）</h2>
<div class="note">提问公式 = <b>指定源 + 明确筛选条件 + 指定产出 + 是否交叉核验</b><br>
① 源：阿里法拍 / 重庆联交所 / 公拍网<br>
② 条件：地域 + 用途 + 价格（起拍/总成本）+ 户型<br>
③ 产出：只要链接起拍价 / 完整尽调 / 导出表格 HTML　④ 交叉核验：单平台 / 多平台</div>
</div></body></html>"""
    open(html_path, "w", encoding="utf-8").write(doc)
    return md_path, html_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listings", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8")) if a.config else {}
    m, h = render(a.listings, os.path.join(a.out_dir, "report.md"),
                  os.path.join(a.out_dir, "report.html"), cfg)
    print("[gen_reports] %s / %s" % (m, h))


if __name__ == "__main__":
    sys.exit(main())
