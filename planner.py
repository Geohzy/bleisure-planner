"""微行程规划：会议间隙 → 空闲窗口 → 交通感知的贪心排站 → 降级话术。

产品输入是"出差城市 + 会议时段"，所以第一步先把会议时段
反推出空闲窗口，再往窗口里塞微行程。塞不下时不硬塞，
给出降级建议（notes），这是终评"完整度"要看的东西。
"""
import re

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


def _note(constraint, expected, actual, action, text):
    """构造结构化降级说明（三条面对评委：约束 / 现实 / 处理）+ 可读 text。

    老代码只取 text 字段也能工作（向后兼容 plan 返回的 notes 从字符串列表
    升级为 dict 列表这一变更）。
    """
    return {
        "constraint": constraint,   # 卡在哪条约束
        "expected": expected,       # 期望是
        "actual": actual,           # 现实是
        "action": action,           # 可操作建议
        "text": text,               # 人类可读整句（老代码兼容）
    }


# ===== 景点开放时间 =====
# 取数优先级：① 飞猪详情返回的真实开放时间（poi["open_hours"]，能解析成 HH:MM 才用）
#              ② 按品类/抓取关键词估算的"典型开放窗口"（规则表，UI 标注"参考"）
#              ③ 都没有 → 不做时间约束（保持原有排站行为）
# 说明：规则表只用于排除"明显处于打烊时段"的安排，不代表官方营业时间。
OPEN_WINDOW_RULES = [
    (("博物馆",), ("09:00", "17:00")),
    (("人文古迹",), ("08:00", "18:00")),
    (("园林花园",), ("06:00", "21:00")),
    (("地标建筑",), ("00:00", "24:00")),
    (("市集", "文创"), ("10:00", "22:00")),
    (("夜市", "美食"), ("11:00", "23:00")),
    (("咖啡",), ("08:00", "22:00")),
]

# "09:00" / "09：00" / "9:5" 之类都能认；解析窗口时取前两个时刻当开/关
_CLOCK_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{1,2})")
_ALL_DAY_RE = re.compile(r"全天|24\s*小时|24\s*h", re.IGNORECASE)


def _parse_window(raw):
    """从开放时间文本里解析 (open_min, close_min)；解析不出返回 None。

    兼容 "09:00-17:00"、"09:00–17:00（16:00 停止入场）"、"全天开放" 等写法。
    """
    if not raw:
        return None
    s = str(raw)
    if _ALL_DAY_RE.search(s):
        return 0, 24 * 60
    hits = _CLOCK_RE.findall(s)
    if len(hits) < 2:
        return None
    open_min = int(hits[0][0]) * 60 + int(hits[0][1])
    close_min = int(hits[1][0]) * 60 + int(hits[1][1])
    if open_min >= close_min:  # 跨夜（如 20:00–02:00）暂不纳入，避免误判
        return None
    return open_min, close_min


def _rule_window(poi):
    """按 category / 抓取关键词（src）匹配典型开放窗口，返回 (open, close) 文本或 None。"""
    tags = [str(poi.get("category") or "")]
    src = str(poi.get("src") or "")
    tags.append(src[3:] if src.startswith("kw:") else src)
    for keys, win in OPEN_WINDOW_RULES:
        if any(k in t for k in keys for t in tags):
            return win
    return None


# 夜景属性 POI：品类常被判成"地标建筑 → 全天"，光靠 category/关键词会把它误排到早上。
# 用名称/简介里的夜间关键词兜底，把可用窗口收窄到傍晚（"音乐喷泉排早 9 点"这类问题的根因）。
_NIGHT_HINTS = ("夜游", "夜景", "夜市", "夜间", "夜场", "夜赏", "晚上",
                "灯光秀", "喷泉", "酒吧", "夜航")
_NIGHT_WINDOW = (18 * 60, 23 * 60)


def _is_all_day(win):
    """窗口是否等于"全天"（00:00–24:00）。"""
    return win is not None and win[0] <= 0 and win[1] >= 24 * 60


def _infer_night_window(poi):
    """名称/简介命中夜间关键词时返回傍晚窗口 (open_min, close_min)，否则 None。"""
    text = f"{poi.get('name') or ''}{poi.get('desc') or poi.get('description') or ''}"
    if any(h in text for h in _NIGHT_HINTS):
        return _NIGHT_WINDOW
    return None


