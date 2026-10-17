"""端到端 demo：杭州出差（会议时段输入）→ 空闲窗口 → 微行程 → 行程卡。"""
import sys

from flyai_client import build_queries, search_hotel, search_poi_multi
from planner import plan, free_windows_from_meetings
from llm import generate_intro, explain_plan
from natural import parse_meetings

CITY = "杭州"

# 产品真实输入：出差城市 + 会议时段（demo 用一组典型会议，作为解析失败/空输入的默认值）
MEETINGS = [
    ("09:30", "12:00"),   # 上午会
    ("14:00", "17:30"),   # 下午会
]


def _prefs_from_argv():
    """命令行传偏好，如 `python demo.py 美食,夜景`；不传则不加偏好权重。"""
    if len(sys.argv) < 2:
        return []
    return [p.strip() for p in sys.argv[1].split(",") if p.strip()]


PREFERENCES = _prefs_from_argv()


def render_card(city, hotel, stops, notes, intro, reasons=None):
    lines = [
        f"# 🗺️ 带薪旅行地图 · {city}",
        "",
        f"🏨 落脚：{hotel['name']}",
        f"📍 {hotel['address']}",
        "",
        f"💬 {intro}",
        "",
        "## 今晚行程",
    ]
    for i, s in enumerate(stops):
        p = s["poi"]
        free = {"FREE": "免费", "NOT_FREE": "收费"}.get(p["free"], "价格见详情")
        lines += [
            f"",
            f"### 🚇 {s['leg_mode']} {s['leg_min']} 分钟（{s['leg_km']}km）→ "
            f"{s['start']}–{s['end']} 📍 {p['name']}",
            f"{free} · {p['address']}",
        ]
        if p["desc"]:
            lines.append(f"> {p['desc']}")
        if reasons and i < len(reasons) and reasons[i]:
            lines.append(f"💡 {reasons[i]}")
        if p["url"]:
            lines.append(f"[查看详情/预订]({p['url']})")
    if notes:
        lines += ["", "## 📝 行程说明"] + [f"- {n}" for n in notes]
    lines += ["", "——", "基于飞猪实时数据（flyai skill）生成 · 商旅融合行程官"]
    return "\n".join(lines)


def read_meetings():
    """一句话读入会议描述：解析成功用解析结果，空输入或解析失败回退默认 MEETINGS。"""
    try:
        text = input("请输入会议描述（直接回车用默认会议）：\n> ").strip()
    except EOFError:
        text = ""
    if not text:
        print(f"   使用默认会议时段 {MEETINGS}")
        return MEETINGS

    parsed = parse_meetings(text)
    if not parsed:
        print(f"   解析失败，回退默认会议时段 {MEETINGS}")
        return MEETINGS

    print(f"   一句话解析出 {len(parsed)} 个会议：{parsed}")
    return [(m["start"], m["end"]) for m in parsed]


def main():
    print("0/6 解析会议描述…")
    meetings = read_meetings()

    print("1/6 搜酒店…")
    hotels = search_hotel(CITY, "西湖")
    hotel = hotels[0]
    print(f"   落脚：{hotel['name']}")

    print("2/6 搜 POI…")
    queries = build_queries(PREFERENCES)
    print(f"   偏好 {PREFERENCES or '（无）'} → 抓取顺序 {queries}")
    pois = search_poi_multi(CITY, queries)
    print(f"   拿到 {len(pois)} 个 POI")

    print("3/6 会议 → 空闲窗口…")
    windows = free_windows_from_meetings(meetings)
    print(f"   会议 {meetings} → 空闲 {windows}")

    print("4/6 规划微行程（含交通时间）…")
    stops, notes = plan(pois, hotel, windows, preferences=PREFERENCES)
    print(f"   排入 {len(stops)} 站，降级说明 {len(notes)} 条")

    print("5/6 生成文案…")
    intro = generate_intro(CITY, hotel["name"], stops)

    print("6/6 生成推荐理由…")
    legs = [(s["leg_min"], s["leg_mode"], s["leg_km"]) for s in stops]
    reasons = explain_plan(stops, legs, windows)
    print(f"   拿到 {len(reasons)} 条理由")

    card = render_card(CITY, hotel, stops, notes, intro, reasons)
    with open("demo_output.md", "w") as f:
        f.write(card)
    print("\n" + card)
    print("\n✅ 已保存到 demo_output.md")


if __name__ == "__main__":
    main()
