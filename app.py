"""Gradio 网页版在线 Demo：Editorial 新中式极简单页。

启动：python3 app.py   （首次运行先 pip install gradio）
页面输入：出差城市 / 会议描述 / 偏好多选 —— 点击「生成行程」后依次调用
parse_meetings → search_hotel → build_queries → search_poi_multi
→ plan → generate_intro / explain_plan，输出 暖咖金极简 SVG 路线示意图行程卡。

本文件只改展示层：不改 planner / flyai_client / llm / natural 的任何函数签名与返回结构。
页面上每一个数字（距离、耗时、时刻）均来自后端计算结果，前端不写死假数据。
Key 只读环境变量，不写进代码。
"""
import html
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor


def _load_local_keys():
    """启动时从本机 .gitignore 的 key.txt 补齐缺失的环境变量。

    仅当某个 Key 尚未在环境变量里时才会读本地文件（已有 env 优先）；
    key.txt 不进版本库、代码中不含任何密钥，因此不违反「Key 只来自环境变量」。
    兼容 `set VAR=val` 与 `VAR=val` 两种行格式。
    """
    kp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "key.txt")
    if not os.path.isfile(kp):
        return
    for line in open(kp, encoding="utf-8"):
        m = re.match(r"^(?:(?:set)\s+)?(\w+)=(.+)$", line.strip())
        if m and not os.environ.get(m.group(1)):
            os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")
_load_local_keys()


import gradio as gr

from flyai_client import build_queries, search_hotel, search_poi_multi
from urllib.parse import quote
from planner import (plan, free_windows_from_meetings, stop_alternatives,
                     swap_stop, apply_buffer, open_hours_label)
from llm import generate_intro, judge_verdict
from natural import parse_meetings
from demo import MEETINGS as DEFAULT_MEETINGS

# 偏好展示层 → 后端真实 key 的映射（后端只认 美食/夜景/文化/放松）
PREFERENCE_OPTIONS = [
    ("人文古迹", "文化"),
    ("美食", "美食"),
    ("夜景", "夜景"),
]
DEFAULT_PREFS = ["文化", "美食", "夜景"]  # 默认全选

_MODE_ICON = {"步行": "🚶", "骑行": "🚲", "地铁/公交": "🚇", "打车": "🚕"}
DEFAULT_MODE_ICON = "📍"

