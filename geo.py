"""地理小工具：距离计算 + 按酒店位置的 POI 排序（空间感知核心）。"""
import math


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def sort_by_distance(pois, lat, lng, max_km=10):
    """返回 [(poi, 距离km)]，过滤无坐标与超远点，按距离升序。"""
    out = []
    for p in pois:
        if p["lat"] is None or p["lng"] is None:
            continue
        d = haversine_km(lat, lng, p["lat"], p["lng"])
        if d <= max_km:
            out.append((p, round(d, 1)))
    return sorted(out, key=lambda x: x[1])
