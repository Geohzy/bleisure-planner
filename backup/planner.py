"""微行程规划：把空闲窗口切分，按距离贪心塞入 POI。"""
from geo import sort_by_distance


def _to_min(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _to_str(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def plan(pois, hotel, windows, visit_minutes=90, max_km=10):
    """windows: [("18:00","21:00"), ...]；返回按时间排好的 stops 列表。"""
    ranked = sort_by_distance(pois, hotel["lat"], hotel["lng"], max_km)
    stops, used, idx = [], set(), 0
    for ws, we in windows:
        cur, end = _to_min(ws), _to_min(we)
        while cur + visit_minutes <= end and idx < len(ranked):
            poi, dist = ranked[idx]
            idx += 1
            if poi["name"] in used:
                continue
            used.add(poi["name"])
            stops.append({
                "start": _to_str(cur), "end": _to_str(cur + visit_minutes),
                "poi": poi, "dist_km": dist,
            })
            cur += visit_minutes
    return stops
