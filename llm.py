"""文案生成层：优先调百炼（OpenAI 兼容接口），无 Key 时用本地模板兜底。

百炼 Key 请放在环境变量 DASHSCOPE_API_KEY，不要写进代码或仓库。
"""
import json
import os
import urllib.error
import urllib.request

ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# 模型优先级：前一个额度用尽 / 报错 / 返回空内容时，自动回退到下一个。
# 可用环境变量 QWEN_MODELS（逗号分隔）或 QWEN_MODEL（单个）覆盖。
MODELS = [m.strip() for m in (
    os.environ.get("QWEN_MODELS") or os.environ.get("QWEN_MODEL")
    or "qwen3.8-2.4t-a95b,deepseek-v4-pro-0813,qwen3.8-flash,qwen3.8-27b,qwen3.8-max"
).split(",") if m.strip()]

COPY_SYSTEM = ("你是「商旅融合行程官」的文案助手，风格轻松、带一点打工人梗，"
               "但信息准确、不编造。")


def _post(payload, api_key):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _bailian(prompt, api_key, system=COPY_SYSTEM, max_tokens=600, temperature=None,
             no_thinking=False):
    """百炼 OpenAI 兼容接口的单轮调用。

    按 MODELS 顺序逐个尝试，某个模型额度用尽 / 报错 / 返回空内容时自动回退到下一个；
    system / max_tokens / temperature 可按调用方覆盖。
    no_thinking=True 时请求关闭思考模式（reasoning 会吃掉几十秒），
    个别模型强制思考会返回 400，此时自动去掉该参数重试同一模型。
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    last_err = None
    for i, model in enumerate(MODELS):
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:  # 结构化抽取场景传 0，保证结果稳定
            payload["temperature"] = temperature
        if no_thinking:
            payload["enable_thinking"] = False
        try:
            try:
                data = _post(payload, api_key)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:160]
                if not (no_thinking and "enable_thinking" in detail):
                    raise
                # 该模型强制思考模式：换下一个模型，避免为了关思考又退回几十秒
                print(f"   模型 {model} 强制思考，跳过（请求关闭思考模式）")
                last_err = e
                continue
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:160]
            print(f"⚠ 模型 {model} 不可用（HTTP {e.code}）：{detail}")
            last_err = e
            continue
        content = (data["choices"][0]["message"].get("content") or "").strip()
        if not content:  # 思考型模型可能把 token 花在 reasoning 上，content 为空
            print(f"⚠ 模型 {model} 返回空内容，尝试下一个…")
            continue
        if i > 0:
            print(f"   已回退到模型 {model}")
        return content
    raise last_err or RuntimeError("所有候选模型均不可用")


def _stub(city, stops):
    names = "、".join(s["poi"]["name"] for s in stops) or "酒店躺平"
    return (f"带薪旅行的精髓：会开完了，快乐还没下班。今晚 {names}，"
            f"打卡不耽误回酒店，明天照样精神开会。")


def generate_intro(city, hotel_name, stops, api_key=None):
    key = api_key or os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        return _stub(city, stops) + "\n（本地模板兜底：设置 DASHSCOPE_API_KEY 后走百炼真实生成）"
    detail = "\n".join(
        f"- {s['start']} {s['poi']['name']}（距酒店 {s['dist_km']}km）" for s in stops
    )
    prompt = (f"用户在{city}出差，住{hotel_name}，今晚的微行程如下：\n{detail}\n"
              f"请写一段 80 字以内的开场白，点出'带薪旅行'的快乐，提到今晚的 1-2 个地点。")
    try:
        return _bailian(prompt, key, no_thinking=True)
    except Exception as e:
        print(f"⚠ 生成开场白失败，改用本地模板：{e}")
        return _stub(city, stops)


EXPLAIN_SYSTEM = """你是「商旅融合行程官」的行程解释助手。

每站的距离、耗时、停留时刻都已经由程序算好并写给你了。你唯一的任务是解释"为什么这样排"，
让用户明白这趟不是随便凑的，而是兼顾了"开会不耽误、打卡不绕路"。

硬性要求：
1. 所有数字（km、分钟、时刻）只能原样引用给你的数据，不许自己新增、改动或估算任何数字。
   特别注意"距酒店"和"从上一站过来"是两个不同的距离，绝对不要混用。
2. 每条理由必须落到具体数字上，不许写"安排合理""非常贴心""值得一去"这类套话。
3. 每条严格不超过 30 个字（含标点），只讲最关键的一点、最多出现 2 个数字，比如：
   "距酒店1.3km、步行18分钟，午休刚好够逛"（20字）
   "从上一站骑行14分钟，晚间空档衔接不赶"（20字）
   不要写"12:00–14:00"这类窗口时刻，用"午间空档""晚间空档"代替。