CSS = """
:root{
  /* ===== 设计令牌（Design Tokens）=====
     对齐「住哪好」Editorial 风格：暖米白底 + 奶茶棕强调 + 近黑文字。
     参考附图：路线指引条、大圆角卡片、顶部时段标签、黑色胶囊按钮、等宽数字。 */
  --bg:#FAF6EF; --card:#FFFFFF; --card-h:#F4F0E9; --card-hi:#FBF9F4;
  --gold:#B8956A; --gold-hi:#8B6914; --gold-line:#CDB28A; --gold-soft:rgba(184,149,106,.12);
  --txt:#1A1A1A; --sub:#5F6572; --soft:#A0A5AF; --line:#ECE7DE; --line-soft:#F1EDE6;
  --accent:#B85450; --accent2:#5A7A8A; --warn:#9A6B2E; --err:#B85C5C;

  /* 圆角 / 阴影 */
  --r-s:10px; --r-m:14px; --r-l:18px; --r-xl:22px; --r-pill:999px;
  --sh-card:0 1px 2px rgba(0,0,0,.03), 0 6px 22px rgba(0,0,0,.05);
  --sh-soft:0 1px 2px rgba(0,0,0,.04);
  --sh-pop:0 12px 36px rgba(0,0,0,.12);

  /* 字号层级 */
  --fs-hero:50px; --fs-h1:24px; --fs-h2:19px; --fs-h3:16px;
  --fs-body:15px; --fs-sm:13.5px; --fs-xs:12px; --fs-cap:11px;
  --lh-tight:1.25; --lh-body:1.75; --lh-loose:1.9;
}
/* 全局盒模型 + 数字等宽（时间/距离/价格对齐扫描） */
.gradio-container *{box-sizing:border-box!important;}
.gradio-container .num,.it-chip,.it-tt,.it-period,.it-dayhead .period,
.it-route .r-gap,.pois .no,.it-poi-no{font-variant-numeric:tabular-nums;}
html,body{background:var(--bg)!important;}
body{color:var(--txt);margin:0;-webkit-font-smoothing:antialiased;}
.gradio-container{
  background:var(--bg)!important;color:var(--txt)!important;
  width:100%!important;max-width:960px;margin:0 auto!important;padding:10px 24px 56px!important;
  box-sizing:border-box!important;overflow-x:hidden!important;overflow-wrap:anywhere!important;
  font-family:'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Noto Sans SC',system-ui,-apple-system,sans-serif!important;
  letter-spacing:.01em;line-height:var(--lh-body)!important;
}
/* 保证生成结果注入前后，核心内容区宽度与布局完全一致：
   1) 内容区与内部弹性容器都可收缩到 min-width:0，避免被长 URL/宽图撑开；
   2) 长 token 允许折行，杜绝横向溢出导致的"变宽"。 */
.gradio-container .markdown,.gradio-container .md{overflow-wrap:anywhere!important;}
.it-wrap,.it-timeline,.it-card,.it-map,.it-alert,.it-insight,
.it-actions,.it-meta,.it-dayhead,.it-footer,.it-swap,.pois,.it-map .legend,
.it-map .pois,.it-map-head,.it-route{min-width:0;max-width:100%;box-sizing:border-box;}
.it-footer{display:flex;flex-wrap:wrap;gap:10px 22px;justify-content:space-between;align-items:flex-start;}
.it-footer a,.it-btn,.it-why p,.it-hotel,.it-desc,.it-intro,.it-route{overflow-wrap:anywhere;word-break:break-word;}
.it-map-head h2{overflow-wrap:anywhere;word-break:break-word;}
html,body{overflow-x:hidden;}
.gradio-container textarea,.wrap textarea,.gradio-container input[type=text],
.gradio-container input[type=number],.gradio-container select{
  background:var(--card)!important;color:var(--txt)!important;
  border:1px solid var(--line)!important;border-radius:var(--r-m)!important;
  font-size:15px!important;transition:border-color .15s ease, box-shadow .15s ease;
  box-shadow:var(--sh-soft)!important;
}
.gradio-container textarea:focus,.wrap textarea:focus,
.gradio-container input[type=text]:focus{
  border-color:var(--gold)!important;outline:none!important;box-shadow:0 0 0 3px var(--gold-soft)!important;
}
.gradio-container label span,.gradio-container .label-wrap span{color:var(--sub)!important;font-size:13px;font-weight:600;}
.gradio-container .multiselect,.gradio-container .dropdown,.gradio-container .wrap{
  background:var(--card)!important;color:var(--txt)!important;
  border:1px solid var(--line)!important;border-radius:var(--r-m)!important;
}
.gradio-container .token,.gradio-container .selected,.gradio-container .tag{
  background:#1A1A1A!important;color:#fff!important;border-radius:var(--r-pill)!important;font-weight:500;
}
.gradio-container button,.gradio-button{
  font-weight:500;border:none!important;border-radius:var(--r-pill)!important;transition:.15s ease;
}
.gradio-container button.primary,.gradio-container .primary{
  background:var(--gold)!important;color:#fff!important;font-size:16px!important;
  box-shadow:0 2px 12px rgba(184,149,106,.28);
}
.gradio-container button.primary:hover,.gradio-container .primary:hover{background:var(--gold-hi)!important;}
.gradio-container .markdown,.gradio-container .md,.gradio-container p{color:var(--txt)!important;}
.gradio-container a{color:var(--gold-hi)!important;text-decoration:none;}
.gradio-container a:hover{text-decoration:underline;}

/* ===== Hero：编辑式极简（对照「住哪好」首屏，参考附图一） ===== */
.g-hero{text-align:left;padding:64px 4px 30px;border-bottom:1px solid var(--line);margin-bottom:34px;}
.g-hero .kicker{
  font-size:12px;letter-spacing:4.5px;color:var(--soft);font-weight:600;
  text-transform:uppercase;margin-bottom:18px;
}
.g-hero h1{
  margin:0;font-size:var(--fs-hero);line-height:1.06;font-weight:300;
  letter-spacing:.04em;color:var(--txt);
}
.g-hero .sub{color:var(--sub);font-size:16px;margin-top:18px;letter-spacing:.4px;line-height:var(--lh-body);}
.g-hero .sub em{color:var(--gold-hi);font-style:normal;font-weight:600;}
.pill{
  display:inline-flex;align-items:center;gap:6px;margin-top:24px;padding:8px 18px;
  border-radius:var(--r-pill);background:#1A1A1A;color:#fff;
  font-size:12.5px;letter-spacing:.6px;line-height:1;
}
.pill b{color:var(--gold);font-weight:600;}

/* ===== 结果区：引用块 / 地图 / 时间轴 ===== */
.it-wrap{display:flex;flex-direction:column;gap:26px;margin-top:26px;}
.it-card{background:var(--card);border:1px solid var(--line);border-radius:var(--r-l);padding:24px 28px;box-shadow:var(--sh-card);}
.it-insight{
  border-left:3px solid var(--gold-line);background:var(--card-hi);
  border-radius:8px var(--r-l) var(--r-l) 8px;padding:22px 26px;
  box-shadow:inset 4px 0 0 var(--gold-soft);
}
.it-insight .kicker{font-size:12px;letter-spacing:3.5px;color:var(--gold-hi);font-weight:700;text-transform:uppercase;margin-bottom:12px;}
.it-intro{font-size:17px;line-height:var(--lh-loose);font-weight:400;}
.it-hotel{margin-top:16px;color:var(--sub);font-size:14px;display:flex;align-items:baseline;gap:8px;}
.it-hotel b{color:var(--txt);font-weight:600;}
.it-hotel:empty{margin-top:0;}

/* 路线草图地图 */
.it-map{border:1px solid var(--line);border-radius:var(--r-l);background:var(--card);overflow:hidden;box-shadow:var(--sh-card);}
.it-map-head{
  display:flex;align-items:center;justify-content:space-between;gap:16px;
  padding:18px 24px;border-bottom:1px solid var(--line);
}
.it-map-head h2{margin:0;font-size:var(--fs-h3);font-weight:600;letter-spacing:.5px;color:var(--txt);}
.it-map-head .kicker{
  font-size:12px;letter-spacing:3px;color:var(--soft);font-weight:600;
  text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.it-map svg{width:100%;height:auto;display:block;background:var(--card-hi);}
.it-map .legend{
  display:flex;flex-wrap:wrap;gap:10px 24px;align-items:center;padding:14px 24px;
  border-top:1px solid var(--line);color:var(--sub);font-size:var(--fs-xs);
}
.it-map .legend .sw{display:inline-block;width:26px;height:4px;border-radius:2px;vertical-align:middle;margin-right:8px;}
.it-map .legend .leg{display:inline-flex;align-items:center;}
.it-map .pois{
  display:grid;grid-template-columns:1fr 1fr;gap:6px 32px;padding:14px 24px 20px;
  border-top:1px dashed var(--line);color:var(--sub);font-size:14px;
}
.it-map .pois .row{display:flex;align-items:baseline;gap:10px;padding:7px 0;}
.it-map .pois .no,.it-map .pois .poi-item .poi-info .no{
  font-variant-numeric:tabular-nums;font-weight:700;color:var(--gold-hi);min-width:18px;
}
.it-map .poi-item{display:flex;align-items:center;gap:12px;padding:7px 0;}
.it-map .poi-thumb{
  width:60px;height:60px;border-radius:var(--r-s);object-fit:cover;flex:none;
  border:1px solid var(--line);background:var(--card-h);box-shadow:var(--sh-soft);
}
.it-map .poi-info{display:flex;align-items:baseline;gap:8px;color:var(--txt);font-size:14px;min-width:0;}
.it-map .poi-info span{overflow-wrap:anywhere;word-break:break-word;}

/* ===== 时间轴卡（参考附图三：时段标签 + 大标题 + 实景主图 + 黑色胶囊按钮） ===== */
.it-timeline{position:relative;margin:2px 0 0 4px;padding-left:0;}
.it-dayhead{
  display:flex;align-items:baseline;justify-content:space-between;margin:4px 0 24px;
  padding-bottom:16px;border-bottom:1px solid var(--line);
}
.it-dayhead .h{font-size:var(--fs-h1);font-weight:600;color:var(--txt);letter-spacing:.02em;}
.it-dayhead .h small{color:var(--soft);font-weight:400;font-size:14px;margin-left:14px;letter-spacing:.5px;white-space:nowrap;}
.it-dayhead .period{color:var(--soft);font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap;}
.it-node{position:relative;margin-bottom:26px;padding-left:32px;}
.it-dot{
  position:absolute;left:0;top:30px;width:16px;height:16px;border-radius:50%;
  background:#fff;border:2px solid var(--gold);box-shadow:0 0 0 4px rgba(205,178,138,.28);
}
.it-timeline:before{content:"";position:absolute;left:7px;top:80px;bottom:10px;width:2px;background:var(--line);}
.it-timeline .it-dayhead{position:relative;padding-right:2px;}
.it-card .it-tt{
  color:var(--gold-hi);font-weight:700;font-size:12px;letter-spacing:2px;text-transform:uppercase;
  display:inline-flex;align-items:center;gap:8px;
}
.it-card .it-tt::before{content:"";width:18px;height:2px;background:var(--gold);border-radius:2px;}
.it-name{font-size:26px;font-weight:600;line-height:1.22;margin-top:10px;letter-spacing:.01em;}
.it-tags{display:flex;flex-wrap:wrap;gap:6px 12px;margin-top:12px;color:var(--sub);font-size:var(--fs-xs);letter-spacing:.3px;}
.it-tags span{color:var(--txt);font-weight:500;}
.it-tags .t-dot{color:var(--gold-line);}
/* 景点实景主图（应参照附图三：16:9 大圆角出血图） */
.it-photo{
  width:100%;height:280px;object-fit:cover;border-radius:var(--r-m);
  box-shadow:var(--sh-soft);display:block;margin:16px 0 0;border:1px solid var(--line-soft);
}
.it-meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px;}
.it-chip{
  background:var(--card-hi);border:1px solid var(--line);color:var(--txt);
  border-radius:var(--r-pill);padding:6px 14px;font-size:var(--fs-sm);font-variant-numeric:tabular-nums;
  line-height:1.4;
}
.it-chip .off{color:var(--soft);font-weight:400;}
.it-desc{color:var(--sub);font-size:14px;margin-top:16px;line-height:var(--lh-body);}
.it-actions{margin-top:20px;display:flex;flex-wrap:wrap;align-items:flex-start;gap:14px;padding-top:18px;border-top:1px dashed var(--line);}
.it-why{flex:1;min-width:220px;}
.it-why summary{cursor:pointer;color:var(--gold-hi);font-weight:600;font-size:var(--fs-sm);user-select:none;line-height:1.6;}
.it-why p{
  margin:14px 0 0;color:var(--sub);font-size:14.5px;line-height:var(--lh-body);
}
.it-btn{
  display:inline-flex;align-items:center;gap:7px;background:#1A1A1A;color:#fff!important;
  font-weight:500;font-size:13.5px;padding:11px 22px;border-radius:var(--r-pill);
  text-decoration:none!important;box-shadow:0 2px 8px rgba(0,0,0,.14);transition:background .15s ease, transform .15s ease;
}
.it-btn:hover{background:#333;transform:translateY(-1px);}
.it-btn:active{transform:translateY(0);}
/* 站点间路线指引条（参考附图三：浅灰分隔条，箭头 + 时长，承上启下） */
.it-route{
  display:flex;flex-wrap:wrap;align-items:center;gap:8px;
  margin:0 0 26px 32px;padding:11px 18px;
  background:var(--card-hi);border:1px solid var(--line);border-radius:var(--r-s);
  color:var(--sub);font-size:var(--fs-sm);line-height:1.6;box-shadow:var(--sh-soft);
}
.it-route .ric{font-size:15px;line-height:1;}
.it-route .r-from,.it-route .r-to{font-weight:600;color:var(--txt);overflow-wrap:anywhere;word-break:break-word;}
.it-route .r-arrow{color:var(--gold);font-weight:700;}
.it-route .r-gap{margin-left:auto;color:var(--soft);font-size:var(--fs-xs);white-space:nowrap;font-variant-numeric:tabular-nums;}
.it-note{color:var(--soft);font-size:13px;margin-top:6px;}
.it-alert{
  background:var(--card-hi);border:1px solid var(--line);border-left:3px solid var(--warn);
  border-radius:var(--r-s);color:var(--txt);padding:16px 20px;font-size:14px;line-height:var(--lh-body);
  box-shadow:inset 4px 0 0 rgba(154,107,46,.12);
}
.it-alert b{color:var(--warn);display:block;margin-bottom:6px;}
.it-alert ul{margin:6px 0 0;padding-left:18px;}
.it-alert li{margin:3px 0;color:var(--sub);}
.it-alert li b{display:inline;color:var(--txt);}
.it-tpl{
  margin-top:16px;color:var(--warn);font-size:13px;
  background:var(--card-hi);border:1px dashed var(--line);border-radius:var(--r-s);padding:11px 15px;
}
.it-footer{
  display:flex;flex-wrap:wrap;gap:10px 22px;justify-content:space-between;align-items:center;
  margin-top:34px;padding-top:20px;border-top:1px solid var(--line);color:var(--soft);font-size:13px;
}
.it-footer a{color:var(--gold-hi);font-weight:600;}
/* 页脚品牌区：极简中性，层级用字号/字重/字距区分，不引入新色 */
.it-footer .brand{display:flex;flex-direction:column;gap:4px;min-width:0;}
.it-footer .brand-name{color:var(--txt);font-size:13.5px;font-weight:600;letter-spacing:.06em;line-height:1.4;}
.it-footer .brand-name em{
  font-style:normal;color:var(--soft);font-size:10.5px;font-weight:600;
  letter-spacing:.18em;margin-left:8px;vertical-align:baseline;
}
.it-footer .brand-sub{color:var(--soft);font-size:12px;letter-spacing:.03em;line-height:1.5;}
.g-err{color:var(--err);font-size:15px;white-space:pre-wrap;line-height:var(--lh-body);}

/* ===== 一键换站（统一到设计系统） ===== */
.it-swap{border:1px solid var(--line);border-radius:var(--r-l);background:var(--card);margin-top:30px;overflow:hidden;width:100%!important;box-sizing:border-box;box-shadow:var(--sh-card);}
.it-app-foot{margin-top:8px;}
.it-swap .label-wrap{color:var(--txt);font-size:15px;font-weight:600;padding-left:2px;}
.it-swap .container{background:var(--card)!important;border:none!important;box-shadow:none!important;}
.it-swap-sub{color:var(--sub);font-size:13px;line-height:1.75;margin:0 0 16px;}
.it-swap .swap-seq{
  background:var(--card)!important;border:1px solid var(--line)!important;border-radius:var(--r-s)!important;
}
.it-btn-pill{
  height:100%;min-height:42px;border-radius:var(--r-pill);font-weight:500;font-size:14.5px;
  background:#1A1A1A;color:#fff!important;transition:.15s ease;
}
.it-btn-pill:hover{background:#333;}
.it-btn-pill:disabled,.it-btn-pill:disabled:hover{background:var(--line);color:var(--soft)!important;}
.swap-note{margin-top:2px;}
.swap-status{
  font-size:13.5px;line-height:1.7;padding:11px 15px;border-radius:var(--r-s);
  border-left:3px solid var(--line);background:var(--card-hi);color:var(--sub);
}
.swap-status.ok{border-left-color:var(--gold);color:var(--gold-hi);background:var(--gold-soft);}
.swap-status.warn{border-left-color:var(--warn);color:var(--warn);background:rgba(154,107,46,.10);}

@media (max-width:768px){
  .gradio-container{padding:6px 16px 34px!important;}
  .g-hero{padding:36px 4px 22px;}
  .g-hero h1{font-size:34px;letter-spacing:.03em;}
  .g-hero .sub{font-size:14.5px;letter-spacing:0;line-height:var(--lh-body);}
  .it-card{padding:18px 20px;border-radius:var(--r-m);}
  .it-wrap{gap:20px;margin-top:20px;}
  .it-dayhead .period,.it-dayhead .h small{display:none;}
  .it-timeline{margin-left:0;}
  .it-node{padding-left:26px;margin-bottom:22px;}
  .it-route{margin-left:26px;padding:10px 14px;}
  .it-route .r-gap{white-space:normal;}
  .it-photo{height:220px;border-radius:var(--r-s);}
  .it-map .pois{grid-template-columns:1fr;gap:6px;padding:8px 16px 14px;}
  .it-map .poi-thumb{width:54px;height:54px;}
  .it-map-head{padding:14px 16px;}
  .it-map .legend{padding:10px 16px;}
  .it-insight{padding:18px 18px;border-radius:6px var(--r-m) var(--r-m) 6px;}
  .it-name{font-size:22px;}
  .it-meta{flex-direction:column;align-items:flex-start;gap:6px;}
  .it-actions{flex-direction:column;} .it-btn{justify-content:center;width:100%;}
  .it-why{min-width:0;}
  .it-btn-pill{width:100%;justify-content:center;}
  .it-swap .label-wrap{font-size:14px;}
  .swap-status{font-size:13px;}
}
@media (max-width:420px){
  .g-hero h1{font-size:30px;}
  .it-name{font-size:20px;}
  .it-photo{height:190px;}
  .it-map .pois{gap:4px;}
}

/* ===== 统一四个输入模块（出差城市/入住酒店/会议描述/偏好）：字体·间距·对齐规范 ===== */
.it-form{display:flex;flex-direction:column;gap:18px;}
.it-form .it-field,.it-form .it-hotel-card{margin:0;}
/* 四个模块的标题文字使用完全一致的层级（字号/字重/颜色/行高）
   覆盖 Gradio 各组件（Textbox/Textarea/Dropdown）不同的标题元素 */
.gradio-container .it-form label,
.gradio-container .it-form label span,
.gradio-container .it-form .label-wrap span,
.gradio-container .it-form .block-title,
.gradio-container .it-form [data-testid="block-info"],
.it-hotel-t{
  color:var(--sub)!important;font-size:13px!important;font-weight:600!important;
  letter-spacing:.2px;line-height:1.6;
}
/* 四个模块的输入控件字号统一，避免大小不一 */
.gradio-container .it-form input,
.gradio-container .it-form textarea,
.gradio-container .it-form select,
.gradio-container .it-form .wrap{font-size:15px!important;}

/* 入住酒店 + 飞猪检索：合并为单一容器卡片 */
.it-hotel-card{
  background:var(--card);border:1px solid var(--line);border-radius:var(--r-l);
  padding:18px 20px 20px;box-shadow:var(--sh-card);
  display:flex;flex-direction:column;gap:14px;
}
.it-hotel-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 14px;}
.it-hotel-t{display:inline-flex;align-items:center;gap:8px;}
.it-hotel-hint{color:var(--sub);font-size:var(--fs-xs);line-height:1.6;}
/* 入住酒店输入框：去掉外边框与底色，直接融入卡片（无需再画一层框）
   注意圆角必须归零：Gradio 给 textarea 预设 border-radius（继承全局 --r-m）
   且 overflow 为 auto/hidden，配合 padding:0 会按圆角裁掉首行第一个字。 */
.it-hotel-key,
.it-hotel-key>div,
.it-hotel-key .wrap,
.it-hotel-key textarea{
  border:none!important;box-shadow:none!important;background:transparent!important;
  border-radius:0!important;
}
.it-hotel-key{padding:0!important;}
.it-hotel-key textarea{padding:0!important;min-height:auto!important;}
/* 隐藏标签文字（不隐藏输入框本身）
   根因：Gradio 前端对空 label 会回退成组件默认名（Textbox/Dropdown…），
   故此处双重保险——组件侧给非空 label + show_label=False + container=False，
   样式侧再把标签容器整体隐藏。 */
.it-hotel-key .label-wrap,
.it-hotel-key label:not(:has(textarea)),
.it-hotel-key label:not(:has(textarea)) span,
.it-hotel-key [data-testid="block-label"],
.it-hotel-key [data-testid="block-info"]{display:none!important;}
/* 检索按钮与选择框同一行、上下居中、尺寸协调（严格等高 44px）
   1) 强制不换行：Gradio Row 子项自带较大 min-width，会在中等宽度被挤到第二行；
   2) 抹掉 Gradio 为空标题/说明预留的占位（block-info + padded），
      否则下拉块高于按钮，按钮会被顶到视觉中线之上、看起来上下不对齐。 */
.it-hotel-ctrl{display:flex;align-items:center;gap:14px;flex-wrap:nowrap;}
.it-hotel-ctrl>*{min-width:0!important;}
.it-hotel-ctrl .it-hotel-btn{
  flex:0 0 auto;height:44px;min-height:44px;align-self:center;
  padding:0 22px;white-space:nowrap;
}
.it-hotel-ctrl>div{height:44px!important;min-height:44px!important;display:flex;align-items:center;}
.it-hotel-ctrl .it-hotel-sel{
  flex:1 1 auto;min-width:0;height:44px!important;min-height:44px!important;
  padding:0!important;border:none!important;background:transparent!important;
  display:flex;align-items:center;
}
.it-hotel-ctrl .it-hotel-sel .container{width:100%;padding:0!important;gap:0!important;}
.it-hotel-ctrl .it-hotel-sel [data-testid="block-info"]{display:none!important;}
.it-hotel-ctrl .it-hotel-sel .wrap{min-height:44px!important;}
.it-hotel-ctrl .it-hotel-sel input{height:auto!important;padding:0 34px 0 12px!important;}
@media (max-width:768px){
  .it-form{gap:14px;}
  .it-hotel-card{padding:14px 14px 16px;}
  .it-hotel-head{gap:6px 10px;}
}
/* 仅在极窄屏才把按钮与选择框改为纵向堆叠；常规宽度保持同一行、上下居中 */
@media (max-width:520px){
  .it-hotel-ctrl{flex-direction:column;align-items:stretch;}
  .it-hotel-ctrl .it-hotel-btn,.it-hotel-ctrl .it-hotel-sel{width:100%;flex:1 1 auto;}
}
"""

