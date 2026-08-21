## ADDED Requirements

### Requirement: Canonical OKS URI
系统 SHALL 使用 `oks://{scope}/{path}` 作为当前知识库实例内资源的 canonical URI。实例根目录 SHALL 由现有 `get_kb_root()` 解析，URI 不携带实例 authority；根 URI SHALL 表示为 `oks://`。

支持的公开 scope SHALL 仅包括 `profiles`、`raw`、`wiki`、`drafts`、`mail`、`skills` 和 `traces`。`settings`、`_meta`、`.oks`、代码仓库目录及实例根目录下的其他路径 MUST NOT 通过 VFS 暴露。

#### Scenario: 解析 Wiki URI
- **WHEN** 当前实例包含 `wiki/computing/concepts/example.md`，调用方解析 `oks://wiki/computing/concepts/example.md`
- **THEN** 系统返回该文件的 canonical URI 和实例根目录内的对应节点

#### Scenario: 列出虚拟根目录
- **WHEN** 调用方列出 `oks://`
- **THEN** 系统返回受支持的公开 scope，而不返回物理实例根目录中的其他目录

#### Scenario: 拒绝未公开基础设施
- **WHEN** 调用方访问 `oks://settings/recall.yaml`、`oks://_meta/` 或 `oks://.oks/`
- **THEN** 系统返回 `UNSUPPORTED_SCOPE`，且不读取目标内容

### Requirement: Scope mount mapping
系统 SHALL 使用以下只读 mount 映射：

| URI scope | 物理位置 |
|---|---|
| `profiles` | `profiles/` |
| `raw` | `raw/`，但排除 `raw/executions/` 与 `raw/.logs/` |
| `wiki` | `wiki/` |
| `drafts` | `drafts/` |
| `mail` | `mail/` |
| `skills` | `.agents/skills/` |
| `traces` | `raw/executions/` |

每个物理节点 SHALL 只有一个 canonical URI。`raw/executions/` MUST 通过 `oks://traces/` 暴露，不得同时通过 `oks://raw/executions/` 暴露；`raw/.logs/` MUST NOT 暴露。

#### Scenario: 解析 Trace 别名
- **WHEN** 当前实例包含 `raw/executions/run-123/events.jsonl`，调用方访问 `oks://traces/run-123/events.jsonl`
- **THEN** 系统解析到该文件，并返回以 `oks://traces/` 开头的 canonical URI

#### Scenario: 防止重复 Raw 地址
- **WHEN** 调用方访问 `oks://raw/executions/run-123/events.jsonl`
- **THEN** 系统返回 `PATH_NOT_EXPOSED`，并提示使用 `oks://traces/`

#### Scenario: Skills mount 不存在
- **WHEN** 当前实例没有 `.agents/skills/`
- **THEN** `oks://skills/` 的 `stat` 返回 `PATH_NOT_FOUND`，系统不回退到实例外或编辑器专用的其他目录

### Requirement: URI normalization and containment
resolver MUST 在任何文件读取之前验证 URI。路径 segment MUST NOT 是空字符串、`.`、`..`，MUST NOT 包含 NUL、反斜线、编码后的 `/` 或编码后的 `\`。URI MUST NOT 包含 user-info、port、query 或 fragment。

resolver MUST 拒绝路径中任意位置的符号链接，并在规范化后验证目标仍位于对应 mount 根目录内。验证失败时 MUST NOT 返回物理绝对路径。

#### Scenario: 拒绝目录穿越
- **WHEN** 调用方访问 `oks://wiki/../profiles/team.md` 或包含等价百分号编码的 URI
- **THEN** 系统返回 `INVALID_URI`，且不访问 `profiles/team.md`

#### Scenario: 拒绝反斜线逃逸
- **WHEN** 调用方提交包含 `\` 或 `%5C` 的路径 segment
- **THEN** 系统返回 `INVALID_URI`

#### Scenario: 拒绝符号链接
- **WHEN** mount 内的任一路径 segment 是符号链接，无论其目标位于 mount 内还是 mount 外
- **THEN** 系统返回 `SYMLINK_NOT_ALLOWED`，且不读取链接目标

### Requirement: Read-only filesystem commands
CLI SHALL 新增 `oks fs` 命令组，并提供 `ls`、`tree`、`stat`、`read`、`overview` 和 `find`。所有命令 SHALL 只读取文件系统；命令组 MUST NOT 提供 `write`、`mkdir`、`mv`、`rm`、`cp` 或其他持久化修改操作。

所有命令 SHALL 支持人类可读文本输出和 `--format json`。JSON 成功响应 SHALL 使用 `oks-fs-response/v1`，至少包含 `schema_version`、`operation`、`uri` 和 `result`。JSON 失败响应 SHALL 包含稳定的 `error.code` 与不泄露物理绝对路径的 `error.message`。

#### Scenario: 列出目录
- **WHEN** 调用方执行 `oks fs ls oks://wiki/ --format json`
- **THEN** 系统仅返回该目录的直接子节点，每项包含 canonical URI、名称和节点类型