4. 只输出一个 JSON 字符串数组，条数与顺序跟要求的站点序号一一对应。
   除 JSON 外不要输出任何解释文字，也不要用 Markdown 代码块包裹。"""


def _minutes(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _window_text(window):
    """窗口入参兼容两种写法：单个 ("18:00","21:00") 或窗口列表。"""
    if not window:
        return "（未提供）"
    if isinstance(window[0], str):
        return f"{window[0]}–{window[1]}"
    return "、".join(f"{a}–{b}" for a, b in window)


def _template_verdict(stop, alternatives, dwell):
    """裁决的离线模板版：只引用真实数字，不调模型。"""
    p = stop["poi"]
    lines = [
        f"选它：距酒店 {stop['dist_km']}km、{stop['leg_mode']} {stop['leg_min']} 分钟，"
        f"停留 {dwell} 分钟正好填满窗口。"
    ]
    if alternatives:
        a = alternatives[0]
        lines.append(
            f"没选《{a['name']}》：距酒店 {a['dist_km']}km、{a['leg_mode']} "
            f"{a['leg_min']} 分钟更耗时，会挤占回酒店时间。"
        )
    return "；".join(lines)


def judge_verdict(stop, alternatives, dwell, api_key=None):
    """为一站生成"对比裁决"：为什么选它、为什么没选备选。返回单段中文。

    stop:          planner 生产的一站（含 poi/dist_km/leg_min/leg_mode/start/end）
    alternatives:  同窗口落选的 1–2 个备选 POI 摘要，各含
                   {"name","dist_km","leg_min","leg_mode"}
    dwell:         该站停留分钟数（由 start/end 算出，避免模型改数字）
    约束：只能引用入参给的真实数字，不许编造。无 Key 或调用失败时返回模板裁决。
    """
    if not alternatives:
        return _template_verdict(stop, [], dwell)

    key = api_key or os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        return _template_verdict(stop, alternatives, dwell)

    row = (f"选中站点：{stop['poi']['name']}｜距酒店 {stop['dist_km']}km｜"
           f"{stop['leg_mode']} {stop['leg_min']} 分钟｜停留 {dwell} 分钟")
    alts = "；".join(
        f"备选《{a['name']}》｜距酒店 {a['dist_km']}km｜{a['leg_mode']} {a['leg_min']} 分钟"
        for a in alternatives
    )
    prompt = (
        "对比裁决。数据已算好，只能原样引用、不许新增或改动数字：\n"
        f"选中：{row}\n备选：{alts}\n"
        "输出两句话，分号分隔：①『选它：』给 1–2 个真实理由（引用距离/耗时/停留数字）；"
        "②『没选《备选名》：』给真实劣势（引用其距离/耗时数字，点出为何让位）。"
        "备选只讲最关键的 1 点。最高 120 字，只有这两句，不要额外文字。"
    )
    try:
        raw = _bailian(prompt, key, system=(
            "你是「商旅融合行程官」的行程裁决助手。你负责解释『为什么选 A 不选 B』。"
            "铁律：所有数字只能原样引用给定数据，绝对不许编造距离/时间；"
            "没给的理由不要编。输出简体中文，简洁。" ), max_tokens=200, temperature=0,
            no_thinking=True)
        raw = raw.strip().strip("；").strip()
        return raw if raw else _template_verdict(stop, alternatives, dwell)
    except Exception as e:
        print(f"⚠ 生成裁决失败，改用模板：{e}")
        return _template_verdict(stop, alternatives, dwell)


def explain_plan(stops, legs, window, api_key=None):
    """为 planner 算好的行程生成 2-3 条推荐理由，返回与 stops 下标对齐的 list[str]。

    stops:  planner.plan 返回的站点（含 poi、start/end、dist_km）
    legs:   与 stops 下标对齐的路程 (leg_min, leg_mode, leg_km)，即"从上一站/酒店过来"
    window: 本段行程的空闲窗口，单个 ("18:00","21:00") 或窗口列表
    所有数字都来自 planner / transport，模型只负责解释。失败时返回 []，不中断主流程。
    """
    stops = list(stops or [])
    if not stops:
        return []

    key = api_key or os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        print("⚠ 未设置 DASHSCOPE_API_KEY，跳过推荐理由生成。")
        return []

    legs = list(legs or [])
    rows = []
    for i, s in enumerate(stops, 1):
        leg = legs[i - 1] if i <= len(legs) else (s["leg_min"], s["leg_mode"], s["leg_km"])
        leg_min, leg_mode, leg_km = leg
        dwell = _minutes(s["end"]) - _minutes(s["start"])
        origin = "酒店" if i == 1 else stops[i - 2]["poi"]["name"]
        rows.append(f"{i}. {s['poi']['name']}｜距酒店 {s['dist_km']}km｜"
                    f"从{origin}过来 {leg_mode} {leg_min}分钟／路程{leg_km}km｜"
                    f"停留 {s['start']}–{s['end']}（{dwell}分钟）")

    want = min(3, len(stops))  # 要求模型写几条，就按序号取前几条，保证下标对齐
    prompt = (
        "行程数据（数字已算好，只能原样引用）：\n"
        + "\n".join(rows)
        + f"\n可用空闲窗口：{_window_text(window)}\n"
        + f"请为序号 1 到 {want} 的站点各写一条推荐理由，"
        + f"按序号顺序输出一个内含 {want} 个字符串的 JSON 数组。"
    )

    try:
        raw = _bailian(prompt, key, system=EXPLAIN_SYSTEM, max_tokens=400, temperature=0)
    except Exception as e:
        print(f"⚠ 生成推荐理由失败，本次跳过：{e}")
        return []

    chunk = raw[raw.find("["):raw.rfind("]") + 1] if "[" in raw and "]" in raw else ""
    if not chunk:
        print("⚠ 推荐理由没有返回 JSON 数组，本次跳过。")
        return []
    try:
        items = json.loads(chunk)
    except json.JSONDecodeError as e:
        print(f"⚠ 推荐理由不是合法 JSON，本次跳过：{e}")
        return []
    if not isinstance(items, list):
        print("⚠ 推荐理由不是数组，本次跳过。")
        return []

    reasons = [str(x).strip() for x in items if isinstance(x, str) and str(x).strip()]
    return reasons[:want]
