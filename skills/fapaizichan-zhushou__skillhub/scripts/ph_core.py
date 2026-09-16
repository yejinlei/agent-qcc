# -*- coding: utf-8 -*-
"""
ph_core.py —— Property Hunter 准确度核心库（2026-09-07 新增）

解决 2026-09-07 重庆江北/渝北检索暴露的准确度问题：
  1. 标题含 HTML 实体（伟岸&middot;一品城）→ clean_title 统一还原
  2. 外地同名区误判（浙江省宁波市江北区…被当成重庆江北）→ match_district 城市感知
  3. 非住宅漏筛（"5幢负1-4" 实为储藏/车位类）→ is_residential 扩展规则
  4. 同一标的多平台/多页重复 → fingerprint + merge_cross_source
  5. 已结束标的混入候选 → status_from_dates（源字段优先，日期兜底）
  6. 评分维度失效（区域均衡固定加分）→ score() 重设计，缺字段明确扣分

纯标准库，无第三方依赖。可 `--self-test` 自检。
"""
import hashlib
import html as _html
import re
from datetime import datetime

# ---------------------------------------------------------------- 文本清洗

ENTITY_FIX = {
    "&middot;": "·", "&nbsp;": " ", "&amp;": "&", "&hellip;": "…",
    "&ldquo;": "“", "&rdquo;": "”", "&mdash;": "—",
}

def clean_title(t):
    if not t:
        return ""
    s = _html.unescape(str(t))
    for k, v in ENTITY_FIX.items():
        s = s.replace(k, v)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------- 区域匹配（城市感知）

# 常见外省市（用于排除"同名区"误判：如 宁波市江北区 ≠ 重庆市江北区）
OTHER_CITIES = [
    "北京", "上海", "天津", "南京", "杭州", "广州", "深圳", "成都", "武汉", "西安",
    "宁波", "苏州", "无锡", "青岛", "大连", "厦门", "长沙", "郑州", "昆明", "贵阳",
    "南宁", "福州", "合肥", "南昌", "济南", "太原", "石家庄", "沈阳", "长春",
    "哈尔滨", "兰州", "海口", "三亚", "株洲", "来宾", "柳州", "桂林", "唐山",
    "常州", "南通", "徐州", "温州", "佛山", "东莞", "珠海", "中山", "合肥", "洛阳",
    "泉州", "嘉兴", "金华", "绍兴", "台州", "扬州", "盐城", "潍坊", "烟台", "淄博",
]

CITY_ALIAS = {
    "重庆": ["重庆", "重庆市", "渝"],
    "北京": ["北京", "北京市", "京"],
    "上海": ["上海", "上海市", "沪"],
}

def match_district(title, districts, city=None):
    """
    城市感知的区县匹配。返回 (district|None, reason)
      - 命中目标区县 + 城市一致 → (district, "ok")
      - 命中外地城市名且非目标城市 → (None, "外地同名区")
      - 无城市前缀但只有一个目标区县命中 → (district, "ok(无城市前缀)")
    """
    t = clean_title(title)
    if not t:
        return None, "空标题"
    # 外地城市优先排除
    if city and city != "重庆":  # 目前主要服务重庆，逻辑保持通用
        pass
    hit_other = None
    for c in OTHER_CITIES:
        if c in t:
            hit_other = c
            break
    is_target_city = False
    if city:
        aliases = CITY_ALIAS.get(city, [city])
        is_target_city = any(a in t for a in aliases)
    elif "重庆" in t:
        is_target_city = True
    # 无城市前缀的简写标题（如"渝北区龙溪街道…"）也视为目标城市
    if not is_target_city and not hit_other:
        is_target_city = True  # 未出现任何城市名 → 不排除
    if hit_other and not is_target_city:
        return None, f"外地同名区({hit_other})"
    for d in districts:
        short = d[:-1] if d.endswith("区") else d
        if d in t or (f"{short}区" in t):
            return d, "ok"
    return None, "未命中目标区县"