HERO_HTML = """
<div class="g-hero">
  <div class="kicker">2026 飞猪 AI 旅行创新大赛 · BLEISURE</div>
  <h1>商旅融合<br>行程官</h1>
  <div class="sub">会议反推空闲窗口，AI 替你裁决微行程 —— <em>带薪旅行，不耽误正事</em>。</div>
  <div class="pill">飞猪实时数据 · 空间感知选点 · <b>AI 对比裁决</b></div>
</div>
"""


def _esc(s):
    if s is None:
        return ""
    return html.escape(str(s))


def _meetings_from_text(text):
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


def _dwell_minutes(s):
    hh, mm = s["start"].split(":")
    end_hh, end_mm = s["end"].split(":")
    return int(end_hh) * 60 + int(end_mm) - int(hh) * 60 - int(mm)


def _verdict_for(stops, idx, pois, hotel, preferences):
    """为一站生成 F1 对比裁决：先取同窗口落选备选，再请 LLM/模板裁决。"""
    stop = stops[idx]
    alternatives = stop_alternatives(stops, idx, pois, hotel, preferences=preferences)
    return judge_verdict(stop, alternatives, _dwell_minutes(stop))


def _station_names(stops):
    return [f"{i + 1}. {s['poi']['name']}" for i, s in enumerate(stops)]


