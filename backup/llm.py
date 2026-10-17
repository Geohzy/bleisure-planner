"""文案生成层：优先调百炼（OpenAI 兼容接口），无 Key 时用本地模板兜底。

百炼 Key 请放在环境变量 DASHSCOPE_API_KEY，不要写进代码或仓库。
"""
import json
import os
import urllib.request

ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
MODEL = "deepseek-v4-flash-0731"


def _bailian(prompt, api_key):
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "你是「商旅融合行程官」的文案助手，风格轻松、带一点打工人梗，但信息准确、不编造。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 600,
    }).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return data["choices"][0]["message"]["content"].strip()


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
    return _bailian(prompt, key)
