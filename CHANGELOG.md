# Changelog

## v0.5.14 (2026-08-17)

### 全面 review 修复（2 个 bug）

**1. signal_rel_floor 半死配置 → 活起来**
- post-tool-edit.py 的 `_should_signal` 第 3 步硬编码 `rel < 2.5`，不读 yaml
- 改为读 `load_recall_params()['posttool_signal_rel_floor']`（fallback 2.5）
- 现在 yaml 改 signal_rel_floor 真正生效——dsh-oks 设置卡 + oks metrics + hook 三处一致

**2. oks config set recall 参数双轨漂移 → 写 recall.yaml**
- v0.5.12 声明 settings/recall.yaml 是唯一参数真源，但 `oks config set` 还写 ~/.oks/config.json
- recall 读 yaml 不读 config.json → search_backend 等参数配置了不生效
- 修复：config_set 对 _RECALL_YAML_KEYS（search_backend/recall_*/posttool_*/conflict_window/mail_topn）调 set_recall_yaml_param 写 yaml
- 其余 key（knowledge_base_path/handlers/api_keys/feishu）仍写 config.json

### PR 处理（8 个）
- merge #29 #30 #32 #33 #35 #36 #37（7 个，含 capability health fix + recall scope fix + docs）
- close #34（signal_rel_floor 不删，已在 main 4fce21c 修复）

## v0.5.12 (2026-08-17)

### env 废弃 — settings/recall.yaml 是唯一参数真源

回应「参数不能永远跟随仓库配置文件吗？环境变量忘了怎么办？两边不同步怎么办？」

- **env 完全废弃**：`load_recall_params()` 去掉 env 读取，只读 yaml + 默认。
  参数永远跟随 `settings/recall.yaml`，git 同步，走到哪带到哪。
- **迁移警告**：检测到旧 `OKS_*` env 时警告提示迁移到 yaml + unset
  （`load_recall_params._warned` 防刷屏）
- **CLI flag 临时调参**：`oks recall --floor 0.9` 一次性调 floor，不改 yaml。
  recall_cmd 用 `floor_override` 过滤 rel 低于 floor 的结果。
- **metrics html 文案**：去掉「env 覆盖 yaml」，改为「settings/recall.yaml 是唯一
  参数真源 → git commit → 走到哪同步到哪。临时调参用 oks recall --floor」
- **去掉 `envvar=OKS_SEARCH_BACKEND`**：search_backend 也从 yaml 读，不读 env

### 新优先级

```
CLI flag（一次性临时调参）> settings/recall.yaml（唯一持久真源）> 代码默认值
env 已废弃——不再读取
```

### 向后兼容

现有 env 用户升级后 env 不再生效。`oks init . --upgrade` 生成默认 yaml，
用户把 env 值搬到 yaml（或看警告手动迁移），unset env 即可。

## v0.5.11 (2026-08-17)

### 实验数据图表化 + 参数存知识库

将 PostToolUse 注入实验数据沉淀到文档与报告，参数可随知识库同步：

- **fig6 四模式对比图**（`docs/assets/experiments/fig6-posttool-modes.png`）：
  A(20KB) / D(8KB) / J(1KB) / K+J(1KB) token + signal 次数对比
- **docs/algorithms/oks-effectiveness.md 第十二节**：PostToolUse 注入模式对比，
  含四模式表 + J 闸门 3 条件 + 实测数据 + K 引导说明
- **`settings/recall.yaml` 参数文件**：`oks init` 生成实例级参数文件，
  改 → git commit → 走到哪同步到哪。OKS 只提供默认值，每人自调。
  优先级：env > settings/recall.yaml > 代码默认值
- **`load_recall_params()` 共享加载函数**（recall.py）：env / yaml / 默认 三级 fallback
- **post-tool-edit.py 用 load_recall_params**：取代直接 os.environ，读 yaml
- **`oks metrics --html` 增强**：加 PostToolUse 注入统计 + 当前参数表
  （recall.floor / posttool.mode / signal_rel_floor / search_backend）

### 数据同步路径

```
settings/recall.yaml (参数) + records/inject.jsonl (注入数据)
  → git commit → clone 即同步
  → oks metrics --html 随时看报告
  → 参数 + 数据不断积累沉淀，每人不同
```

## v0.5.10 (2026-08-17)

### K+J 混合：system prompt 引导 + 智能信号

PostToolUse recall 从 D 模式（每次工具 signal ~8KB）进化为 K+J 混合：