def _against_number(stops, choice):
    """把下拉选择（"1. 某某"）解析成站点下标；解析失败返回 -1。"""
    if not choice:
        return 0
    text = str(choice).split(".", 1)[0].strip()
    return int(text) - 1 if text.isdigit() and 1 <= int(text) <= len(stops) else -1


def _route_map_svg(stops, hotel):
    """按真实经纬度投影，画一张「路线草图」SVG 地图。

    标注：H(酒店) + 数字编号节点 + 行进方向箭头 + 每段距离/耗时标识 + 北向。
    坐标仅用于相对布局（等矩形投影），不claim精确到街道。
    """
    W, H = 520, 340
    PAD = 52
    pts = []
    for i, s in enumerate(stops):
        pts.append((s["poi"]["lat"], s["poi"]["lng"]))
    if not pts:
        return ""
    anchor = (hotel.get("lat", pts[0][0]), hotel.get("lng", pts[0][1]))

    lats = [anchor[0]] + [p[0] for p in pts]
    lngs = [anchor[1]] + [p[1] for p in pts]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)
    span_lat, span_lng = max_lat - min_lat, max_lng - min_lng
    # 千篇一律（全同点）时撑开一点，避免除零
    if span_lat < 1e-6:
        span_lat = 1e-6
    if span_lng < 1e-6:
        span_lng = 1e-6

    def px(lat, lng):
        x = PAD + (lng - min_lng) / span_lng * (W - 2 * PAD)
        y = PAD + (max_lat - lat) / span_lat * (H - 2 * PAD)
        return x, y

    hx, hy = px(*anchor)
    node_pts = [px(*p) for p in pts]

    def seg(p1, p2):
        return f"<path d='M{p1[0]:.1f} {p1[1]:.1f} C{(p1[0]+p2[0])/2:.1f} {p1[1]:.1f}," \
               f"{(p1[0]+p2[0])/2:.1f} {p2[1]:.1f},{p2[0]:.1f} {p2[1]:.1f}' " \
               f"fill='none' stroke='#B8956A' stroke-width='3.5' stroke-linecap='round'/>"

    line_paths = []
    line_paths.append(f"<path d='M{hx:.1f} {hy:.1f} "
                      f"C{(hx+node_pts[0][0])/2:.1f} {hy:.1f},{(hx+node_pts[0][0])/2:.1f} "
                      f"{node_pts[0][1]:.1f},{node_pts[0][0]:.1f} {node_pts[0][1]:.1f}' "
                      f"fill='none' stroke='#B8956A' stroke-width='3.5' stroke-linecap='round' "
                      f"marker-mid='url(#arr)'/>")
    for i, b in enumerate(node_pts[1:]):
        a = node_pts[i]
        line_paths.append(seg(a, b))

    # 每段中点标距离 + 交通方式
    labels = []
    prev = (hx, hy)
    for i, (lat, lng) in enumerate(pts):
        stop = stops[i]
        mx, my = (prev[0] + node_pts[i][0]) / 2, (prev[1] + node_pts[i][1]) / 2
        labels.append(
            f"<text x='{mx:.1f}' y='{my - 10:.1f}' text-anchor='middle' "
            f"font-size='12' font-weight='600' fill='#9A7B52' "
            f"stroke='#fff' stroke-width='3' paint-order='stroke'>"
            f"{stop['leg_km']}km · {_esc(stop['leg_mode'])} {stop['leg_min']}分</text>")
        prev = node_pts[i]

    nodes = [
        # 酒店
        f"<g><circle cx='{hx:.1f}' cy='{hy:.1f}' r='11' fill='#1A1A1A'/>"
        f"<circle cx='{hx:.1f}' cy='{hy:.1f}' r='11' fill='none' stroke='#fff' "
        f"stroke-opacity='.5' stroke-width='2'/>"
        f"<circle cx='{hx:.1f}' cy='{(hy + 11):.1f}' r='4' fill='#1A1A1A'/>"
        f"<text x='{hx:.1f}' y='{(hy + 4):.1f}' text-anchor='middle' fill='#fff' "
        f"font-size='12' font-weight='700'>H</text></g>"
    ]
    for i, (x, y) in enumerate(node_pts, 1):
        nodes.append(
            f"<g><circle cx='{x:.1f}' cy='{y:.1f}' r='11' fill='#fff' stroke='#B8956A' "
            f"stroke-width='2.5'/>"
            f"<text x='{x:.1f}' y='{(y + 4):.1f}' text-anchor='middle' fill='#1A1A1A' "
            f"font-size='12.5' font-weight='700'>{i}</text></g>")

    total = sum(s["leg_km"] for s in stops)
    return (f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='路线示意图'>"
            f"<defs><marker id='arr' viewBox='0 0 10 10' refX='5' refY='5' "
            f"markerWidth='7' markerHeight='7' orient='auto-start-reverse'>"
            f"<path d='M0 0 L10 5 L0 10 z' fill='#B8956A' opacity='.85'/></marker></defs>"
            f"<rect width='{W}' height='{H}' fill='#FBF9F5'/>"
            f"<text x='{W-16}' y='26' text-anchor='end' font-size='13' fill='#999'>北 ↑</text>"
            + "".join(line_paths) + "".join(labels) + "".join(nodes)
            + f"<text x='26' y='{H-16}' font-size='12.5' fill='#999'>全程约 "
            f"{total:.1f}km · 等矩形投影示意</text></svg>")


