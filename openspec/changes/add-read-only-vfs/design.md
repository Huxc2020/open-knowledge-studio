## Context

OKS 已经以真实文件系统作为内容真源，并通过 `get_kb_root()` 在 `OKS_ROOT`、全局配置和当前实例之间解析唯一知识库根目录。现有领域 API 围绕 `wiki`、`drafts`、`raw-commit`、recall 和 traces 展开；这保证了 Raw → Draft → Wiki 的治理门控，但 Agent 无法通过统一地址浏览不同上下文类型，recall 结果也混用 slug 与物理相对路径。

OpenViking 证明了“Agent 通过文件系统语义管理上下文”的接口价值，但 OKS 的核心差异是人审生命周期、Git 可审计和核心无 AI 依赖。本设计只吸收命名空间与浏览接口，不复制 OpenViking 的 AGFS、向量索引、自动 Session 记忆提取或代码。

本变更影响 URI 解析、CLI、recall 返回契约、文档和安全边界，属于跨模块架构改动。

## Goals / Non-Goals

**Goals:**

- 为当前实例提供稳定、可测试的 `oks://` 逻辑地址。
- 让 Agent 使用有限、可观察的只读命令浏览上下文。
- 为 recall hit 增加 canonical URI，同时保持现有调用方兼容。
- 保持 Markdown + Git 为唯一内容真源，所有 VFS 操作零持久化副作用。
- 在 resolver 层集中处理 scope 白名单、目录穿越、符号链接和输出上限。

**Non-Goals:**

- 不提供任意写入、移动、删除或目录创建；写入继续使用领域命令。
- 不生成 OpenViking 风格的 L0/L1 语义 sidecar。
- 不实现层级递归语义检索、embedding、rerank 或 Agentic Search。
- 不改变物理目录结构，不迁移任何实例数据。
- 不提供多实例 URI authority、多租户或网络服务。
- 不暴露 `settings/`、`_meta/`、`.oks/` 或仓库代码目录。
- 不引入或链接 OpenViking 代码，避免许可证耦合。

## Decisions

### 1. 使用当前实例隐式 root，而不是在 URI 中编码实例

URI 采用 `oks://{scope}/{path}`。`wiki` 等 scope 位于 URI authority 位置，实例继续由 `get_kb_root()` 解析。这样与现有“一个 CLI 调用只操作一个实例”的模型一致，也避免把本机绝对路径或可变实例名固化进知识链接。

备选方案是 `oks://{instance}/{scope}/{path}`。它有利于跨实例引用，但需要实例注册表、重命名和权限语义，超出只读 MVP。

### 2. 使用白名单虚拟 mount，而不是暴露实例根目录

新增 `knowledge_studio.vfs` 模块，定义不可变 mount 表：

```text
profiles → profiles/
raw      → raw/，隐藏 executions/ 与 .logs/
wiki     → wiki/
drafts   → drafts/
mail     → mail/
skills   → .agents/skills/
traces   → raw/executions/
```

`traces` 是 `raw/executions` 的唯一公开地址。`skills` 只使用编辑器中立的 `.agents/skills`，不回退到实例外目录或 `.claude/skills`，避免同一 URI 因宿主环境不同指向不同内容。

备选方案是直接把任何实例相对路径变成 URI。该方案简单，但会暴露 `.oks` 运行状态、配置和其他非上下文文件，也无法保证 canonical 唯一性，因此拒绝。

### 3. URI 类型与 resolver 集中安全验证

`OksUri` 负责 parse、normalize 和 render；`VfsResolver` 负责把 URI 映射到 mount 下的 `ResolvedNode`。CLI、recall 和后续 connector 只能调用该 resolver，不得自行拼接 URI。

解析顺序固定为：

1. 校验 scheme、scope，以及 query/fragment/user-info/port 均为空。
2. 对每个 segment 解码一次，拒绝空 segment、`.`、`..`、NUL、反斜线和编码分隔符。
3. 从白名单 mount 生成候选路径。
4. 使用逐 segment `lstat` 拒绝任意符号链接。
5. 规范化并验证候选路径位于 mount root 内。
6. 应用 mount 排除规则，再执行目标操作。

错误使用 `VfsError(code, public_message)` 表达；public message 只包含 URI，不包含物理绝对路径。

备选方案是只调用 `Path.resolve().relative_to(root)`。它能阻止部分逃逸，但会跟随符号链接，且不能表达虚拟排除路径，因此不足以作为唯一防线。

