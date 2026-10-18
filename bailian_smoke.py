"""百炼 Key 烟雾测试：一键验证 DASHSCOPE_API_KEY + QWEN_MODEL 是否真的能调通。

用法（在 Trae 终端里）：
    export DASHSCOPE_API_KEY=sk-xxx          # 百炼控制台 -> API-KEY 管理
    export QWEN_MODEL=qwen-turbo             # 换成你免费额度最多的模型，默认 qwen-plus
    python3 bailian_smoke.py

只做一次最小调用，不跑业务逻辑。纯标准库，零依赖。
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def main():
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    model = os.environ.get("QWEN_MODEL", "qwen-plus").strip() or "qwen-plus"

    print(f"模型: {model}（扣的就是这个模型的额度，跟 key 无关）")
    if not api_key:
        print("未检测到 DASHSCOPE_API_KEY。")
        print("去百炼控制台 https://bailian.console.aliyun.com -> API-KEY 管理 创建，")
        print("然后 export DASHSCOPE_API_KEY=sk-xxx 再跑一次。")
        return 2

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "用一句话介绍杭州西湖。"}],
        "max_tokens": 60,
    }).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "ignore")[:500]
        ms = int((time.time() - t0) * 1000)
        print(f"调用失败: HTTP {e.code}（{ms}ms）")
        if e.code == 401:
            print("-> Key 无效或已过期，去控制台重新复制一串 sk- 开头的。")
        elif e.code == 403:
            print("-> 额度问题：可能是该模型的免费额度用完了。")
            print("   1) 控制台 -> 模型广场点进该模型，看剩余额度/到期时间；")
            print("   2) 换一个免费额度多的模型：export QWEN_MODEL=xxx；")
            print("   3) 建议打开「免费额度用完即停」开关，避免悄悄转按量付费。")
        else:
            print("-> 返回内容:", err_body)
        return 1
    except Exception as e:  # 网络等其他问题
        print(f"调用异常: {type(e).__name__}: {e}")
        return 1

    ms = int((time.time() - t0) * 1000)
    try:
        text = data["choices"][0]["message"]["content"].strip()
        used_model = data.get("model", model)
    except (KeyError, IndexError, TypeError):
        print("返回结构异常:", json.dumps(data, ensure_ascii=False)[:300])
        return 1

    print(f"调用成功（{ms}ms），实际计费模型: {used_model}")
    print("模型回复:", text)
    print("结论: llm.py 的百炼链路是通的，P1/P3 可以放心用真实调用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