def _notes_to_text(notes):
    """把结构化/字符串 notes 摊平成用于地图注意区的短句。"""
    out = []
    for n in notes or []:
        if isinstance(n, dict):
            out.append(n["text"])
        else:
            out.append(str(n))
    return out


def _stop_card(i, s, verdict):
    p = s["poi"]
    icon = _MODE_ICON.get(s["leg_mode"], DEFAULT_MODE_ICON)
    verdict = verdict or "（未生成裁决：配置 DASHSCOPE_API_KEY 后走百炼实时生成）"
    why = (
        f"<details class='it-why'><summary>🤖 AI 为什么选这里 · 对比裁决</summary>"
        f"<p>{_esc(verdict)}</p></details>"
    )
    link = (
        f"<a class='it-btn' href='{_esc(p['url'])}' target='_blank' rel='noopener'>"
        f"查看详情/预订 ↗</a>"
        if p.get("url")
        else ""
    )
    desc = f"<div class='it-desc'>{_esc(p['desc'])}</div>" if p.get("desc") else ""
    free = {"FREE": "免费", "NOT_FREE": "收费"}.get(p.get("free"), "价格见详情")
    dwell = _dwell_minutes(s)
    photo = f"<img class='it-photo' loading='lazy' src='{_esc(_poi_photo(p))}' alt='{_esc(p['name'])}'/>"
    hours = open_hours_label(p)   # 开放时段：飞猪详情真实值，或按品类估算（带"参考"）
    tag_items = [f"<span>{_esc(s['start'])}</span>",
                 f"<span>停留 {dwell} 分钟</span>",
                 f"<span>{_esc(free)}</span>"]
    if hours:
        tag_items.append(f"<span>🕐 开放 {_esc(hours)}</span>")
    tags = ("<div class='it-tags'>"
            + "<span class='t-dot'>·</span>".join(tag_items) + "</div>")
    return f"""
<div class="it-node">
  <div class="it-dot"></div>
  <div class="it-card">
    <div class="it-tt">STOP {i + 1}</div>
    <div class="it-name">{_esc(p['name'])}</div>
    {tags}
    {photo}
    <div class="it-meta">
      <span class="it-chip">📍 <span class="off">距酒店</span> {s['dist_km']} km</span>
      <span class="it-chip">{icon} <span class="off">{_esc(s['leg_mode'])}</span> {s['leg_min']} 分钟
           · <span class="off">路程</span> {s['leg_km']} km</span>
    </div>
    {desc}
    <div class="it-actions">
      {why}
      {link}
    </div>
  </div>
</div>"""