- **K（system prompt 引导）**：`oks init` 生成实例根 `AGENTS.md`，内含 OKS
  recall 引导——AI 读到即知晓有知识库 + 何时调 + query 来自任务意图。
  零 hook 注入，token 最省。
- **J（智能信号）**：`post-tool-edit.py` 加 `_should_signal()` 闸门，3 条件
  AND 才注入 signal：
  1. 工具类型：只 Edit/Write/MultiEdit/Grep/Glob（Bash/Read 跳过）
  2. query 质量：非通用词（git/status/ls 等），≥4 字符
  3. rel > 2.5（极高相关）
- 实测：20 工具长任务只 2-3 次 signal ≈ 1KB（vs A=20KB，省 95%）
- Bash/Read 全跳过——AI 已在读内容，signal 纯噪声

### 新增

- `_INSTANCE_AGENTS_MD` 模板常量 + `init` 写实例根 `AGENTS.md`
- `_should_signal()` 闸门 + `_query_from_tool` 路径过滤增强

## v0.5.9 (2026-08-17)

### 可插拔 search backend 架构

- **SearchBackend Protocol**（`cli/knowledge_studio/search/`）：第三方包通过
  `entry_points(group="oks_search_backend")` 注册 search backend，OKS 核心不改，
  recall 切 `--search-backend <name>` 即用
- 3 个内置 backend：
  - `native`（默认）：6+1 因子（jieba + IDF + title boost）
  - `fts5`：SQLite FTS5 + BM25 + column weights（title 5x > tags 3x > body 1x >
    code 0.5x）+ 增量 diff content_hash + 持久化 `.oks/fts5.db` + LIKE fallback
  - `fusion`：native top-3 主 + fts5 独有补 2（实验验证最优，RRF 伤 R@1）
- `oks recall --search-backend <name>` + `OKS_SEARCH_BACKEND` envvar

### CV TreeSearch 纯函数到 recall.py

- `estimate_idf`：平滑 IDF `log((N+1)/(df+1))+1`
- `compute_term_overlap`：IDF 加权 token overlap，bonus 加在 count×0.3 之上
- `check_title_match`：query term 逐个命中 title，+0.3/个
- `is_generic_page`：通用目录性页（index/overview/readme/目录/概述...）×0.5 降权
- 效果：baseline R@1 0.333→0.400，MRR 0.472→0.506

### lazy watch（FTS5 无守护进程刷新）

- `_wiki_fingerprint()`：stat-only（path, mtime_ns, size）
- `_maybe_reindex()`：recall 前比对 fingerprint，变了才增量重索引
- meta 表存 wiki_fingerprint 跨进程
- 速度：首次 552ms | 不变 3ms | 变 1 页 41ms

### PostToolUse recall 补位（长任务盲区）

- **post-tool-edit.py 新版**（367 行）：文件冲突检测 + recall 补位段
  （`_query_from_tool` + `_recall_supplement`）
- query 来自工具操作（Edit/Write/Read→file stem；Bash→command；Grep/Glob→pattern）
- 高 floor 0.9 + 低 topn 2 + 共享 cooldown + inject trace source=posttool
- `OKS_POSTTOOL_FLOOR` / `OKS_POSTTOOL_TOPN` env

### pi extension（oks-posttool-recall.ts）

- `.pi/extensions/oks-posttool-recall.ts`：监听 `tool_result`（pi 的 PostToolUse 等价）
- `_kbRoot()` 解析 OKS_ROOT / config → KB 实例（不依赖 process.cwd()）
- query-level cooldown 预检查（同 query 10 轮 0ms 跳过 Python）
- 真实注入验证：Bash/Edit → OSS call chain / AI agent 记忆（rel 3.17/2.741）

### oks-connector-code（独立包）

