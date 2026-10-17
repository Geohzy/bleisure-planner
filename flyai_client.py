"""飞猪 flyai CLI 薄封装：POI / 酒店搜索，统一归一化输出。"""
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def _run(*args, timeout=120):
    cmd = ["npx", "flyai", *args]
    if os.name == "nt":  # Windows 上 npx 是 .cmd，需经 cmd 解析
        cmd = ["cmd", "/c", *cmd]
    out = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=HERE, timeout=timeout,
    ).stdout.strip()
    data = json.loads(out[out.find("{"):])
    if data.get("status") != 0:
        raise RuntimeError(f"flyai 调用失败: {data.get('message')}")
    return data["data"]["itemList"]


def _f(x):
    try:
        return float(x) if x else None
    except (TypeError, ValueError):
        return None


def search_poi(city, category=None, keyword=None):
    args = ["search-poi", "--city-name", city]
    if category:
        args += ["--category", category]
    if keyword:
        args += ["--keyword", keyword]
    out = []
    for it in _run(*args):
        out.append({
            "name": it.get("name"),
            "address": it.get("address") or "",
            "lat": _f(it.get("latitude")),
            "lng": _f(it.get("longitude")),
            "pic": it.get("mainPic"),
            "url": it.get("jumpUrl"),
            "free": it.get("freePoiStatus"),  # FREE / NOT_FREE / UNKNOWN
            "desc": (it.get("description") or "")[:100],
        })
    return out


def search_poi_multi(city, queries):
    """多路 POI 抓取并按名称去重。

    queries 元素两种形式：
      - "人文古迹"  → 走 --category（中文类目名，见 CLI 校验白名单；
                      注意 --help 里写的英文 slug 实测不认，用中文）
      - "kw:夜市"   → 走 --keyword（名称关键词，兜底美食/夜景/咖啡；
                      keyword 路线待真实 Key 验证）
    单路失败只打印警告、不中断整轮抓取。
    """
    seen, out = set(), []
    for q in queries:
        try:
            if q.startswith("kw:"):
                pois = search_poi(city, keyword=q[3:])
            else:
                pois = search_poi(city, category=q)
        except Exception as e:
            print(f"   ⚠ 查询 {q} 失败，已跳过：{e}")
            continue
        for p in pois:
            if p["name"] and p["name"] not in seen:
                seen.add(p["name"])
                p["src"] = q  # 记下这条 POI 是哪个查询抓到的，供偏好排序用
                out.append(p)
    return out


# 默认抓取清单：中文类目走 --category；美食/夜景/咖啡无对应类目，走 kw: 关键词兜底
DEFAULT_QUERIES = [
    "人文古迹",
    "地标建筑",
    "博物馆",
    "园林花园",
    "市集",        # 河坊街这类
    "文创街区",
    "kw:夜市",     # 夜景/美食兜底
    "kw:美食街",
    "kw:咖啡",
]

# 用户偏好 → 对应查询词。选中的偏好会被提到抓取清单最前面。
PREFERENCE_QUERIES = {
    "美食": ["kw:美食街", "kw:夜市", "市集"],
    "夜景": ["kw:夜市"],
    "文化": ["人文古迹", "博物馆", "文创街区"],
    "放松": ["园林花园", "kw:咖啡"],
}


def build_queries(preferences=None):
    """把偏好（美食/夜景/文化/放松，可多选）映射成有顺序的查询词列表。

    偏好相关的查询排在最前，其余按 DEFAULT_QUERIES 补在后头；没给偏好就是默认顺序。
    """
    out = []
    for pref in preferences or []:
        for q in PREFERENCE_QUERIES.get(pref, []):
            if q not in out:
                out.append(q)
    for q in DEFAULT_QUERIES:
        if q not in out:
            out.append(q)
    return out


def search_hotel(dest, keywords=None):
    args = ["search-hotel", "--dest-name", dest]
    if keywords:
        args += ["--key-words", keywords]
    out = []
    for it in _run(*args):
        out.append({
            "name": it.get("name"),
            "address": it.get("address") or "",
            "lat": _f(it.get("latitude")),
            "lng": _f(it.get("longitude")),
            "price": it.get("price"),  # 体验模式下为 ¥2xx 脱敏价
            "url": it.get("jumpUrl"),
            "pic": it.get("mainPic"),
        })
    return out
