"""飞猪 flyai CLI 薄封装：POI / 酒店搜索，统一归一化输出。"""
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))

# —— 内存缓存：避免同一 city/queries 重复冷启动 npx flyai（单次冷启 3–6 秒）——
_POI_CACHE = {}            # (city, tuple(queries)) -> (时间戳, [poi])
_POI_CACHE_TTL = 300       # 5 分钟
_POI_CACHE_LOCK = threading.Lock()
_HOTEL_CACHE = {}          # (city, keyword) -> (时间戳, [hotel])

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _run(*args, timeout=120):
    cmd = ["npx", "flyai", *args]
    if os.name == "nt":  # Windows 上 npx 是 .cmd，需经 cmd 解析
        cmd = ["cmd", "/c", *cmd]
    p = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=HERE, timeout=timeout,
    )
    # CLI 的 MCP 负载可能走 stdout 或 stderr，两路都收，再去掉 ANSI 色码
    blob = _ANSI.sub("", f"{p.stdout or ''}\n{p.stderr or ''}")
    s, e = blob.find("{"), blob.rfind("}")
    if s == -1 or e <= s:
        raise RuntimeError(
            f"flyai 调用失败（exit={p.returncode}）：命令没有返回 JSON。"
            "请确认 FLYAI_API_KEY 已配置且未超试用额度。")
    data = json.loads(blob[s:e + 1])
    # 兼容两种返回：① JSON-RPC 信封（成功在 result、错误在 error）
    #              ② 直接负载 {status, message, data:{itemList}}
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise RuntimeError(f"flyai 调用失败：{err.get('message') or err}")
    inner = data.get("result") if (isinstance(data, dict) and data.get("result") is not None) else data
    if not isinstance(inner, dict):
        inner = {}
    status = inner.get("status")
    if status not in (0, None):
        raise RuntimeError(f"flyai 调用失败：{inner.get('message') or inner}")
    items = (inner.get("data") or {}).get("itemList")
    if items is None:
        raise RuntimeError("flyai 调用失败：响应缺少 data.itemList。")
    return items


def _f(x):
    try:
        return float(x) if x else None
    except (TypeError, ValueError):
        return None


# POI 详情里可能携带开放时间的字段名（不同接口/版本命名不一，全部扫一遍）
_OPEN_HOURS_KEYS = (
    "openTime", "open_time", "openingHours", "opening_hours",
    "businessHours", "business_hours", "openHours", "open_hours",
)


def _open_hours(it):
    """尽力从 POI 详情里取开放时间原文；取不到返回 None。

    实测飞猪 search-poi 当前响应不含该字段（ticketInfo 为 null），故此函数是
    "优先取接口真实值" 的入口：一旦接口补充或换字段名即可自动生效，
    不取到时由 planner 按品类典型窗口估算，二者在输出中区分标注。
    """
    ticket = it.get("ticketInfo")
    sources = [it, ticket] if isinstance(ticket, dict) else [it]
    for src in sources:
        for k in _OPEN_HOURS_KEYS:
            v = src.get(k)
            if v:
                return str(v).strip()
    if isinstance(ticket, str) and ticket.strip():
        return ticket.strip()
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
            "category": it.get("category"),   # 用于开放时段规则估算
            "open_hours": _open_hours(it),    # 详情里的真实开放时间；无则为 None
        })
    return out


def search_poi_multi(city, queries):
    """多路 POI 抓取并按名称去重。

    queries 元素两种形式：
      - "人文古迹"  → 走 --category（中文类目名，见 CLI 校验白名单；
                      注意 --help 里写的英文 slug 实测不认，用中文）
      - "kw:夜市"   → 走 --keyword（名称关键词，兜底美食/夜景/咖啡；
                      keyword 路线待真实 Key 验证）

    性能：每个查询都会冷启动一个 npx flyai 子进程（3–6 秒），串行跑 9 路要
    30–60 秒。这里改成 ThreadPoolExecutor 并行（子进程不受 GIL 限制），
    并对相同 (city, queries) 做 5 分钟内存缓存。
    单路失败只打印警告、不中断整轮抓取。
    """
    key = (city, tuple(queries))
    with _POI_CACHE_LOCK:
        hit = _POI_CACHE.get(key)
    if hit and time.perf_counter() - hit[0] < _POI_CACHE_TTL:
        print(f"   search_poi_multi 命中缓存（{len(hit[1])} 条）")
        return hit[1]

    def _one(q):
        # flyai 有并发限流（并发过高会返回 Rate limit exceeded），
        # 因此单路内部做退避重试；限流错误退避更久，仍失败才放弃。
        last = None
        for attempt in range(3):
            tq = time.perf_counter()
            try:
                if q.startswith("kw:"):
                    pois = search_poi(city, keyword=q[3:])
                else:
                    pois = search_poi(city, category=q)
                print(f"   查询 {q:<10} {time.perf_counter()-tq:>5.2f}s → {len(pois)} 条")
                return q, pois, None
            except Exception as e:
                last = e
                if attempt < 2:
                    time.sleep(2.5 if "Rate limit" in str(e) else 0.8)
        print(f"   ⚠ 查询 {q} 失败，已跳过：{last}")
        return q, [], last

    t0 = time.perf_counter()
    results = []
    # 限并发 3：既能把 9 路串行压到 1/3 左右，又不触发 flyai 的限流阈值
    with ThreadPoolExecutor(max_workers=min(3, max(len(queries), 1))) as ex:
        futures = [ex.submit(_one, q) for q in queries]
        for fut in as_completed(futures):
            results.append(fut.result())
    # 恢复查询顺序，保证偏好加权结果稳定
    order = {q: i for i, q in enumerate(queries)}
    results.sort(key=lambda x: order[x[0]])

    seen, out = set(), []
    failed = False
    for q, pois, err in results:
        failed = failed or err is not None
        for p in pois:
            if p["name"] and p["name"] not in seen:
                seen.add(p["name"])
                p["src"] = q  # 记下这条 POI 是哪个查询抓到的，供偏好排序用
                out.append(p)

    print(f"   search_poi_multi 总耗时 {time.perf_counter()-t0:.2f}s → {len(out)} 条去重后 POI")
    if not failed:  # 有查询失败时不写缓存，下次可完整重试
        with _POI_CACHE_LOCK:
            _POI_CACHE[key] = (time.perf_counter(), out)
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
    """酒店搜索：相同 (城市, 关键词) 5 分钟内走缓存，避免重复点击时反复冷启。"""
    cache_key = (dest, keywords or "")
    hit = _HOTEL_CACHE.get(cache_key)
    if hit and time.perf_counter() - hit[0] < _POI_CACHE_TTL:
        print(f"   search_hotel 命中缓存（{len(hit[1])} 家）")
        return hit[1]

    args = ["search-hotel", "--dest-name", dest]
    if keywords:
        args += ["--key-words", keywords]
    t0 = time.perf_counter()
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
    print(f"   search_hotel 耗时 {time.perf_counter()-t0:.2f}s → {len(out)} 家")
    _HOTEL_CACHE[cache_key] = (time.perf_counter(), out)
    return out