# ---------------------------------------------------------------- 用途判定（住宅）

NON_RESIDENTIAL_KW = [
    # 车位/储藏
    "车位", "车库", "停车位", "储藏室", "杂物间", "设备房", "架空层",
    # 商业/办公/产业
    "商业", "商铺", "门面", "营业房", "办公", "写字楼", "厂房", "仓储", "仓库",
    "土地", "林地", "厂房", "工业", "酒店", "宾馆", "市场", "加油站",
    # 负层（法拍标题常见"5幢负1-4"，多为储藏/车位类，非住宅）
    "负1-", "负2-", "负3-", "负一层", "负二层", "地下室", "地下层",
]
RESIDENTIAL_HINT = ["房屋", "房产", "住宅", "住房", "成套住宅", "住宅用房", "房屋及", "房屋（", "房"]

def is_residential(title):
    """住宅判定：命中非住宅关键词即否；无住宅提示词且带负层/车位特征也否。"""
    t = clean_title(title)
    for k in NON_RESIDENTIAL_KW:
        if k in t:
            return False, f"非住宅关键词「{k}」"
    if re.search(r"负\s?\d\s?-", t):
        return False, "负层标的（储藏/车位类）"
    if not any(h in t for h in RESIDENTIAL_HINT):
        return False, "无住宅特征词"
    return True, "ok"


# ---------------------------------------------------------------- 状态判定

def status_from_dates(src_status=None, start_time=None, end_time=None, now=None):
    """
    统一状态：已结束 / 竞价中 / 即将开始 / 挂牌中 / 未获取
    Ali: status in (doing, todo, done); cquae: publicStatus=PUBLICING + listEndDate
    """
    now = now or datetime.now()
    st = (src_status or "").strip()
    mapping = {
        "doing": "竞价中", "todo": "即将开始", "done": "已结束",
        "PUBLICING": "挂牌中", "pending": "即将开始", "bidding": "竞价中",
        "done": "已结束", "end": "已结束", "成交": "已结束", "流拍": "已结束",
    }
    state = mapping.get(st, "")
    if not state:
        state = "挂牌中" if st else "未获取"
    # 日期兜底：结束时间早于当前 → 已结束（覆盖源状态缺失/滞后）
    if end_time:
        try:
            et = datetime.strptime(end_time[:16], "%Y-%m-%d %H:%M")
            if et < now and state not in ("已结束",):
                state = "已结束"
        except Exception:
            pass
    return state


def is_active(state):
    return state in ("竞价中", "即将开始", "挂牌中")


# ---------------------------------------------------------------- 指纹与跨平台去重

def _norm_addr(title):
    t = clean_title(title)
    t = t.replace("重庆市", "").replace("重庆", "")
    t = re.sub(r"【[一二三]拍】", "", t)
    t = re.sub(r"（?分零）?|（?分包）?|（?整体）?", "", t)
    t = re.sub(r"房屋及?室内.*$", "", t)
    t = re.sub(r"[及含]可?移?动?家具家电.*$", "", t)
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[（）()・·、，,。．.「」【】\[\]]", "", t)
    return t

def fingerprint(title, price_wan=None):
    base = _norm_addr(title)
    key = f"{base}|{round(price_wan, 1) if price_wan else ''}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]

def addr_key(title):
    """跨平台合并用的地址键：取归一化地址前 24 字（含小区+楼栋+房号）"""
    return _norm_addr(title)[:24]

