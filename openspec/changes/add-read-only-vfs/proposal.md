## Why

OKS 目前以 Markdown、目录和 Git 作为内容真源，但对 Agent 暴露的主要标识仍是 slug 或物理相对路径，缺少稳定、统一、可浏览的上下文地址。新增严格只读的虚拟命名空间，可以让 Agent 确定性定位和浏览 Raw、Draft、Wiki、Profile、Skill 与 Trace，同时不改变 Raw → Draft → Wiki 的治理门控。

## What Changes

- 新增 canonical `oks://` URI，作为单个 OKS 实例内资源的稳定逻辑地址。
- 新增 URI resolver，将受支持的 URI 安全映射到当前知识库根目录内的既有文件或目录。
- 新增只读 `oks fs` 命令：`ls`、`tree`、`stat`、`read`、`overview`、`find`。
- 在 `recall-response/v1` 的每个命中项中增加 canonical `uri`，保留现有 `slug` 和 `source_path` 字段以兼容调用方。
- 明确 VFS 只是一层访问接口：Markdown + Git 继续作为内容真源；FTS5 与其他搜索后端继续是可重建索引。
- 明确禁止第一版提供通用 `write`、`mv`、`rm`，防止绕过 `raw-commit`、Draft 审核和 Wiki promotion。
- 修正文档中把 frontmatter、Wiki、Raw 直接等同于 OpenViking L0/L1/L2 的表述；它们只是概念类比，不是同一对象的三层表示。

本变更不包含破坏性接口变更。

## Capabilities

### New Capabilities

- `virtual-context-filesystem`: 定义 `oks://` URI、路径解析、安全边界、只读浏览命令及其输出契约。

### Modified Capabilities

现有仓库尚无 OpenSpec capability；本变更通过兼容性字段扩展现有 recall 响应，不修改已有 OpenSpec requirement。

## Impact

- CLI：新增 `oks fs` 子命令组。
- 核心模块：新增 URI 数据类型、resolver 和只读文件系统服务。
- Recall：episodic、profile、knowledge 命中项新增 `uri` 字段，schema version 保持 `v1`。
- 文档：CLI reference、文件系统范式和架构说明需要更新。
- 测试：覆盖 URI 规范化、目录穿越防护、符号链接逃逸、命令输出、recall 兼容性和只读约束。
- 依赖：不引入向量数据库、LLM、OpenViking 代码或新的运行时依赖。