#### Scenario: 对文件执行 ls
- **WHEN** 调用方对普通文件执行 `oks fs ls`
- **THEN** 系统返回 `NOT_DIRECTORY`

#### Scenario: 不存在修改命令
- **WHEN** 调用方执行 `oks fs write`、`oks fs mv` 或 `oks fs rm`
- **THEN** CLI 将其视为未知命令，且知识库内容不发生变化

### Requirement: Bounded traversal and reading
`tree` SHALL 默认最多遍历 3 层，并接受范围为 0 到 10 的 `--depth`。`tree` SHALL 默认最多返回 1000 个节点，并在达到限制时显式返回 `truncated: true`。

`read` SHALL 只读取 UTF-8 普通文件，默认从字符 offset 0 返回最多 20,000 个字符；调用方可以使用 `--offset` 和 `--limit` 分页读取，单次 `--limit` MUST NOT 超过 1,000,000。响应 SHALL 包含 `offset`、`returned_chars`、`total_chars`、`truncated` 和下一页 offset。二进制或非 UTF-8 文件 SHALL 返回 `UNSUPPORTED_CONTENT`。

#### Scenario: Tree 达到节点上限
- **WHEN** 目标子树在当前深度内超过 1000 个节点
- **THEN** 系统停止遍历并返回 `truncated: true`，而不是继续产生无界输出

#### Scenario: 分页读取长文本
- **WHEN** 调用方读取超过 20,000 字符的 UTF-8 文件且未显式指定 limit
- **THEN** 系统返回前 20,000 个字符、`truncated: true` 和下一页 offset

#### Scenario: 拒绝二进制读取
- **WHEN** 调用方对图片、压缩包或无法以 UTF-8 解码的文件执行 `read`
- **THEN** 系统返回 `UNSUPPORTED_CONTENT`，但 `stat` 仍可报告该节点

### Requirement: Mechanical overview
`overview` SHALL 只生成机械目录清单，不调用 LLM、不写入 sidecar、不把结果保存为 Wiki 或其他知识。结果 SHALL 包含直接子目录、直接文件、节点类型计数，以及存在时的 `INDEX.md` canonical URI；它 MUST NOT 把 Raw、Wiki 和 frontmatter 声称为同一对象的 L0/L1/L2。

#### Scenario: 获取目录概览
- **WHEN** 调用方对包含子目录、Markdown 文件和 `INDEX.md` 的目录执行 `overview`
- **THEN** 系统返回机械统计与 `INDEX.md` URI，不生成或写入 `.abstract.md`、`.overview.md`

#### Scenario: Overview 保持只读
- **WHEN** 调用前后比较知识库文件快照
- **THEN** 除操作系统访问时间外，文件集合和文件内容保持不变

### Requirement: Deterministic literal find
`find` SHALL 在指定 URI 子树内对相对路径和 UTF-8 文本执行大小写不敏感的字面子串搜索，不执行正则表达式、查询改写、embedding、LLM 推理或递归 Agent 循环。默认最多返回 50 项，`--max-results` MUST 限制在 1 到 200；达到限制时 SHALL 返回 `truncated: true`。每个正文命中 snippet MUST NOT 超过 200 个字符。

#### Scenario: 在指定子树搜索
- **WHEN** 调用方执行 `oks fs find "atomic write" --under oks://wiki/computing/`
- **THEN** 系统只返回该 URI 子树内的路径或正文命中，并为每项返回 canonical URI

#### Scenario: Find 跳过不支持内容
- **WHEN** 子树包含二进制文件或无法读取的文件
- **THEN** 系统跳过这些文件，在响应中报告 `skipped_count`，并继续返回其他可靠命中

### Requirement: Recall hit URI compatibility
`recall-response/v1` 中的每个 episodic、profile 和 knowledge hit SHALL 新增 canonical `uri` 字段。knowledge hit SHALL 从实际 `file_path` 生成 URI；episodic/profile hit SHALL 从现有 `source_path` 生成 URI。现有 `slug`、`source_path`、排序、评分和 schema version MUST 保持不变。

#### Scenario: Knowledge hit 增加 URI
- **WHEN** `oks recall` 命中 `wiki/computing/concepts/example.md`
- **THEN** 该命中包含 `uri: "oks://wiki/computing/concepts/example.md"`，并保留原有 slug、分数和 `recall-hit/v1`

#### Scenario: Profile hit 增加 URI
- **WHEN** 授权的 profile 查询命中 `profiles/users/alice/profile.md`
- **THEN** 该命中包含 `uri: "oks://profiles/users/alice/profile.md"`，且既有 `source_path` 不变

#### Scenario: 现有调用方保持兼容
- **WHEN** 调用方忽略新增的 `uri` 字段并继续读取原有字段
- **THEN** 返回结构、字段类型、排序与既有行为保持兼容