def merge_cross_source(items, price_tol=0.06):
    """
    跨平台合并：地址键相同且价格差 < tol 视为同一标的。
    返回合并后列表，merged_from 记录各来源。
    """
    groups = {}
    for it in items:
        k = addr_key(it.get("title", ""))
        p = it.get("start_price_wan")
        hit = None
        for g in groups.get(k, []):
            gp = g.get("start_price_wan")
            if gp and p and abs(gp - p) / max(gp, p) < price_tol:
                hit = g
                break
        if hit is None:
            groups.setdefault(k, []).append(it)
        else:
            hit.setdefault("merged_from", [])
            hit["merged_from"].append({
                "source": it.get("source"), "url": it.get("source_url"),
                "price_wan": p, "status": it.get("status"),
            })
    out = []
    for k, v in groups.items():
        out.extend(v)
    return out


# ---------------------------------------------------------------- 评分（重设计）

def score(item, budget_total=70.0):
    """
    100 分制。相较 2026-09-07 旧版修正：
      - 删除"区域均衡"的固定 +10（对所有标的同额加分 = 无效维度）
      - 缺评估价（折价率未知）明确扣分，而不是给中性分
      - 已结束标的直接归零
    """
    if not is_active(item.get("status")):
        return 0
    s = 0
    sp = item.get("start_price_wan") or 0
    cost_hi = (item.get("est_cost_wan") or [0, 0])[1] or sp * 1.10
    # 1 价格/预算匹配 30
    if cost_hi <= budget_total:
        s += 22
        if cost_hi <= budget_total * 0.8: s += 8
        elif cost_hi <= budget_total * 0.92: s += 5
        else: s += 2
    else:
        s += 6
    # 2 折价空间 25（无评估价 → 8 分，明确劣后）
    d = item.get("discount_pct")
    if d is None: s += 8
    elif d <= 57: s += 25
    elif d <= 71: s += 18
    elif d <= 85: s += 10
    else: s += 2
    # 3 可操作性 20（竞价中 > 即将开始 > 挂牌中；临近截止加权）
    st = item.get("status")
    s += {"竞价中": 14, "即将开始": 11, "挂牌中": 9}.get(st, 5)
    end = (item.get("end_time") or "")[:10]
    if end:
        try:
            days = (datetime.strptime(end, "%Y-%m-%d") - datetime.now()).days
            if 0 <= days <= 7: s += 6
            elif days <= 21: s += 3
        except Exception:
            pass
    # 4 来源可信度 10
    src = item.get("source", "")
    s += 10 if "联交所" in src else (8 if "阿里" in src else 5)
    # 5 信息完整度 15
    if item.get("eval_price_wan"): s += 6
    if item.get("area_sqm"): s += 4
    if item.get("deposit_wan"): s += 2
    if item.get("source_url"): s += 3
    return min(s, 100)


# ---------------------------------------------------------------- 自检

def _self_test():
    cases = [
        ("浙江省宁波市江北区宁馨园52号5、6、7楼三层房地产", ["江北区"], "重庆", None),
        ("重庆市江北区建新西路399号1幢3-3", ["江北区", "渝北区"], "重庆", "江北区"),
        ("渝北区龙溪街道荷塘路55号琼海花园1幢B-12-2", ["江北区", "渝北区"], "重庆", "渝北区"),
        ("重庆市渝北区胡杨路31号5幢负1-4", ["渝北区"], "重庆", "渝北区"),
    ]
    ok = True
    for title, districts, city, expect in cases:
        d, why = match_district(title, districts, city)
        flag = "OK " if d == expect else "FAIL"
        if d != expect: ok = False
        print(f"[{flag}] {title[:26]} -> {d} ({why})")
    res = [
        ("重庆市江北区洋河一村57号9-4房产", True),
        ("重庆市渝北区胡杨路31号5幢负1-4", False),
        ("渝北区食品城西路10号负2-152车位转让", False),
        ("重庆市江北区建新北路9号14-1办公用房", False),
    ]
    for title, expect in res:
        r, why = is_residential(title)
        flag = "OK " if r == expect else "FAIL"
        if r != expect: ok = False
        print(f"[{flag}] 住宅判定 {title[:30]} -> {r} ({why})")
    print("\nSELF-TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    print(__doc__)
