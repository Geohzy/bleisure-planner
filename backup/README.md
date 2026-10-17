# 商旅融合行程官（Bleisure 助手）· 开发脚手架

2026 飞猪 AI 旅行创新大赛参赛项目的最小闭环：**flyai 真实数据 → 经纬度空间聚类 → 微行程规划 → 百炼文案 → 行程卡**。

## 运行

```bash
cd feizhu-bleisure
npm i @fly-ai/flyai-cli   # 只需一次
python3 demo.py
```

- `FLYAI_API_KEY`：你的飞猪 Key，`export FLYAI_API_KEY=...` 后价格不再脱敏。
  Key 只放环境变量，不要写进代码或提交到仓库。
- `DASHSCOPE_API_KEY`：你的百炼 Key，设置后文案走百炼真实生成；
  不设置则用本地模板兜底，流水线照样跑通（提交前必须换成真实调用）。

## 文件

| 文件 | 作用 |
|---|---|
| `flyai_client.py` | flyai CLI 薄封装：search-poi / search-hotel，统一归一化 |
| `geo.py` | haversine 距离 + 按酒店位置排序 POI（空间感知核心） |
| `planner.py` | 空闲窗口切分，贪心塞入最近的 POI |
| `llm.py` | 百炼 OpenAI 兼容接口调用，无 Key 时本地模板兜底 |
| `demo.py` | 端到端 demo：杭州 + 西湖酒店 + 今晚 18:00–21:00 |

## 下一步（按 9 天排期）

- [x] 多品类 POI：`search_poi_multi` 已合入（中文类目 + `kw:` 兜底，单路失败不中断）；
      待用真实 Key 验证 `kw:夜市/美食街/咖啡` 是否返回美食夜景类 POI
- [ ] 交通时间估算
- [ ] 会议冲突检测与降级话术
- [ ] 百炼 Agent 编排（当前为单次调用）
- [ ] README + 2 分钟演示视频 + 提交 issue