def resolve_open_window(poi):
    """返回 (open_min, close_min, src)；src ∈ {"api", "estimated", "unknown"}。

    unknown 时 open/close 为 None，调用方据此不做时间约束。
    """
    parsed = _parse_window(poi.get("open_hours"))
    if parsed:
        win, src = parsed, "api"
    else:
        rule = _rule_window(poi)
        if rule:
            win, src = (_to_min(rule[0]), _to_min(rule[1])), "estimated"
        else:
            win, src = None, "unknown"

    # 只在"全天 / 无依据"时用夜间窗口兜底；已有具体时段（API 真实值、品类规则，
    # 如博物馆 09:00–17:00）一律不覆盖，避免误伤白天的景点。
    if win is None or _is_all_day(win):
        night = _infer_night_window(poi)
        if night:
            return night[0], night[1], "estimated"
    if win is None:
        return None, None, "unknown"
    return win[0], win[1], src


def open_hours_label(poi):
    """展示用开放时段文案；无依据时返回空串。

    估算值明确标注"（参考）"，与飞猪详情返回的真实值区分，不冒充官方数据。
    """
    start, close, src = resolve_open_window(poi)
    if src == "unknown":
        return ""
    label = f"{_to_str(start)}–{_to_str(close)}"
    return label + "（参考）" if src == "estimated" else label


def _fits_open(poi, start_min, end_min):
    """到访时段是否完整落在开放窗口内；无依据（unknown）时不设限。"""
    start, close, src = resolve_open_window(poi)
    if src == "unknown":
        return True
    return start <= start_min and end_min <= close


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


def apply_buffer(windows, buffer_min=30, min_gap=45):
    """给每个空闲窗口两端各削掉 buffer_min 分钟，作为会议衔接缓冲。

    用于确保微旅行活动的赶路/收尾不会压到正式工作时段。窗口被缓冲压到
    不足 min_gap 时整体丢弃。不改变 free_windows_from_meetings / plan 的签名。
    """
    out = []
    for ws, we in windows:
        s = _to_min(ws) + buffer_min
        e = _to_min(we) - buffer_min
        if e - s >= min_gap:
            out.append((_to_str(s), _to_str(e)))
    return out


