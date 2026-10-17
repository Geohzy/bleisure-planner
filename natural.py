"""一句话会议解析：中文口语描述 → 结构化会议列表（走百炼，失败降级为 []）。

输出结构与产品约定一致：
    [{"title": str, "start": "HH:MM", "end": "HH:MM"}]
解析失败时只打印中文警告并返回 []，绝不抛异常中断主流程。
"""
import json
import os
import re

from llm import _bailian  # 复用 llm.py 的百炼调用方式（读 DASHSCOPE_API_KEY / QWEN_MODEL）

SYSTEM = """你是会议信息抽取器。用户用中文口语描述自己的日程，你只负责把它转成结构化 JSON。

输出要求（严格遵守）：
- 只输出一个 JSON 数组，不要输出任何解释、注释、前后缀，也不要用 Markdown 代码块包裹。
- 每个元素形如：{"title": "会议名称", "start": "HH:MM", "end": "HH:MM"}

解析规则：
1. 时间统一用 24 小时制、小时两位补零，如 "14:00"、"19:30"；中文数字（两点、七点半）要换算成阿拉伯数字。
   "X点半" 一律读成 X 时 30 分："7点半" → "19:30"（晚上）、"7点半" → "07:30"（早上）。
2. "下午2-4点" → start "14:00"、end "16:00"。
3. 只说了开始时间、没给结束时间时，默认时长 1 小时。
   "晚上7点半客户晚宴" → start "19:30"、end "20:30"。
4. title 用用户原话里的会议名（如"客户晚宴"、"周会"），不要一律写成"会议"。
5. 没有明确日期的一律按明天处理（输出里不需要写日期）。
6. 跨天的会议拆成两段：如"今晚11点到凌晨1点" →
   {"title": "...", "start": "23:00", "end": "24:00"} 与 {"title": "...", "start": "00:00", "end": "01:00"}。
7. 实在识别不出任何会议时输出 []。"""

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

# 「X点半」的小时数直接从原话里抓，用来兜住模型偶尔漏掉的 30 分钟
_HALF_RE = re.compile(r"(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*点\s*半")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}


def _half_hours(text):
    """扫原话里所有「X点半」，返回小时数集合（按 12 取模，兼容上午/晚上）。"""
    out = set()
    for raw in _HALF_RE.findall(text or ""):
        h = int(raw) if raw.isdigit() else _CN_NUM.get(raw)
        if h is not None:
            out.add(h % 12)
    return out


def _fix_half(start, end, half_hours):
    """模型把「X点半」漏写成「X:00」时，用原话的半点信息就地校正。

    起点补 30 分钟；若模型给的是默认 1 小时时长，终点同步后移，保持时长不变。
    """
    hh, mm = (int(x) for x in start.split(":"))
    if mm != 0 or hh % 12 not in half_hours:
        return start, end

    fixed_start = f"{hh:02d}:30"
    eh, em = (int(x) for x in end.split(":"))
    if eh * 60 + em - (hh * 60 + mm) == 60:  # 默认 1 小时 → 终点一起后移
        total = hh * 60 + 30 + 60
        return fixed_start, f"{total // 60:02d}:{total % 60:02d}"
    return fixed_start, end


def _extract_json(raw):
    """从模型输出里抠出 JSON 数组，容忍代码块围栏和前后废话。"""
    s = (raw or "").strip()
    i, j = s.find("["), s.rfind("]")
    if i == -1 or j < i:
        return None
    return s[i:j + 1]


def _norm_time(t):
    """规范化 "9:00" → "09:00"；不合法则返回 None。"""
    t = str(t or "").strip()
    if not _TIME_RE.match(t):
        return None
    return t.zfill(5)


def _normalize(items, half_hours=frozenset()):
    """校验并清洗模型输出，丢掉时间不合法的条目，顺带校正「X点半」。"""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        start, end = _norm_time(it.get("start")), _norm_time(it.get("end"))
        if not start or not end:
            continue
        start, end = _fix_half(start, end, half_hours)
        out.append({
            "title": str(it.get("title") or "会议").strip(),
            "start": start,
            "end": end,
        })
    return out


def parse_meetings(text, api_key=None):
    """把一句话会议描述解析成会议列表。

    JSON 解析失败会重试一次；仍失败则打印中文警告并返回 []（调用方回退默认值）。
    """
    text = (text or "").strip()
    if not text:
        return []

    half_hours = _half_hours(text)

    key = api_key or os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        print("⚠ 未设置 DASHSCOPE_API_KEY，无法解析会议描述，将使用默认会议时段。")
        return []

    for attempt in (1, 2):
        if attempt > 1:
            print("⚠ 上一次解析结果不可用，重试一次…")
        try:
            raw = _bailian(text, key, system=SYSTEM, max_tokens=500, temperature=0)
        except Exception as e:
            print(f"⚠ 调用百炼失败：{e}")
            continue

        chunk = _extract_json(raw)
        if chunk is None:
            print("⚠ 模型没有输出 JSON 数组。")
            continue

        try:
            items = json.loads(chunk)
        except json.JSONDecodeError as e:
            print(f"⚠ 模型返回的不是合法 JSON：{e}")
            continue

        meetings = _normalize(items, half_hours)
        if meetings:
            return meetings
        print("⚠ 解析结果里没有有效时段。")

    print("⚠ 会议描述解析失败，本次改用默认会议时段。")
    return []