### 4. 一个纯读取服务承载所有命令语义

`VfsService` 提供 `ls`、`tree`、`stat`、`read`、`overview`、`find`，返回普通 dataclass/dict；Typer CLI 只做参数校验和 text/JSON 渲染。服务不依赖 Rich，便于 hooks、测试和未来 MCP 复用。

- `ls`：只返回直接子节点。
- `tree`：按名称稳定排序、深度和节点数双重限界，不跟随 symlink。
- `stat`：返回 URI、类型、大小、修改时间和 mount，不计算内容哈希。
- `read`：UTF-8 文本字符分页；二进制只允许 `stat`。
- `overview`：直接子节点和类型的机械统计，可链接现有 `INDEX.md`，不生成摘要。
- `find`：对相对路径和 UTF-8 正文字面搜索；限定范围、结果数和 snippet。

备选方案是让每个 CLI 命令直接使用 `pathlib`。这会重复安全验证，并使 recall URI 生成与 CLI 解析发生漂移，因此拒绝。

### 5. JSON 契约统一，文本输出保持轻量

所有 `oks fs` JSON 响应使用 `oks-fs-response/v1` 外壳。结果对象按 operation 变化，但错误结构固定。CLI 输入/URI 错误退出码为 2，不可预期 I/O 错误退出码为 1；正常的截断、跳过二进制文件不是失败。

文本模式为人类和 Agent 提供紧凑输出，不画复杂 TUI。JSON 模式是自动化的规范接口。

### 6. Recall 只增加字段，不升 schema version

`parse_wiki_file()` 已保留实际 `file_path`，knowledge hit 可通过 resolver 从该路径生成 URI；episodic/profile hit 则从 `source_path` 映射。新增字段对 JSON 消费者是向后兼容扩展，因此继续使用 `recall-response/v1` 和 `recall-hit/v1`。

URI 生成失败属于实现不变量破坏：测试环境中应失败，生产 CLI 则记录诊断并省略该异常节点，而不是生成不可信 URI。正常受支持 hit 必须始终含 `uri`。

### 7. 第一版不把 Raw/Wiki 重命名为 L0/L1/L2

OpenViking 的 L0/L1 是同一目录子树的派生摘要，L2 是完整内容。OKS 的 Wiki 是从 Raw 蒸馏、人审后的独立知识对象，两者通过 provenance 关联但并非同一对象的不同粒度。因此文档只保留“渐进加载思想的启发”，不宣称现有三类文件已经实现 OpenViking 的三层模型。

## Risks / Trade-offs

- **[URI 依赖物理层级，文件重组会改变地址]** → 第一版没有 `mv`，canonical URI 明确表示当前稳定逻辑路径；未来如需重组，通过 redirect manifest 单独设计，不在本次预埋。
- **[拒绝所有 symlink 会限制部分高级实例布局]** → MVP 优先可证明的 containment；未来可在有测试和威胁模型后允许 mount 内 symlink。
- **[字面 find 在大实例上比索引慢]** → 使用严格的 subtree、结果和输出限制；语义或索引检索仍由 `oks recall` 负责。
- **[额外 `uri` 字段可能影响严格 JSON schema 消费者]** → 项目现有响应是开放对象且未声明禁止额外字段；增加兼容性测试，并在变更说明中明确字段扩展。
- **[skills 两份副本可能漂移]** → VFS 只认 `.agents/skills`；`skills-install` 继续负责副本同步，VFS 不尝试合并。
- **[mail/profile 内容可能敏感]** → VFS 不改变现有本机进程权限，也不提供网络服务；profile 的 recall 身份过滤保持不变。若未来开放远程 API，必须另做认证和 scope 授权设计。

## Migration Plan

1. 先添加 resolver、服务和安全测试，不接入现有命令。
2. 添加 `oks fs` CLI 与 JSON 契约测试。
3. 在 recall hit 中增加 URI，并运行现有 recall/evaluation 回归测试。
4. 更新中文文档和 CLI reference，纠正 L0/L1/L2 类比。
5. 运行定向测试、完整 pytest、发行物内容校验和 `git diff --check`。

本变更无数据迁移。回滚时删除 `oks fs` 命令和 VFS 模块，并停止输出新增 `uri` 字段；现有实例文件无需修改或恢复。

## Open Questions

只读 MVP 没有阻塞性开放问题。以下能力明确延期，出现真实需求后单独提案：跨实例 URI、允许 mount 内 symlink、目录语义 sidecar、层级检索、远程 API 权限和 URI redirect。
