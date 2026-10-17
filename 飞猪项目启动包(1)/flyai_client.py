"""飞猪 flyai CLI 薄封装：POI / 酒店搜索，统一归一化输出。"""
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def _run(*args, timeout=120):
    out = subprocess.run(
        ["npx", "flyai", *args],
        capture_output=True, text=True, cwd=HERE, timeout=timeout,
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
                out.append(p)
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
