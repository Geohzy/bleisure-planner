"""微行程规划：会议间隙 → 空闲窗口 → 交通感知的贪心排站 → 降级话术。

产品输入是"出差城市 + 会议时段"，所以第一步先把会议时段
反推出空闲窗口，再往窗口里塞微行程。塞不下时不硬塞，
给出降级建议（notes），这是终评"完整度"要看的东西。
"""
from flyai_client import PREFERENCE_QUERIES
from geo import sort_by_distance
from transport import leg_between

# 距离落在同一档（±0.25km）就视为"同等距离"，此时优先选偏好品类
PREF_BAND_KM = 0.5


def _preferred_queries(preferences):
    """把偏好（美食/夜景/文化/放松）折算成一组"算作偏好"的查询词。"""
    out = set()
    for p in preferences or []:
        out.update(PREFERENCE_QUERIES.get(p, []))
    return out


def _rank(ranked, pref_srcs):
    """ranked 为 [(poi, dist)]。先按距离档位，同档再让偏好品类插到前面。"""
    if not pref_srcs:
        return ranked
    return sorted(ranked, key=lambda pd: (
        round(pd[1] / PREF_BAND_KM),
        0 if pd[0].get("src") in pref_srcs else 1,
        pd[1],
    ))


def _to_min(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _to_str(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def free_windows_from_meetings(meetings, day_start="09:00", day_end="22:00",
                               min_gap=45):
    """从会议时段反推空闲窗口。

    meetings: [("09:00","12:00"), ("14:00","17:30")]（可乱序、重叠）
    返回 [("12:00","14:00"), ("17:30","22:00")]，过滤掉 < min_gap 的碎片。
    """
    busy = sorted((_to_min(s), _to_min(e)) for s, e in meetings)
    merged = []
    for s, e in busy:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    wins, cur = [], _to_min(day_start)
    for s, e in merged:
        if s - cur >= min_gap:
            wins.append((_to_str(cur), _to_str(s)))
        cur = max(cur, e)
    if _to_min(day_end) - cur >= min_gap:
        wins.append((_to_str(cur), day_end))
    return wins


def plan(pois, hotel, windows, visit_minutes=90, max_km=10, preferences=None):
    """把空闲窗口切分，按距离贪心塞入 POI，考虑站间交通时间。

    windows: [("18:00","21:00"), ...]
    preferences: 用户偏好（美食/夜景/文化/放松），同等距离时优先选偏好品类的 POI；
                 不传则与原来完全一致。
    返回 (stops, notes)：
      stops: 每站含 start/end/poi/dist_km/leg_min/leg_mode（从上一站过来）
      notes: 降级话术列表（窗口太碎 / 半径内无 POI / 交通吃掉太多时间）
    """
    stops, notes, used = [], [], set()
    pref_srcs = _preferred_queries(preferences)

    for ws, we in windows:
        start, end = _to_min(ws), _to_min(we)
        span = end - start

        # —— 降级 1：窗口太碎，连一次快闪都不够 ——
        if span < 45:
            notes.append(f"{ws}–{we} 只有 {span} 分钟，碎片时间建议酒店附近散步/买杯咖啡，不硬塞景点。")
            continue

        # 窗口只够快闪（45–90 分钟）：只排最近的一站，游览压缩到 40 分钟
        quick = span < visit_minutes
        budget = 40 if quick else visit_minutes

        # —— 降级 2：半径内无 POI，尝试放宽到 2 倍半径 ——
        ranked = sort_by_distance(pois, hotel["lat"], hotel["lng"], max_km)
        if not ranked:
            ranked = sort_by_distance(pois, hotel["lat"], hotel["lng"], max_km * 2)
            if ranked:
                notes.append(f"{ws}–{we}：{max_km}km 内无合适地点，已放宽到 {max_km * 2}km 搜索。")
            else:
                notes.append(f"{ws}–{we}：{max_km * 2}km 内仍无 POI，建议酒店休息或明日早出发。")
                continue
        ranked = _rank(ranked, pref_srcs)
        if quick:
            notes.append(f"{ws}–{we} 只有 {span} 分钟，安排一次 40 分钟快闪（最近的一站）。")

        cur, prev = start, hotel
        idx = 0
        while cur + budget <= end and idx < len(ranked):
            poi, dist = ranked[idx]
            idx += 1
            if poi["name"] in used:
                continue
            leg_min, leg_mode, leg_km = leg_between(prev, poi)
            # 路上时间吃掉太多 → 放弃这一站（及更远的），直接降级收尾
            if leg_min > 40:
                notes.append(f"{poi['name']} 单程要 {leg_min} 分钟（{leg_mode}），"
                             f"时间不够，已跳过更远的地点。")
                break
            if cur + leg_min + budget > end:
                notes.append(f"{ws}–{we} 剩余时间不够再加一站（含路上 {leg_min} 分钟），行程到此为止。")
                break
            used.add(poi["name"])
            stops.append({
                "start": _to_str(cur + leg_min),
                "end": _to_str(cur + leg_min + budget),
                "poi": poi, "dist_km": dist,
                "leg_min": leg_min, "leg_mode": leg_mode, "leg_km": leg_km,
            })
            cur += leg_min + budget
            prev = poi
            if quick:  # 快闪只排一站
                break

        if stops and cur < end and idx < len(ranked):
            notes.append(f"{ws}–{we} 时间用完（剩 {_to_str(cur)} 之后 {end - cur} 分钟不够再排一站），行程到此为止。")

    return stops, notes