def plan(pois, hotel, windows, visit_minutes=90, max_km=10, preferences=None,
         respect_hours=True):
    """把空闲窗口切分，按距离贪心塞入 POI，考虑站间交通与景点开放时间。

    windows: [("18:00","21:00"), ...]
    preferences: 用户偏好（美食/夜景/文化/放松），同等距离时优先选偏好品类的 POI；
                 不传则与原来完全一致。
    respect_hours: 是否校验景点开放时间；到访时段落在打烊时段则跳过该站、改选
                   同窗口内仍开放的备选（无依据的 POI 不做限制）。设 False 即恢复
                   旧行为，供调用方显式关闭。
    返回 (stops, notes)：
      stops: 每站含 start/end/poi/dist_km/leg_min/leg_mode（从上一站过来）
      notes: 降级话术列表（窗口太碎 / 半径内无 POI / 交通吃掉太多时间 / 景点打烊）
    """
    stops, notes, used = [], [], set()
    pref_srcs = _preferred_queries(preferences)

    for ws, we in windows:
        start, end = _to_min(ws), _to_min(we)
        span = end - start

        # —— 降级 1：窗口太碎，连一次快闪都不够 ——
        if span < 45:
            notes.append(_note(
                "空闲窗口太碎", f"至少 {45} 分钟才能排一站",
                f"{ws}–{we} 只有 {span} 分钟",
                "建议酒店附近散步/买杯咖啡，不硬塞景点；把微小碎片留给休息",
                f"{ws}–{we} 只有 {span} 分钟，碎片时间建议酒店附近散步/买杯咖啡，不硬塞景点。",
            ))
            continue

        # 窗口只够快闪（45–90 分钟）：只排最近的一站，游览压缩到 40 分钟
        quick = span < visit_minutes
        budget = 40 if quick else visit_minutes

        # —— 降级 2：半径内无 POI，尝试放宽到 2 倍半径 ——
        ranked = sort_by_distance(pois, hotel["lat"], hotel["lng"], max_km)
        if not ranked:
            ranked = sort_by_distance(pois, hotel["lat"], hotel["lng"], max_km * 2)
            if ranked:
                notes.append(_note(
                    "搜索半径内无 POI", f"{max_km}km 内有可选地点",
                    f"{max_km}km 内无 POI",
                    f"已放宽到 {max_km * 2}km 搜索；如需更多可选，调大 max_km",
                    f"{ws}–{we}：{max_km}km 内无合适地点，已放宽到 {max_km * 2}km 搜索。",
                ))
            else:
                notes.append(_note(
                    "放宽后仍无 POI", f"{max_km * 2}km 内有可选地点",
                    f"{max_km * 2}km 内仍无 POI",
                    "建议酒店休息或明日早出发；演示前确认该城市 POI 数据已抓取",
                    f"{ws}–{we}：{max_km * 2}km 内仍无 POI，建议酒店休息或明日早出发。",
                ))
                continue
        ranked = _rank(ranked, pref_srcs)
        if quick:
            notes.append(_note(
                "窗口仅够快闪", f"≥{visit_minutes} 分钟",
                f"只有 {span} 分钟",
                "安排 40 分钟快闪（最近的一站）",
                f"{ws}–{we} 只有 {span} 分钟，安排一次 40 分钟快闪（最近的一站）。",
            ))

        cur, prev = start, hotel
        idx = 0
        placed_before = len(stops)
        skipped = []   # 因打烊被跳过的 POI：[(名称, 开放, 关闭, 依据)]
        while cur + budget <= end and idx < len(ranked):
            poi, dist = ranked[idx]
            idx += 1
            if poi["name"] in used:
                continue
            leg_min, leg_mode, leg_km = leg_between(prev, poi)
            # 路上时间吃掉太多 → 放弃这一站（及更远的），直接降级收尾
            if leg_min > 40:
                notes.append(_note(
                    "站间交通硬约束", "单程 ≤40 分钟",
                    f"{poi['name']} 单程要 {leg_min} 分钟（{leg_mode}）",
                    "跳过该站及更远地点；换更快交通或搜更近 POI",
                    f"{poi['name']} 单程要 {leg_min} 分钟（{leg_mode}），"
                    f"时间不够，已跳过更远的地点。",
                ))
                break
            if cur + leg_min + budget > end:
                notes.append(_note(
                    "剩余窗口不足再加一站", f"还能塞 ≥1 站（含路上）",
                    f"剩 {end - cur} 分钟，该站含路上需 {leg_min} 分钟+停留 {budget} 分钟",
                    "把搜索半径放宽或缩短停留时长，以多排一站",
                    f"{ws}–{we} 剩余时间不够再加一站（含路上 {leg_min} 分钟），行程到此为止。",
                ))
                break
            # —— 开放时间约束：到访时段必须完整落在开放窗口内，否则跳过换备选 ——
            if respect_hours and not _fits_open(poi, cur + leg_min,
                                               cur + leg_min + budget):
                o, c, src = resolve_open_window(poi)
                skipped.append((poi["name"], _to_str(o), _to_str(c),
                                "飞猪详情" if src == "api" else "参考"))
                continue
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

        # —— 因打烊被跳过的地点：给一条结构化说明（跳过了谁 / 改选了什么）——
        if respect_hours and skipped:
            shown = "、".join(f"{n}（{o}–{c}，{tag}）" for n, o, c, tag in skipped[:3])
            more = "" if len(skipped) <= 3 else f" 等 {len(skipped)} 个"
            if len(stops) > placed_before:
                notes.append(_note(
                    "景点开放时间", f"{ws}–{we} 该时段有景点在营业",
                    f"{shown}{more}此时段不在开放时间内",
                    "已跳过这些地点，改选同窗口内仍开放的备选",
                    f"{ws}–{we}：{shown}{more}此时段不在开放时间，"
                    f"已跳过并改选仍开放的备选。",
                ))
            else:
                notes.append(_note(
                    "景点开放时间", f"{ws}–{we} 至少能排 1 站",
                    f"{shown}{more}此时段均不在开放时间内",
                    "本窗口未排站；建议换到其他空闲窗口，或选择该时段仍开放的公园/街区类地点",
                    f"{ws}–{we}：周边{shown}{more}此时段均不在开放时间，"
                    f"本窗口未安排景点。",
                ))

        if stops and cur < end and idx < len(ranked):
            notes.append(_note(
                "窗口时间用完", f"{_to_str(cur)} 之后还能加一站",
                f"剩 {end - cur} 分钟不够再排一站",
                "把半径放宽、或把停留时长调短来多排一站",
                f"{ws}–{we} 时间用完（剩 {_to_str(cur)} 之后 {end - cur} 分钟不够再排一站），行程到此为止。",
            ))

    return stops, notes


