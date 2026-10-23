# 商旅融合行程官 · UI 设计规范（Design System）

> 定位：Editorial 新中式极简。参照「住哪好」的设计语言与第四张路线草图的表达，
> 转译为本产品的暖咖金极简系统。本文档与 `app.py` 内的 `CSS` 常量一一对应，
> 组件实现以 Gradio 6 为宿主，样式通过 `.gradio-container` 作用域注入。

## 1. 色彩系统（Color Tokens）

| Token | 值 | 用途 |
|---|---|---|
| `--bg` | `#FAFAF9` | 页面暖浅灰底 |
| `--card` | `#FFFFFF` | 卡片 / 输入框 / 下拉 |
| `--card-h` | `#F3EFE8` | 次要底（引用块、chip、状态条） |
| `--txt` | `#1A1A1A` | 主文字 |
| `--sub` | `#666666` | 次级文字 |
| `--soft` | `#999999` | 弱文字 / kicker |
| `--line` | `#E6E0D6` | 描边 / 分隔 |
| `--gold` | `#B8956A` | 主强调（路线、focus、对比度） |
| `--gold-hi` | `#9A7B52` | 强调深一档（链接、标签、成功态文字） |
| `--warn` | `#9A6B2E` | 警示 |
| `--err` | `#B85C5C` | 错误 |

规则：强调色只作点睛，不改大面；主文字近黑 `#1A1A1A`、底近白暖灰 `#FAFAF9`、描边棕灰 `#E6E0D6`，
形成「白-灰-棕」三档信息层级。

## 2. 字体与层级（Typography）

- 字体栈：`PingFang SC / Hiragino Sans GB / Microsoft YaHei / Noto Sans SC / sans-serif`。
  优先级：屏显用苹方/雅黑，含字体栈回退。
- 层级（由大到小）：
  1. Hero 标题：`52px / weight 300 / letter-spacing .06em`
  2. 卡片主名 `.it-name`：`25px / weight 600`
  3. 节标题 `.it-dayhead .h`：`22px / weight 600`
  4. 正文 `.it-intro`：`16.5px / 行高 1.8`
  5. 释义/副文：`13–14px`（`--sub` / `--soft`）
- 大写字距 kicker（`letter-spacing 3–4px`、`text-transform: uppercase`）作为小节眉：
  `INSIGHT · 为什么这样排`、`STOP 1`、`EVENING · 会议间隙微行程`、`调整 · SWAP`。

## 3. 间距规范（Spacing）

- 单元：`4/8/12/14/16/18/22/26px`。
- 卡片内边距：`22px 26px`（小屏 `16px 18px`）。
- 组件间：页内模块 `gap:22px`；卡片 `padding:22px 26px`；
  chip 间 `gap:8px`；时间轴每站 `margin-bottom:22px`。
- 响应式断点：`≤768px` 收窄为单列、减边距、字号降档（见 §6）。

## 4. 基础组件（统一实现）

| 组件 | 视觉 | 状态 |
|---|---|---|
| 按钮·Primary | 暖金 `#B8956A` 圆角胶囊（`999px`），hover 转 `#9A7B52` | focus/hover 有色阶过渡 |
| 按钮·行动（换站 `.it-btn-pill`） | 近黑 `#1A1A1A` 白字胶囊；hover `#333`；disabled 灰底 `--line` 浅字 | 成功/警示由下方状态条表达 |
| 文本/数字输入 | 白底、`1px --line` 描边、`10px` 圆角、focus 描边转 `--gold` | focus 有 0.15s 过渡 |
| 下拉（偏好/换站） | 与输入同款白底描边；已选 token 近黑底白字胶囊 | 多选 token 圆角 `999px` |
| 卡片 | 白底、`1px --line` 描边、`14px` 圆角、微阴影 | 无 hover 位移（克制） |
| 引用块（INSIGHT/警示/状态条） | 左侧 `3px` 强调竖线 + 浅底，左对齐 | 成功暖金、警示琥珀 |
| 导航条 | Editorial 顶部：大写字距 kicker 眉 + 左对齐大标题 + 胶囊 pill | — |
| 折叠区（换站） | 白卡片描边圆角，`.it-swap` 作用域统一 | open/close 原生过渡 |

### 一键换站模块（专项优化）
- 结构：折叠卡片 `.it-swap` → 说明副文本 `.it-swap-sub` → 行（下拉 `scale2` + 胶囊按钮）→ 状态条。
- 交互反馈：成功 `_swap_note(text,"ok")`（暖金左竖线、`已替换第 N 站 → 「某站」`）、
  无备选/未选择 `"warn"`（琥珀），全部由 `swap-status` 类统一渲染，无裸字符串。
- 一致性：下拉与「偏好」共用同一输入样式；按钮与「生成」共用胶囊语言；
  状态条与 INSIGHT/警示共用「左竖线 + 浅底」视觉语法。
- 防幻觉底线：无备选时 UI 明示「无合适备选，保留原站点」，不编造 POI。

## 5. 布局节奏（页序）

1. Hero：kicker 眉 → 超大标题 → 副文 → pill —— 均左对齐，底部细分隔线。
2. INSIGHT 引用块（一句话解释为什么这样排）。
3. 路线示意图（SVG 地图 + 实景缩略图 POI 清单 + 图例 + 注意事项）。
4. 晚间日程：dayhead（左节标题 + 右时段）→ 纵向时间轴 stop 卡。
5. 换站折叠区（低干扰、可展开）。
6. 页脚。

节奏：先「结论引用」→「空间概览」→「逐站细节」→「调整入口」，信息漏斗式。

## 6. 响应式（≤768px）

- 容器内边距收窄（`0 14px 30px`）；Hero 标题 `34px`。
- 地图 POI 双列 → 单列；`it-meta`、`it-actions` 纵排。
- 换站按钮全宽 `.it-btn-pill{width:100%}`；折叠标题字号降档。
- 时间轴 dayhead 右时段隐藏，保留左标题。

## 7. 排除与统一（本次 audit）

- 移除暗色主题残留：暖色单一主题化，无明暗混用。
- 修正 CSS 冗余：删除未使用的 `.g-nav` 块与额外花括号，保证无死代码 / 样式冲突。
- 唯一 UI 宿主为 `app.py`（Gradio 单页）；`demo.py` 为 CLI/Markdown 输出，不参与组件层。
- 所有可点击态在 hover/disabled 均有明确视觉响应，无「看得到点不了」组件。

## 8. 验证

- `python -m py_compile app.py` 通过；`python -m pytest tests` 全绿。
- 浏览器全页截图（桌面 + 移动宽度）确认：Hero/INSIGHT/地图/日程/换站层次清晰、
  SVG 节点与箭头无越界、无文字重叠与样式冲突。