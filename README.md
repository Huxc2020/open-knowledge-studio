<div align="center">

<img src="docs/assets/oks-logo-readme.png" width="420" alt="Open Knowledge Studio">

# Open Knowledge Studio

Turn sources into reviewed, traceable knowledge that your Agent can recall later.

[English](#english) · [中文](#chinese) · [Documentation](https://open-agent-power.github.io/open-knowledge-studio/)

</div>

---

<a id="english"></a>

## English

Open Knowledge Studio (OKS) is an Agent-native, filesystem-first knowledge
workspace. It preserves source evidence, lets an Agent draft reusable knowledge,
keeps a human in control of promotion, and recalls the result in later work.

```text
your source → Candidate → human review → Wiki → Recall
```

### Quick Start

Requirements: Python 3.12+, Git, and pipx.

```bash
pipx install open-knowledge-studio
oks init ./my-knowledge-base
cd ./my-knowledge-base
oks status
```

In Claude Code, Codex, or another compatible Agent host, give the Agent a real
source and ask it to ingest it:

> Ingest this PDF into my OKS knowledge base.

The Agent follows the installed `/ingest` skill, records evidence, and creates a
Candidate in `drafts/`. Review it before promotion:

```bash
oks drafts list
oks drafts promote <slug>
oks recall "what did we decide?"
```

Without an Agent, prepare a run workspace explicitly:

```bash
oks ingest prepare <file-or-url>
```

`prepare` does not call an Agent. It creates the protocol workspace and prints
the next steps. For connector-managed acquisition, use
`oks ingest run <file-or-url>`; that compatibility path delegates extraction to
the separately packaged `oks-connector` runtime.

### Product Boundaries

- Core owns filesystem protocols, validation, human review, and Recall; it does
  not call AI APIs.
- `oks-connector` owns acquisition and mechanical extraction.
- Providers create evidence, not Wiki knowledge. Candidate promotion always
  requires human review.
- Evidence and execution states remain traceable, including `partial`,
  `failed`, `skipped`, and `environment_limited`.

### Recall Architecture — OKS Triple-Layer Recall

Recall and injection are decoupled across three layers:

- **Node-BM25** (retrieval) — fts5 node-level BM25 (one FTS5 row per `##`
  heading, multi-word same-section scores high). 50-case ablation: R@1=82.5%,
  MRR=0.907 (vs native 6+1 R@1=52.5%).
- **Soul Boost** (injection) — goal re-rank + `injection_boost` annotation
  (type×1.5/0.8/0.6 + review×1.2 + generic×0.5). Does not change retrieval
  order; visible in `--explain`.
- **Memory Curve** (decay) — type-specific λ → tier `hot/warm/cold/evictable`,
  an independent subsystem in `store.py`.

Ablation proves the layering: adding native 6+1 re-rank back into retrieval
(fusion) *lowers* R@1 0.825→0.805 — the "soul" belongs in the injection layer,
not the retrieval layer.

#### 50-case ablation (semantic-paraphrase queries, strict exact-slug match)

| backend | R@1 | R@3 | R@5 | MRR | nDCG@5 | p50 |
|---------|------|------|------|------|---------|------|
| **fts5 (full Triple-Layer)** | **0.825** | **0.925** | 0.927 | **0.907** | **0.893** | 93ms |
| native (−Node-BM25, 6+1 page-level) | 0.525 | 0.647 | 0.689 | 0.630 | 0.624 | 137ms |
| fusion (fts5 + native re-rank) | 0.805 | 0.905 | 0.927 | 0.900 | 0.887 | 226ms |

Node-BM25 lifts R@1 +57% over native; fusion re-rank *lowers* precision — the
soul factors must live in the injection layer, never in retrieval. Runs archived
in `records/experiments/runs/`. Reproduce:
`oks eval recall records/experiments/eval-50.yaml -o run.json --search-backend fts5`.

See [Recall Evaluation](docs/algorithms/recall-evaluation.md).

### Learn More

- [Real-world examples](examples/) — copyable scenarios: learning, books, Feishu, GitHub, maintenance, resume
- [Start here](docs/start-here.md)
- [Complete your first knowledge loop](docs/first-knowledge-loop.md)
- [Verify that OKS works](docs/verify.md)

*Advanced:*

- [Architecture principles](docs/concepts/constitution.md)
- [Ingest boundaries](docs/reference/ingest.md)

---

<a id="chinese"></a>

## 中文

Open Knowledge Studio（OKS）是一个 Agent-native、文件系统优先的知识工作台：
它保存来源证据，让 Agent 起草可复用知识，由人决定是否晋升，并在未来任务中重新召回。

```text
你的资料 → Candidate → 人工审核 → Wiki → Recall
```

### 快速开始

要求：Python 3.12+、Git、pipx。

```bash
pipx install open-knowledge-studio
oks init ./my-knowledge-base
cd ./my-knowledge-base
oks status
```

在 Claude Code、Codex 或兼容 Agent 中，把一份自己的真实资料交给 Agent：

> 把这份 PDF 收录到我的 OKS 知识库。

Agent 会按已安装的 `/ingest` Skill 保存证据，并在 `drafts/` 生成 Candidate。
审核后再晋升：

```bash
oks drafts list
oks drafts promote <slug>
oks recall "我们当时做了什么决定？"
```

没有 Agent 时，可以显式准备 Run Workspace：

```bash
oks ingest prepare <文件或URL>
```

`prepare` 不会自行调用 Agent，只创建协议工作区并输出下一步说明。需要 connector
托管采集时，使用 `oks ingest run <文件或URL>`；这条兼容路径把提取交给独立发布的
`oks-connector`。

### 召回架构 — OKS Triple-Layer Recall

召回与注入解耦，三层架构：

- **Node-BM25**（召回层）—— fts5 node-level BM25（每个 `##` heading 段一个 FTS5
  row，多词同段高分）。50-case 消融：R@1=82.5%，MRR=0.907（vs native 6+1 R@1=52.5%）。
- **Soul Boost**（注入层）—— goal 重排 + `injection_boost` 标注
  （type×1.5/0.8/0.6 + review×1.2 + generic×0.5）。不改召回顺序，`--explain` 可见。
- **Memory Curve**（衰减层）—— type-specific λ → tier `hot/warm/cold/evictable`，
  `store.py` 独立子系统。

#### 50-case 消融实验（语义改写 query，严格精确 slug 匹配）

| backend | R@1 | R@3 | R@5 | MRR | nDCG@5 | p50 |
|---------|------|------|------|------|---------|------|
| **fts5（完整 Triple-Layer）** | **0.825** | **0.925** | 0.927 | **0.907** | **0.893** | 93ms |
| native（去 Node-BM25，6+1 page-level） | 0.525 | 0.647 | 0.689 | 0.630 | 0.624 | 137ms |
| fusion（fts5 + native re-rank） | 0.805 | 0.905 | 0.927 | 0.900 | 0.887 | 226ms |

Node-BM25 R@1 较 native +57%；fusion re-rank 反而*降*精度——灵魂因子必须留在注入层，
不能放召回层。run json 归档 `records/experiments/runs/`。复现：
`oks eval recall records/experiments/eval-50.yaml -o run.json --search-backend fts5`。

详见 [召回评估](docs/algorithms/recall-evaluation.md)。

### 产品边界

- Core 负责文件协议、校验、人工审核和 Recall，不调用 AI API。
- `oks-connector` 负责资料获取与机械提取。
- Provider 产生证据，不直接产生 Wiki 知识；Candidate 晋升必须经过人工审核。
- 证据与执行状态必须可追溯，包括 `partial`、`failed`、`skipped` 和
  `environment_limited`。

### 继续阅读

- [真实案例](examples/) — 可复制的场景：学习、书籍、飞书、GitHub、维护、简历
- [从这里开始](docs/start-here.md)
- [完成第一个知识闭环](docs/first-knowledge-loop.md)
- [确认 OKS 正在工作](docs/verify.md)

*进阶内容：*

- [架构原则](docs/concepts/constitution.md)
- [摄入边界](docs/reference/ingest.md)

## License

MIT