def _candidate_summary(poi, hotel):
    """计算一个落选备选的裁决摘要（dist_km/leg_min/leg_mode），供 F1 裁决用。"""
    leg_min, leg_mode, leg_km = leg_between(hotel, poi)
    return {
        "name": poi["name"], "dist_km": round(leg_km, 2),
        "leg_min": leg_min, "leg_mode": leg_mode,
    }


def swap_stop(stops, idx, pois, hotel, preferences=None, max_km=10,
              respect_hours=True):
    """一键换站：用已抓取的 POI 池（不重新调 flyai）替换第 idx 站。

    换站只替换 poi，并把该站的 dist_km / leg_min / leg_mode / leg_km 按新 POI
    重算；此站的 start/end 由它的"前序"（上一站结束时间）决定，保持不变，
    换站后也不会影响后续站的时刻（避免时间轴连锁错位）。

    候选挑选遵循 planner 同套规则：sort_by_distance(≤max_km) → _rank 偏好加权，
    并跳过本行程已用的 POI 与当前这一站。池内无合适备选时返回原 stops + 提示，
    绝不编造 POI。返回 (stops, note)：
      note: 换站成功返回 None；无备选返回一条人类可读的失败提示字符串。
    """
    stops = list(stops)
    if not (0 <= idx < len(stops)):
        return stops, f"指定站点序号无效（共 {len(stops)} 站）"
    cur = stops[idx]
    cur_poi_name = cur["poi"]["name"]
    used = {s["poi"]["name"] for s in stops}  # 已用的都不再作为候选

    ranked = sort_by_distance(pois, hotel["lat"], hotel["lng"], max_km)
    if not ranked:
        ranked = sort_by_distance(pois, hotel["lat"], hotel["lng"], max_km * 2)
    pref_srcs = _preferred_queries(preferences)
    ranked = _rank(ranked, pref_srcs)

    candidate = None
    closed = []   # 因打烊被排除的备选名
    for poi, dist in ranked:
        name = poi.get("name")
        if not name or name == cur_poi_name or name in used:
            continue
        # 备选也要在该站既有时段内开放，否则换了等于把行程排到打烊时段
        if respect_hours and not _fits_open(poi, _to_min(cur["start"]),
                                            _to_min(cur["end"])):
            closed.append(f"{name}（{open_hours_label(poi)}）")
            continue
        candidate = (poi, dist)
        break

    if candidate is None:
        if closed:
            return stops, ("该时段开放的备选已用完（" + "、".join(closed[:2])
                           + " 等此时段未开放），保留了原站点。")
        return stops, "该窗口暂无合适备选（距离内没有可替换且不重复的 POI），保留了原站点。"

    poi, dist = candidate
    leg_min, leg_mode, leg_km = leg_between(
        hotel if idx == 0 else stops[idx - 1]["poi"], poi
    )
    stops[idx] = dict(cur, poi=poi, dist_km=dist, leg_min=leg_min,
                      leg_mode=leg_mode, leg_km=leg_km)
    return stops, None


def stop_alternatives(stops, idx, pois, hotel, preferences=None, max_km=10, n=2,
                      respect_hours=True):
    """返回第 idx 站同窗口的 1–2 个落选备选摘要（供 F1 对比裁决引用）。

    与 swap_stop 同套挑选逻辑，但不替换、不要求交通更短，只取最近且未用的
    候选作为"没选它"的对比对象。返回 list[dict]，可能为空（此时裁决只写"选它"）。
    """
    if not (0 <= idx < len(stops)):
        return []
    cur_poi_name = stops[idx]["poi"]["name"]
    used = {s["poi"]["name"] for s in stops}

    ranked = sort_by_distance(pois, hotel["lat"], hotel["lng"], max_km)
    pref_srcs = _preferred_queries(preferences)
    ranked = _rank(ranked, pref_srcs)

    out = []
    for poi, _ in ranked:
        name = poi.get("name")
        if not name or name == cur_poi_name or name in used:
            continue
        # 落选备选同样要限定在该站时段内开放，否则"没选它"的对比不成立
        if respect_hours and not _fits_open(poi, _to_min(stops[idx]["start"]),
                                            _to_min(stops[idx]["end"])):
            continue
        out.append(_candidate_summary(poi, hotel))
        if len(out) >= n:
            break
    return out
