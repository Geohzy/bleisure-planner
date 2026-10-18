"""Gradio 网页版在线 Demo：评委不装环境也能体验完整链路。

启动：python3 app.py   （首次运行先 pip install gradio）
页面输入：城市 / 酒店关键词 / 会议描述 / 偏好多选 →
后端依次调用 parse_meetings → search_hotel → build_queries → search_poi_multi
→ plan → generate_intro / explain_plan → 输出行程卡 + 站点表格 + 推荐理由。
Key 只读环境变量，不写进代码。
"""
import gradio as gr

from flyai_client import build_queries, search_hotel, search_poi_multi
from planner import plan, free_windows_from_meetings
from llm import generate_intro, explain_plan
from natural import parse_meetings
from demo import MEETINGS as DEFAULT_MEETINGS, render_card

PREFERENCE_CHOICES = ["美食", "夜景", "文化", "放松"]

TABLE_HEADERS = ["序号", "站点", "距酒店", "出行方式", "耗时", "时段"]


def _meetings_from_text(text):
    """会议描述 → [(start, end)]；空文本或解析失败回退默认会议时段。"""
    text = (text or "").strip()
    if not text:
        return DEFAULT_MEETINGS
    try:
        parsed = parse_meetings(text)
    except Exception:
        return DEFAULT_MEETINGS
    if not parsed:
        return DEFAULT_MEETINGS
    return [(m["start"], m["end"]) for m in parsed]


def build_itinerary(city, hotel_kw, meeting_text, preferences):
    """完整链路：返回 (行程卡 markdown, 站点表格行, 推荐理由 markdown)。

    任何一步失败都返回中文提示，不向页面吐 traceback。
    """
    try:
        meetings = _meetings_from_text(meeting_text)

        hotels = search_hotel(city, hotel_kw)
        if not hotels:
            return ("⚠️ 未找到酒店，请检查城市 / 酒店关键词。", [], "⚠️ 未找到酒店。")
        hotel = hotels[0]

        queries = build_queries(preferences)
        pois = search_poi_multi(city, queries)
        if not pois:
            return ("⚠️ 未抓取到 POI：请确认 FLYAI_API_KEY 已设置、flyai CLI 已安装"
                    "（npm i @fly-ai/flyai-cli）、网络可用。", [], "⚠️ 未抓取到 POI。")

        windows = free_windows_from_meetings(meetings)
        stops, notes = plan(pois, hotel, windows, preferences=preferences)
        if not stops:
            return ("⚠️ 没有排入任何站点（空闲窗口太碎，或半径内无 POI）。", [],
                    "⚠️ 没有可展示的站点。")

        intro = generate_intro(city, hotel["name"], stops)
        legs = [(s["leg_min"], s["leg_mode"], s["leg_km"]) for s in stops]
        reasons = explain_plan(stops, legs, windows)

        card = render_card(city, hotel, stops, notes, intro, reasons)
        rows = [
            [i + 1, s["poi"]["name"], f'{s["dist_km"]}km', s["leg_mode"],
             f'{s["leg_min"]} 分钟', f'{s["start"]}–{s["end"]}']
            for i, s in enumerate(stops)
        ]
        reason_md = (
            "\n".join(f"{i + 1}. {r}" for i, r in enumerate(reasons))
            or "（未生成推荐理由：设置 DASHSCOPE_API_KEY 后重试）"
        )
        return card, rows, reason_md
    except Exception as e:
        # 控制台留详细原因，页面只给中文指引
        print(f"[app] 生成失败：{e!r}")
        return (
            "⚠️ 生成失败（见服务器控制台）。\n\n"
            "请确认：\n"
            "1. 已安装 flyai CLI：`npm i @fly-ai/flyai-cli`；\n"
            "2. 已设置环境变量 `FLYAI_API_KEY`（价格与数据真实）、"
            "`DASHSCOPE_API_KEY`（文案走百炼）；\n"
            "3. Key 只从环境变量读取，未写进代码。",
            [],
            "⚠️ 生成失败。",
        )


with gr.Blocks(title="商旅融合行程官 · 在线 Demo") as demo:
    gr.Markdown(
        "# 🗺️ 商旅融合行程官 · 在线 Demo\n\n"
        "**带薪旅行**：输入出差城市、会议描述与偏好，自动把会议间隙排成"
        "不耽误正事的微行程（真实飞猪 POI + 交通时间估算 + AI 推荐理由）。"
    )
    with gr.Row():
        city = gr.Textbox(label="出差城市", value="杭州")
        hotel_kw = gr.Textbox(label="酒店关键词", value="西湖")
    meeting_text = gr.Textbox(
        label="会议描述（一句话，留空用默认会议）",
        value="明天下午2点到4点开会，晚上7点半客户晚宴",
        lines=3,
    )
    prefs = gr.Dropdown(
        label="偏好（可多选）", choices=PREFERENCE_CHOICES, multiselect=True
    )
    btn = gr.Button("✨ 生成微行程", variant="primary")

    card_out = gr.Markdown(label="行程卡")
    table_out = gr.Dataframe(label="站点详情（距离 / 耗时 / 出行方式）",
                             headers=TABLE_HEADERS)
    reason_out = gr.Markdown(label="推荐理由")

    btn.click(build_itinerary,
              inputs=[city, hotel_kw, meeting_text, prefs],
              outputs=[card_out, table_out, reason_out])

if __name__ == "__main__":
    demo.queue().launch()
