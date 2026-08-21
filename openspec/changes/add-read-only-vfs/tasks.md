## 1. URI 与安全解析

- [ ] 1.1 为 canonical `oks://` URI、公开 scope、百分号编码和非法 URI 编写失败测试
- [ ] 1.2 实现 `OksUri`、mount 表、`VfsError` 和 URI render/parse
- [ ] 1.3 为目录穿越、排除路径和任意 symlink 编写安全测试并实现 `VfsResolver`

## 2. 只读文件系统服务

- [ ] 2.1 为 `ls`、`stat` 和 UTF-8 分页 `read` 编写失败测试并实现最小服务
- [ ] 2.2 为有界 `tree`、机械 `overview` 和字面 `find` 编写失败测试并实现稳定排序、截断与跳过统计
- [ ] 2.3 验证所有服务操作前后文件内容快照不变，并覆盖二进制、缺失路径和错误节点类型

## 3. CLI 契约

- [ ] 3.1 注册 `oks fs` Typer 子命令组并实现 `table|json` 输出
- [ ] 3.2 覆盖 `oks-fs-response/v1`、稳定错误码、退出码、输出上限及不存在写命令的 CLI 测试

## 4. Recall URI 兼容扩展

- [ ] 4.1 为 knowledge、raw 和 profile hit 新增 URI 的回归测试
- [ ] 4.2 使用共享 resolver 生成 hit URI，保留 `recall-response/v1`、`recall-hit/v1`、slug、source_path、评分和排序
- [ ] 4.3 运行现有 recall 与 evaluation 测试，确认开放对象调用方保持兼容

## 5. 文档与交付验证

- [ ] 5.1 更新 CLI reference、架构说明和文件系统范式，纠正现有 L0/L1/L2 直接对应表述
- [ ] 5.2 运行 VFS/CLI/Recall 定向测试、完整 pytest、OpenSpec 严格校验和 `git diff --check`
- [ ] 5.3 构建 wheel/sdist 并运行发行物内容校验，确认没有新运行时依赖或计划外文件
