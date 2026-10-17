"""交通时间估算：按两点直线距离 + 出行方式估算耗时。

出差微行程的核心约束是"别耽误正事"：每两站之间必须预留真实
的路上时间，而不是只看景点本身要逛多久。
"""
from geo import haversine_km

# (方式, 适用距离上限 km, 平均速度 km/h, 固定耗时 min：等车/叫车/找车)
_MODES = [
    ("步行", 1.5, 4.5, 0),
    ("骑行", 4.0, 12.0, 3),     # 找车、还车
    ("地铁/公交", 12.0, 25.0, 10),  # 进出站、等车
    ("打车", float("inf"), 28.0, 8),  # 叫车等待（市区晚高峰均速）
]


def choose_mode(dist_km):
    """按距离自动选最合适的出行方式。"""
    for name, limit, speed, fixed in _MODES:
        if dist_km <= limit:
            return name, speed, fixed
    return _MODES[-1][0], _MODES[-1][1], _MODES[-1][2]


def estimate_leg(lat1, lng1, lat2, lng2, mode=None):
    """估算两点间耗时。返回 (分钟数 int, 出行方式 str)。

    不传 mode 时按距离自动选择；传了则按指定方式计算。
    """
    d = haversine_km(lat1, lng1, lat2, lng2)
    spec = next((m for m in _MODES if m[0] == mode), None) if mode else None
    # mode 写错或没传时回退到按距离自动选择
    name, speed, fixed = (spec[0], spec[1], spec[2]) if spec else choose_mode(d)
    minutes = int(round(d / speed * 60 + fixed))
    return max(minutes, 2 if d > 0.05 else 0), name  # 同一地点不计耗时


def leg_between(a, b):
    """a、b 为含 lat/lng 的地点 dict。返回 (分钟, 方式, 距离km)。"""
    d = haversine_km(a["lat"], a["lng"], b["lat"], b["lng"])
    minutes, mode = estimate_leg(a["lat"], a["lng"], b["lat"], b["lng"])
    return minutes, mode, round(d, 1)