def _route_strip_html(prev_name, s):
    """站点间的路线指引条：前一站 → 本站 + 交通方式/里程/耗时。

    对齐参考附图三的浅灰分隔条，把"空间位移"从卡片里剥出来，避免单卡片过载。
    数字全部来自 s 的 leg 计算结果，不编造。
    """
    icon = _MODE_ICON.get(s["leg_mode"], DEFAULT_MODE_ICON)
    return (f"<div class='it-route'><span class='ric'>{icon}</span>"
            f"<span class='r-from'>{_esc(prev_name)}</span>"
            f"<span class='r-arrow'>→</span>"
            f"<span class='r-to'>{_esc(s['poi']['name'])}</span>"
            f"<span class='r-gap'>{_esc(s['leg_mode'])} · {s['leg_km']} km · "
            f"{s['leg_min']} 分钟</span></div>")


def _poi_photo(p):
    """取景点实景照：优先用飞猪 POI 返回的 pic；无则按景点名生成一张示意实景照兜底。

    仅放行 http(s) 图片地址，避免把非法/相对路径塞进 <img>。
    """
    url = (p.get("pic") or "").strip()
    if url.startswith(("http://", "https://")):
        return url
    prompt = quote(f"清晰真实的实景照片：中国著名旅游景点 {p.get('name', '景点')}，"
                   f"白天自然光，手机随手拍的真实感，画面干净构图端正")
    return (f"https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image"
            f"?prompt={prompt}&image_size=square")


def _map_card_html(hotel, stops, notes, notes_text):
    map_svg = _route_map_svg(stops, hotel)
    pois_rows = "".join(
        f"<div class='poi-item'><img class='poi-thumb' loading='lazy' "
        f"src='{_esc(_poi_photo(s['poi']))}' alt='{_esc(s['poi']['name'])}'/>"
        f"<span class='poi-info'><span class='no'>{i + 1}</span>"
        f"<span>{_esc(s['poi']['name'])}</span></span></div>"
        for i, s in enumerate(stops)
    )
    hotel_pic = _poi_photo(hotel) if (hotel.get("pic") or "").strip() else ""
    if hotel_pic:
        hotel_row = (f"<div class='poi-item'><img class='poi-thumb' loading='lazy' "
                     f"src='{_esc(hotel_pic)}' alt='{_esc(hotel['name'])}'/>"
                     f"<span class='poi-info'><span class='no'>H</span>"
                     f"<span><b>{_esc(hotel['name'])}</b> · 出发</span></span></div>")
    else:
        hotel_row = (f"<div class='row'><span class='no'>H</span>"
                     f"<span><b>{_esc(hotel['name'])}</b> · 出发</span></div>")
    caution = "".join(
        f"<div class='row'><span class='no'>!</span>"
        f"<span>{_esc(t)}</span></div>" for t in notes_text
    )
    legend = (
        "<div class='legend'>"
        "<span class='leg'><span class='sw' style='background:#B8956A'></span>本次微行程</span>"
        "<span class='leg'><span class='no' style='min-width:0'>H</span>酒店出发 · 依次编号浏览</span>"
        "<span class='leg' style='color:#999'>地图为坐标示意，照片取自飞猪 POI 资源</span>"
        "</div>"
    )
    notes_block = (f"<div style='border-top:1px dashed var(--line);padding:12px 22px 8px'><b "
                   f"style='color:#9A6B2E;font-size:13px'>⚠ 注意事项</b>{caution}</div>"
                   if caution else "")
    return (
        f"<div class='it-map'>"
        f"<div class='it-map-head'><h2>路线示意图</h2>"
        f"<span class='kicker'>{_esc(hotel['name'])} → {len(stops)} 站夜景</span></div>"
        f"{map_svg}{legend}"
        f"<div class='pois'>{hotel_row}{pois_rows}</div>"
        f"{notes_block}"
        f"</div>"
    )


def _timeline_html(city, hotel, stops, notes, intro, verdicts, llm_online):
    start = stops[0]["start"] if stops else ""
    end = stops[-1]["end"] if stops else ""
    notes_text = _notes_to_text(notes)
    parts = [
        "<div class='it-wrap'>",
        # INSIGHT 引用块
        "<div class='it-card it-insight'>"
        "<div class='kicker'>INSIGHT · 为什么这样排</div>"
        f"<div class='it-intro'>{_esc(intro)}</div>"
        f"<div class='it-hotel'>🏨 <b>{_esc(hotel['name'])}</b> · {_esc(hotel.get('address', ''))}</div>"
        "</div>",
        # 路线草图（含「⚠ 注意事项」精简说明）
        _map_card_html(hotel, stops, notes, notes_text),
    ]
    parts.append(
        f"<div class='it-timeline'><div class='it-dayhead'>"
        f"<div class='h'>会议间隙微行程</div>"
        f"<div class='period'>{_esc(start)} – {_esc(end)}</div>"
        f"</div>"
    )
    # 每站前插一条"上一站 → 本站"的路线指引条（承上启下，位移信息剥离出卡片）
    nodes_html = []
    prev_name = hotel["name"]
    for i, s in enumerate(stops):
        nodes_html.append(_route_strip_html(prev_name, s))
        nodes_html.append(_stop_card(i, s, verdicts[i] if i < len(verdicts) else ""))
        prev_name = s["poi"]["name"]
    parts += nodes_html
    parts.append("</div>")
    if not llm_online:
        parts.append(
            "<div class='it-tpl'>当前为本地模板文案，配置 "
            "DASHSCOPE_API_KEY 后走百炼实时生成。</div>"
        )
    parts.append("</div>")
    return "".join(parts)


