"""端到端 demo：杭州出差（会议时段输入）→ 空闲窗口 → 微行程 → 行程卡。"""
from flyai_client import search_hotel, search_poi_multi
from planner import plan, free_windows_from_meetings
from llm import generate_intro

CITY = "杭州"

# 产品真实输入：出差城市 + 会议时段（demo 用一组典型会议）
MEETINGS = [
    ("09:30", "12:00"),   # 上午会
    ("14:00", "17:30"),   # 下午会
]

# 多品类抓取：中文类目走 --category；美食/夜景/咖啡无对应类目，走 --keyword 兜底
# （--help 里写的英文 slug 实测不认，用中文；kw: 路线待真实 Key 验证）
QUERIES = [
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


def render_card(city, hotel, stops, notes, intro):
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
    for s in stops:
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
        if p["url"]:
            lines.append(f"[查看详情/预订]({p['url']})")
    if notes:
        lines += ["", "## 📝 行程说明"] + [f"- {n}" for n in notes]
    lines += ["", "——", "基于飞猪实时数据（flyai skill）生成 · 商旅融合行程官"]
    return "\n".join(lines)


def main():
    print("1/5 搜酒店…")
    hotels = search_hotel(CITY, "西湖")
    hotel = hotels[0]
    print(f"   落脚：{hotel['name']}")

    print("2/5 搜 POI…")
    pois = search_poi_multi(CITY, QUERIES)
    print(f"   拿到 {len(pois)} 个 POI")

    print("3/5 解析会议 → 空闲窗口…")
    windows = free_windows_from_meetings(MEETINGS)
    print(f"   会议 {MEETINGS} → 空闲 {windows}")

    print("4/5 规划微行程（含交通时间）…")
    stops, notes = plan(pois, hotel, windows)
    print(f"   排入 {len(stops)} 站，降级说明 {len(notes)} 条")

    print("5/5 生成文案…")
    intro = generate_intro(CITY, hotel["name"], stops)

    card = render_card(CITY, hotel, stops, notes, intro)
    with open("demo_output.md", "w") as f:
        f.write(card)
    print("\n" + card)
    print("\n✅ 已保存到 demo_output.md")


if __name__ == "__main__":
    main()
