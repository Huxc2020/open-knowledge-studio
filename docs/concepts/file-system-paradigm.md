---
title: 文件系统范式
nav_order: 4
parent: 概念
---
# 文件系统范式

OKS 把知识组织成文件系统，不是向量数据库。这一页讲为什么。

## 为什么是文件系统

主流 RAG 把知识存进向量库（embedding + ANN 索引），检索靠余弦相似度。OKS 选了另一条路：**所有知识是 markdown 文件 + frontmatter，按目录组织，用 Git 版本控制**。

OpenViking 的虚拟文件系统与渐进浏览思路为 OKS 提供了设计启发：Agent 可以通过稳定 URI 先看目录，再按需读取具体内容。OKS 当前用 canonical `oks://` 地址和严格只读的 `oks fs` 命令实现这一访问方式，但它不是 OpenViking 的复刻，也没有引入 OpenViking 代码。

两者的数据语义并不相同。OpenViking 的 L0、L1 是针对同一目录子树派生的目录级摘要，L2 是该子树中的完整内容；OKS 的 frontmatter、Wiki 和 Raw 则不是同一对象的三层表示。Raw 是保真材料，Wiki 是经过蒸馏和人工审核的独立知识对象，两者通过 provenance 关联，并受 Raw → Draft → Wiki 生命周期约束。因此不能把 frontmatter、Wiki 正文和 Raw 分别直接命名为 L0、L1、L2。

当前 MVP 只借鉴“稳定命名空间 + 渐进浏览”：`oks fs overview` 返回机械目录统计，`oks fs read` 提供有界 UTF-8 分页，`oks fs find` 做确定性的字面搜索。它不会生成 L0/L1 摘要 sidecar，也不调用 LLM、embedding 或 reranker；语义召回仍由 `oks recall` 及其既有 backend 负责。

## Markdown 纯文本的选择

纯文本看似反直觉（不如 JSON 结构化、不如向量库检索快），但深思熟虑：

- **人可直接读 / 编辑 / 审**——契合 A3 人审门控：结构必须能被人工逐条审阅
- **Git 版本控制**——每次知识变更是 diff，可审查 / 回滚 / 追溯
- **Agent write_file 友好**——Agent 在工作分支写 draft，人审后合入主库
- **链接可达**——wikilink + frontmatter `relates_to` 做轻量图谱（A4 关系）

代价：无向量检索（语义召回差）。倒排索引（BM25）由 fts5 backend 提供，native 用 IDF 加权 token。OKS 用 6+1 因子（token + 子串 + 图谱 + 类型 + review + 记忆 + goal）做规则评分补这个缺口。

## 防孤岛：链接与索引

文件系统范式有一个易被忽视的前提：**文件之间必须建立链接与索引**。只把知识拆成一堆独立文件平铺在目录里，除了逐个全文扫描或向量检索，Agent 无从导航——知识越多越难找。

正确做法是把知识库组织得像 Wikipedia：

- 每个条目提及其他条目时用 wikilink 指向
- 每个域有 index 页（入口）
- `oks wiki export` 生成 per-type index + 顶层 index + log

OKS 的 ingest Pre-flight（search-before-add）强制 Agent 在加新页前先 recall 主题，决定 `relates_to` + relationship（enriches / supersedes / confirms / challenges）——这正是"写新条目前先链接已有条目"的工程化。

不同模型主动建链接的意愿不同——强模型写新知识时自发回指已有条目，弱模型只孤立追加。所以 ingest SKILL.md 把要求写明确：每新增条目，先检索 + 链接已有条目 + 更新索引页，形成双向可达引用网络，不让知识退化成孤岛。

## 对比向量库

| 维度 | 向量库 RAG | OKS 文件系统范式 |
|------|-----------|------------------|
| 存储 | embedding + ANN 索引 | markdown + frontmatter + Git |
| 检索 | 余弦相似度 | 6+1 因子规则评分 |
| 语义召回 | ✅ 强 | ❌ 弱（无 embedding） |
| 可读 / 可审 | ❌ 黑盒 | ✅ 人可直接读 / 编辑 |
| 版本控制 | 需额外方案 | ✅ Git 原生 |
| 依赖 | 要模型 + 向量库 | ✅ 零 AI 依赖 |
| 适合 | 大规模 / 语义检索 | 个人 / 小团队 / 人审门控 |

OKS 选文件系统范式，是因为定位"Agent 状态栏注入 + Recall 原语"——小到中知识库，人审保证质量，零依赖可本地跑。向量库是语义召回的解，但和 OKS 的宪法（人审 + P4 不调 AI + 可读）冲突。
