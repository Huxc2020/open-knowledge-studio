---
title: 召回评估
nav_order: 3
parent: 算法
---
# 召回评估（三层次 + 三指标）

`oks eval recall <dataset.yaml>` 支持离线评测召回质量。这一页讲评估方法论——三层次能力框架 + 三个量化指标——以及 OKS 当前的位置和阻塞。

## 三层次能力框架

借鉴长期对话记忆研究（LoCoMo 等），用户记忆能力可分三层递进：

| 层次 | 能力 | 例子 | OKS 当前 |
|------|------|------|----------|
| **第一层：基础回忆** | 存取用户直接给的、结构化、无歧义信息 | "我的会员号是 12345"→精确返回 | ✅ `oks recall` 单次召回 |
| **第二层：多会话检索** | 跨对象 / 时间找全相关信息 + 推理判断 | 两辆车问"为我的车预约"→找全两辆 + 问哪辆 | ⚠️ 部分——`raw/` 按时间分区，双路召回能跨会话，但无多跳推理 |
| **第三层：主动服务** | 跨会话深层关联，预见性主动帮助 | 订国际航班→关联数月前护照→预警将过期 | ❌ 不做——需 Agent 层主动推理，OKS 只提供召回原语 |

OKS 定位在第一层 + 第二层基础：`oks recall` 是 Recall 原语，第三层"主动服务"要 host Agent 在宿主层做（调 recall + 推理 + 再调 recall）。这是"Agent 状态栏注入 + Recall 原语"的边界——OKS 不替 Agent 思考。

## 三个量化指标

召回质量在带标注答案的测试集上算（query + 期望命中页）：

| 指标 | 回答什么 | 公式直觉 |
|------|---------|---------|
| **recall@k**（召回率@k） | 前 k 个结果含正确答案的查询比例——"该找的找到了吗" | 命中查询数 / 总查询数 |
| **MRR**（平均倒数排名） | 第一个相关结果排名的倒数平均——"找到得够不够靠前" | Σ 1/rank ÷ 查询数；排第 1 得 1 分，第 10 得 0.1 分 |
| **nDCG**（归一化折损累积增益） | 整个排序列表质量，排名越靠后折扣越大——"整个列表质量如何" | 考虑所有相关文档排名 + 相关程度 |

工业报告常见"检索失败率" = 1 − recall@k（如 top-20 未命中率）。跨来源比较先弄清 k 取多少、recall@k 是命中率还是相关文档比例。

## OKS 现状

`oks eval recall <dataset.yaml> --output <run.json>` 跑评测：

- **输入**：YAML 数据集（query + 期望命中 slug 列表 + 可选 `topic_id`）
- **输出**：每个 query 的召回结果 + 三指标聚合 + `--explain` 逐项分数
- **基线**：`--goal none` 无偏基线，`--goal <slug>` 固定 goal 可复现

**阻塞**：OKS 无官方标注数据集——三指标要 query + 期望命中页，需人工标注。社区可自建（把常用 query + 该命中的 wiki 页标好）。在标注数据集就绪前，召回评分权重不盲调（P9 精神）——只能用 `--explain` 做定性检查。

## v0.6.0 实测：50-case 三后端对比

v0.6.0 吸收 TreeSearch 的 markdown tree parser，把 `fts5` backend 从 **flat page-level** 升级为 **node-level**（每个 `##` heading 段一个 FTS5 row）。在 50 个语义改写 case（query 不含 slug 关键词，测试同义词/改写召回）上对比：

| backend | P@3 | 说明 |
|---------|------|------|
| `native` (6+1) | 27/50 = **54%** | oks 原创（jieba + IDF + 6 因子 + memory curve + goal boost），语义召回弱——词不匹配就失效 |
| **`fts5` (node-level)** | **48/50 = 96%** 🏆 | 吸收 TreeSearch 后；多词同段 BM25 高分，语义改写命中率高 |
| `fusion` | 45/50 = **90%** | fts5 召回 + native 归一化 re-rank（0.7 fts5 + 0.3 native） |

### 关键发现（数据驱动）

1. **node-level > page-level**——同样 FTS5 + BM25，按 `##` heading 拆 node（多词同段高分）比整页平铺召回精度高 42pp（54%→96%）。
2. **oks 灵魂与召回精度分层**——native 的 memory curve / goal boost / review bonus 在**召回层 re-rank 反而降精度**（不相关 page 高分挤掉精确命中，fusion 90% < fts5 96%）。结论：**oks 灵魂应在注入层 boost，不在召回层 re-rank**。
3. **语义鸿沟仍在**——fts5 仍 miss 2 case："自进化知识平台"期望 `ai-native-strategy`，但该 wiki 正文不含"自进化""知识平台"——同义词鸿沟，需 embedding（connector 扩展点已就位）。

### 决策

- 默认 `search_backend: fts5`（96% 最优召回）。
- native (6+1) 保留为 oks 原创召回（page-level，无 SQLite 依赖，小库快速）。
- fusion = fts5 主召回 + native 归一化 re-rank，limit<5 缩 native_top 给 fts5 留位。

复现：`oks config set search_backend fts5` + `records/experiments/eval-50.yaml`。

## 评测驱动调权

有标注数据集后，评测驱动调分：

1. 建基线（`--goal none` + 默认权重）跑 recall@k / MRR / nDCG
2. 调一个因子（如 type boost ×1.5→×1.8）重跑
3. 看三指标变化——升则保留，降则回滚
4. 逐因子迭代，每步只动一个

不靠直觉调权重——6+1 因子互相耦合，盲调一处可能拖垮全局。