def _error_html(msg):
    return (
        f"<div class='it-wrap'><div class='it-card'>"
        f"<div class='g-err'>{_esc(msg)}</div></div></div>"
    )


FOOTER_HTML = (
    "<div class='it-footer'>"
    "<div class='brand'>"
    "<span class='brand-name'>商旅融合行程官"
    "<em>SHANGLVRONGHEXINGCHENGUAN</em></span>"
    "<span class='brand-sub'>fly.ai 实时数据 + AI 本地知识 · 飞猪预订闭环</span>"
    "</div>"
    "<a href='https://github.com/Geohzy/bleisure-planner' "
    "target='_blank' rel='noopener'>GitHub 仓库 ↗</a>"
    "</div>"
)


def _make_state(stops, notes, hotel, pois, preferences, intro=""):
    """把一次规划结果打包成可复用的 state（换站时据此局部重渲染）。"""
    return {
        "hotel": hotel, "pois": pois, "preferences": preferences,
        "stops": stops, "notes": notes, "intro": intro,
    }


def _render_from_state(state):
    """不重跑 5 步流水线，只根据 state 重渲染行程卡并实时算每站裁决。

    有百炼 Key 时每站裁决是一次独立的 HTTP 调用，串行会叠加成 N×单次耗时；
    这里把 verdicts 与 intro 一起丢进线程池并行发起。
    无 Key 时裁决走本地模板（<1s），并行不带来额外开销。
    """
    stops = state["stops"]
    n = len(stops)
    t0 = time.perf_counter()

    def _v(i):
        t = time.perf_counter()
        v = _verdict_for(stops, i, state["pois"], state["hotel"],
                         state["preferences"])
        print(f"   verdict 站 {i} {time.perf_counter()-t:.2f}s")
        return i, v

    existing_intro = state.get("intro")
    need_intro = existing_intro is None

    with ThreadPoolExecutor(max_workers=min(n + (1 if need_intro else 0), 8)) as ex:
        futures = [ex.submit(_v, i) for i in range(n)]
        intro_future = ex.submit(_compute_intro,
                                 state["hotel"].get("_city", ""),
                                 state["hotel"]["name"],
                                 stops) if need_intro else None
        verdicts = [None] * n
        for fut in futures:
            i, v = fut.result()
            verdicts[i] = v
        intro = intro_future.result() if intro_future else existing_intro
        if need_intro:
            state["intro"] = intro  # 回写，后续无效操作重渲染时不必再调模型

    print(f"   _render_from_state 文案耗时 {time.perf_counter()-t0:.2f}s")
    llm_online = bool(os.environ.get("DASHSCOPE_API_KEY"))
    return _timeline_html(_esc(state["hotel"].get("_city", "")),
                          state["hotel"], stops, state["notes"],
                          intro, verdicts, llm_online)


def _build_intro(state):
    name = ", ".join(s["poi"]["name"] for s in state["stops"]) or ""
    return f"带薪旅行的精髓：会开完了，快乐还没下班。今晚 {name}。" if name \
        else "带薪旅行的精髓：会开完了，快乐还没下班。"


def _compute_intro(city, hotel_name, stops):
    try:
        return generate_intro(city, hotel_name, stops)
    except Exception as e:
        print(f"[app] 开场白降级：{e}")
        return _build_intro({"stops": stops})


def _swap_note(text, kind="info"):
    """换站反馈的色板化 HTML：ok(暖金成功) / warn(琥珀提示) / info(中性)。"""
    return f"<div class='swap-status {kind}'>{_esc(text)}</div>"


def do_swap(choice, state):
    """一键换站：从 state 的 POI 池里换掉指定站，只重新渲染时间轴，不重跑 5 步。"""
    if not state:
        return (_error_html("请先生成一次行程再换站。"), gr.update(choices=[]),
                _swap_note("请先生成一次行程再换站。", "warn"), None)
    stops = list(state["stops"])
    idx = _against_number(stops, choice)
    if idx < 0:
        return (_render_from_state(state),
                gr.update(choices=_station_names(stops), value=choice),
                _swap_note("请先选择要换的站点。", "warn"), state)
    original_name = stops[idx]["poi"]["name"]
    new_stops, msg = swap_stop(stops, idx, state["pois"], state["hotel"],
                               preferences=state["preferences"])
    swapped = (0 <= idx < len(new_stops)
               and new_stops[idx]["poi"]["name"] != original_name)
    if swapped:
        note = _swap_note(
            f"第 {idx + 1} 站已替换为「{new_stops[idx]['poi']['name']}」，"
            f"其余站点与时间轴不变。", "ok")
    else:
        note = _swap_note(msg or "该窗口暂无合适备选，保留了原站点。", "warn")
    state = _make_state(new_stops, state["notes"], state["hotel"], state["pois"],
                        state["preferences"], None)
    html_out = _render_from_state(state)
    return (html_out, gr.update(choices=_station_names(new_stops),
                                value=(_station_names(new_stops)[idx]
                                       if idx < len(new_stops) else None)),
            note, state)


DEFAULT_HOTEL_LABEL = "锦江之星（杭州西湖店）浙江省杭州市上城区浣纱路126号"


def _resolve_hotel(city, label, pool=()):
    """把用户输入/检索选择的酒店转成结构化 hotel dict。

    - 若 label 命中已检索的 POI 池（hotel_pool）里的酒店名，直接复用其坐标；
    - 否则用 label 作为关键词调飞猪 search_hotel 检索真实经纬度；
    - 兜底回退到默认示例酒店（锦江之星·杭州西湖店）所在城市搜索结果。
    """
    label = (label or "").strip()
    for h in (pool or []):
        if h.get("name") and label and h["name"] == label:
            return dict(h)
    if label:
        hits = search_hotel(city, label)
        if hits:
            return hits[0]
    hits = search_hotel(city, "西湖")
    if hits:
        return hits[0]
    raise RuntimeError("未找到酒店，请检查城市 / 酒店检索关键词。")