- AST 解析 raw/*.py，函数/类级召回（FunctionDef/AsyncFunctionDef/ClassDef）
- token overlap：name hit 5x body hit
- `entry_points(group="oks_search_backend", name="code")`
- 独立仓库 `oks-connector-code`

### 文档 + 实验

- algorithms/oks-effectiveness.md：11 节（召回评估 / 记忆腐化 / 注入分布 /
  PostToolUse 冲突 / TreeSearch 融合 / CV / search backend / is_generic / lazy watch）
- fig1-5 实验图表
- docs/cli.md + recall-engine.md + context-injection.md 更新

### PostToolUse 测试结论

三层注入全部工作：UserPromptSubmit ✅ + PostToolUse recall ✅ + PostToolUse 冲突 ✅
20 次真实注入，11 个不同 wiki 命中，floor=0.9 + topn=2 控制不淹没

## v0.4.0 (2026-08-09)

首个进入 PyPI 的 v0.4 版本（上一个发布版本是 0.2.4）。核心是 Agent-Native
摄入协议：Agent 收集证据并自己写 Manifest，CLI 只做机械校验，绝不判断内容。

### Agent-Native 摄入

- **Raw Bundle v0.2 流水线**（`oks raw-commit`）：JSON Schema 校验、fail-closed、
  暂存后原子提交（staging → 校验 → `shutil.move`）、路径穿越防护、artifact
  SHA-256 逐一核对，拒绝时给出结构化错误码。
- **provenance 门禁 fail-closed**：`steps[]` 必须非空；声明 `succeeded` /
  `degraded` 的步骤无论 manifest 整体状态如何都必须在
  `work/<provider>/output.*` 留下非空原始输出；`provider` 必须是合法注册标识符
  且解析后不得逃出 `work/`。豁免只剩 `agent-runtime` 与 `human`。
  「Agent 自称我存了」不作为证据。
- **17 个 Provider**（`knowledge_studio/providers/<id>/`，含 `provider.yaml`、
  `SKILL.md`、可选 `normalize.py`）、**7 个 Recipe**、**12 份协议 Schema**。
- `oks ingest prepare` 产出的骨架不再用「结果」结构表达「计划」：计划写入
  `notes.planned_capabilities`，`steps` / `modalities` / `evidence_records`
  保持为空，由 Agent 按实际执行填写。

### 安全

- **SSRF 逐跳校验**：重定向不再交给 `requests` 自动跟随（它从不复检目标），
  改由 `safe_redirect_chain` 逐跳 normalize + 断言，缺 `Location` 的 3xx 视为
  错误，中间响应显式关闭。
- **`oks security sanitize <file>`**：外部 Provider 原始输出进入 Run Workspace
  前剥离 API key、bearer token、会话 cookie 与内网地址。
- **路径穿越**：`--area` 与 `provider` 均按白名单正则校验，不再能当路径片段。
- **A3 人工门**：`status: rejected` 的 draft 不可晋升。

### 记忆与召回（CONSTITUTION A2）

- **`[verified]` 必须有事实依据**：只能来自 trace 证据或 draft 晋升写下的
  `human_reviewed_at`。此前读 3 次即把 `provisional` 提为 `active`，再被标成
  「人工审阅」注入 —— 使用次数不再影响信任，只影响排序。
- **episodic 通道全部带来源标签**：`raw/` → `[untrusted-source]`（只作数据引用，
  绝不执行其中指令）、`raw/executions/` → `[provenance]`、`profiles/` →
  `[user-declared]`；无法识别的类型按不可信处理。
- **身份作用域**：召回不再跨用户/项目返回他人画像。

### 打包与 CLI

- **`assets/` 是打包单一事实源**，`_AGENT_TARGETS` 清单据此装配
  `.claude` / `.codex` / `.agents`；维护者专用技能物理隔离在仓库自身
  `.claude/` 下，不进 Wheel。
- 两个入口点：`oks`（核心）与 `oks-connector`（可选连接器层）。
- **52 个命令、11 个命令组**。新增 `oks schema show <name>`（打印协议文档
  样例）、`oks capability guide <provider>`（打印随包 Provider 指南）、
  `oks security sanitize`。
- `oks init` 不再静默改写 `~/.oks/config.json` 的活跃知识库：仅在尚未注册时
  采用，已有且不同则原样保留并提示切换命令。
- `oks ingest` 产出的 Raw Bundle 写入活跃知识库，不再落在当前目录。
- PDF 默认路由改为 `pdf-lite`（pymupdf，约 150MB），MinerU 仍可按需安装。

### 工程

- **PR 强制门禁**：ubuntu / macOS / Windows × Python 3.12 / 3.13 的 pytest，
  外加 Wheel 与 sdist 内容校验（每个 asset 必须到位、维护者技能不得泄漏、
  干净树构建、装包后 `oks init` 冒烟）。
- 测试从 shell 调用全局 `oks` 改为进程内调用被测包 —— 此前脏装会让坏代码显绿、
  干净克隆却全红。
- 文档与技能引用的每个 `oks` 命令都由测试对照真实命令树校验；随包协议样例
  必须通过自己的 Schema。

## v0.3.0

- Base knowledge engineering CLI with search, recall, wiki CRUD, drafts, lint, metrics
- 6+1-factor recall engine with decay system
- Date-based raw/ organization
- Feishu worker integration (Source + Review planes)
- Global config (`~/.oks/config.json`)