def do_search_hotel(city, kw):
    """「检索酒店」按钮：用飞猪 search_hotel 拉取候选酒店填充下拉框并缓存池。"""
    try:
        hotels = search_hotel(city, (kw or "").strip() or "酒店")
        names = [h["name"] for h in hotels if h.get("name")]
        if not names:
            return gr.update(choices=[], value=None,
                             placeholder="未检索到酒店，请换关键词"), []
        return gr.update(choices=names, value=names[0]), hotels
    except Exception as e:
        print(f"[app] 检索酒店失败：{e!r}")
        return gr.update(choices=[], value=None,
                         placeholder="检索失败，可直接在输入框填写入住酒店"), []


def build_itinerary(city, hotel_label, hotel_pool, meeting_text, preferences,
                    prog=gr.Progress()):
    """完整链路：返回整页 HTML。任何一步失败都返回中文提示，不向页面吐 traceback。"""
    try:
        total0 = time.perf_counter()
        prog(0.1, "第 1/5 步：搜索/确认酒店…")
        meetings = _meetings_from_text(meeting_text)
        t1 = time.perf_counter()
        print(f"   会议解析 {t1 - total0:.2f}s")

        hotel = _resolve_hotel(city, hotel_label, hotel_pool)
        hotel["_city"] = city
        print(f"   酒店确认 {time.perf_counter() - t1:.2f}s")

        prog(0.3, "第 2/5 步：搜索景点…")
        queries = build_queries(preferences)
        pois = search_poi_multi(city, queries)
        if not pois:
            return _error_html(
                "⚠️ 未抓取到 POI：请确认 FLYAI_API_KEY 已设置、flyai CLI 已安装"
                "（npm i @fly-ai/flyai-cli）、网络可用。")
        t2 = time.perf_counter()

        prog(0.5, "第 3/5 步：解析会议时间段…")
        # 每个空闲窗口两端各预留 30 分钟缓冲，确保微旅行不失手压到工作时段
        windows = apply_buffer(free_windows_from_meetings(meetings), 30)

        prog(0.7, "第 4/5 步：规划行程（含交通时间）…")
        stops, notes = plan(pois, hotel, windows, preferences=preferences)
        if not stops:
            return _error_html(
                "⚠️ 没有排入任何站点（空闲窗口太碎，或半径内无 POI）。")
        print(f"   plan 耗时 {time.perf_counter() - t2:.2f}s")

        prog(0.85, "第 5/5 步：生成文案与裁决…")
        # intro 不在这里单独生成，交给 _render_from_state 与各站裁决并行发起
        state = _make_state(stops, notes, hotel, pois, preferences, None)
        html_out = _render_from_state(state)
        print(f"   完整链路总耗时 {time.perf_counter() - total0:.2f}s")

        prog(1.0, "完成")
        choices = _station_names(stops)
        return (html_out, gr.update(choices=choices, value=choices[0] if choices else None),
                "", state)

    except Exception as e:
        print(f"[app] 生成失败：{e!r}")
        root = str(e).strip()[:180]
        return _error_html(
            f"⚠️ 生成失败：{root}\n\n"
            "请按提示排除问题，并确认：\n"
            "1. 已安装 flyai CLI：npm i @fly-ai/flyai-cli；\n"
            "2. 已设置 FLYAI_API_KEY（免费试用额度用尽需到 "
            "flyai.open.fliggy.com 获取正式 Key）；\n"
            "3. 已设置 DASHSCOPE_API_KEY（文案/会议解析走百炼）；\n"
            "4. Key 只从环境变量读取，未写进代码。"), gr.update(choices=[]), "", None


with gr.Blocks(title="商旅融合行程官 · 在线 Demo") as demo:
    gr.HTML(HERO_HTML)

    with gr.Column(elem_classes="it-form"):
        city = gr.Textbox(label="出差城市", value="杭州", elem_classes="it-field")
        with gr.Column(elem_classes="it-hotel-card"):
            gr.HTML("<div class='it-hotel-head'>"
                    "<span class='it-hotel-t'>🏨 入住酒店</span>"
                    "<span class='it-hotel-hint'>自行填写实际入住的酒店名称/地址，或检索飞猪酒店后一键选用</span>"
                    "</div>")
            # 注意：label 不能传空串——Gradio 前端会把空 label 回退成默认名 “Textbox”。
            # 这里给一个非空 label，再用 show_label=False + container=False 隐藏它。
            hotel_label = gr.Textbox(
                label="入住酒店", show_label=False, container=False,
                value=DEFAULT_HOTEL_LABEL, lines=2,
                elem_classes="it-hotel-key",
            )
            with gr.Row(equal_height=False, elem_classes="it-hotel-ctrl"):
                hotel_btn = gr.Button("🔍 检索酒店（飞猪）", scale=2,
                                      elem_classes="it-btn-pill it-hotel-btn")
                hotel_dropdown = gr.Dropdown(
                    label="", choices=[], interactive=True, scale=5,
                    elem_classes="it-hotel-sel",
                )
            hotel_pool = gr.State()
        meeting_text = gr.Textbox(
            label="会议描述（一句话）",
            value="明天下午2点到4点开会，晚上7点半客户晚宴",
            lines=3, elem_classes="it-field",
        )
        prefs = gr.Dropdown(
            label="偏好（可多选）",
            choices=PREFERENCE_OPTIONS,
            value=DEFAULT_PREFS,
            multiselect=True, interactive=True, elem_classes="it-field",
        )
    btn = gr.Button("生成行程", variant="primary")
    result = gr.HTML("")

    with gr.Accordion("一键换站 · 不满意 AI 的选择？", open=False, elem_classes="it-swap"):
        gr.HTML("<div class='it-swap-sub'>从已抓取的 POI 池一键替换选定站点，"
                "不重跑全流程；时间轴与裁决同步刷新。无合适备选时不会编造地点。</div>")
        with gr.Row():
            swap_sel = gr.Dropdown(
                label="要换的站点", choices=[], interactive=True, scale=2,
                elem_classes="swap-seq")
            swap_btn = gr.Button("换一站", scale=1, elem_classes="it-btn-pill")
        swap_note = gr.Markdown("", elem_classes="swap-note")

    gr.HTML(FOOTER_HTML, elem_classes="it-app-foot")
    state = gr.State()

    hotel_btn.click(
        do_search_hotel,
        inputs=[city, hotel_label],
        outputs=[hotel_dropdown, hotel_pool],
    )
    hotel_dropdown.change(
        lambda v: gr.update(value=v),
        inputs=[hotel_dropdown],
        outputs=[hotel_label],
    )
    btn.click(
        build_itinerary,
        inputs=[city, hotel_label, hotel_pool, meeting_text, prefs],
        outputs=[result, swap_sel, swap_note, state],
    )
    swap_btn.click(
        do_swap,
        inputs=[swap_sel, state],
        outputs=[result, swap_sel, swap_note, state],
    )


if __name__ == "__main__":
    demo.queue().launch(css=CSS)