# 可选命令健康状态语义——设计与实现计划

> **给实现者：** 修改行为时使用 `superpowers:test-driven-development`，在宣称修复完成前使用 `superpowers:verification-before-completion`。本改动规模较小，可以在一个会话中完成，无需并行开发。

**目标：** 当某个 Provider 仅缺少明确标记为可选的命令时，让 `oks capability doctor` 仍将该 Provider 报告为健康；如果其他 Provider 也不存在必需检查失败，则整体环境同样报告为健康。同时保留失败的可选检查作为诊断信息。

**架构：** 保持 Provider 解析逻辑和 doctor 的公共返回结构不变。修复只应发生在 `capability_doctor()` 的汇总规则中：仅当失败检查不是说明项，且未标记为 `required: false` 时，才影响 `healthy` 和 `overall`。`_provider_status()` 已经遵循这条规则，因此只需对齐汇总逻辑即可消除当前矛盾，无需引入新抽象。

**技术栈：** Python 3.12+、pytest，以及现有的 `knowledge_studio.capability_commands` 模块。

## 1. 问题与证据

`_check_provider_health()` 会记录所有命令检查。对于 Provider 中 `requirements.optional_commands` 下的命令，它会在检查结果中添加 `required: false`。

`_provider_status()` 尊重这一标记：当 `required is False` 时，它会排除该失败检查。但是，`capability_doctor()` 当前会把所有失败的非说明检查都视为健康问题。因此，一个 Provider 可能返回互相矛盾的字段：

```json
{
  "healthy": false,
  "status": "ready",
  "checks": [
    {
      "type": "command",
      "name": "ffmpeg",
      "available": false,
      "required": false
    }
  ]
}
```

即使缺失的依赖已明确声明为可选项，顶层结果也会从 `healthy` 变成 `issues_found`。

现有的 `yt-dlp` Provider 是最直接的场景：`yt-dlp` 是必需命令，`ffmpeg` 是可选命令。测试应直接模拟这一契约，不依赖开发机器上实际安装了哪些二进制程序。

## 2. 目标行为

每项健康检查都应遵循以下规则：

| 检查结果 | 该检查是否导致 Provider 不健康 | 是否将顶层 `overall` 置为 `issues_found` | 是否继续出现在 `checks` 中 |
|---|---:|---:|---:|
| 必需检查成功 | 否 | 否 | 是 |
| 必需检查失败 | 是 | 是 | 是 |
| 可选检查成功 | 否 | 否 | 是 |
| 可选检查失败 | 否 | 否 | 是 |
| 说明项 | 否 | 否 | 是 |

具体规则如下：

- 只有同时满足以下条件的失败检查才具有阻断性：`available is False`、类型不是 `note`，并且 `required is not False`。
- 可选检查失败时，原有的 `available: false` 和 `required: false` 必须继续保留。它表示诊断层面的能力降级，而不是健康状态失败。
- `capability_doctor()` 保持当前返回结构和状态值集合不变。
- 必需命令缺失时，仍然返回 `healthy: false`、Provider 状态 `unavailable`，以及顶层 `overall: issues_found`。

## 3. 范围与非目标

### 本次范围

- 将 `capability_doctor()` 中 `healthy` 和 `overall` 的汇总逻辑，与 `_provider_status()` 已采用的可选检查语义对齐。
- 为“可选命令缺失”和“必需命令缺失”分别添加回归测试。
- 在返回的诊断结果中保留失败的可选检查。

### 非目标

- 修改 Provider YAML 或重新定义任何依赖是否必需。
- 修改 `_provider_status()` 或其中针对特定 Provider 的状态分支。
- 新增 warning/degraded 状态，或改变 JSON、文本输出结构。
- 将健康检查重构为新的类或辅助函数。
- 修复外部 Provider 说明中现有的 `oks capability probe` 文案。
- 修改 Python 包名与导入名映射、指标、安装行为或能力目录。

## 4. 兼容性与风险

这是一次不需要接口迁移的行为修正。调用方仍会收到相同的字段和相同的单项检查。唯一有意改变的结果是：

- 当所有失败检查都明确属于可选项时，`providers[*].healthy` 从 `false` 变为 `true`。
- 当所有 Provider 都不存在必需检查失败时，`overall` 从 `issues_found` 变为 `healthy`。

主要回归风险是误把必需检查失败也忽略掉。为此，需要增加一项对应的负向对照测试。测试必须模拟命令发现结果，避免开发环境和 CI 环境中安装的软件不同而导致结果不稳定。

## 5. 验收标准

满足以下全部条件后，才算完成本次改动：

- 当 Provider 的必需命令可用、可选命令不可用时，返回 `status == "ready"` 且 `healthy is True`。
- 不可用的可选检查仍保留在结果中，并且 `available is False`、`required is False`。
- 当它是唯一 Provider 时，doctor 顶层结果为 `overall == "healthy"`。
- 当 Provider 的必需命令不可用时，返回 `status == "unavailable"`、`healthy is False`，且顶层结果为 `overall == "issues_found"`。
- 定向回归测试和完整 pytest 测试套件全部通过。
- Wheel/sdist 内容校验通过，构建过程不会修改已跟踪文件。

## 6. 文件变更范围

- 新建 `cli/tests/test_capability_commands.py`：为 doctor 汇总逻辑提供不依赖本机环境的回归测试。
- 修改 `cli/knowledge_studio/capability_commands.py`：在 `capability_doctor()` 的失败判断中排除明确标记为可选的失败检查。

不应修改其他生产代码文件。

## 7. 实现计划

### 任务 1：添加回归测试并对齐 doctor 汇总逻辑

**涉及文件：**

- 新建：`cli/tests/test_capability_commands.py`
- 修改：`cli/knowledge_studio/capability_commands.py:338-354`
- 测试：`cli/tests/test_capability_commands.py`

#### 步骤 1：按需准备隔离的开发环境

在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ./cli pytest requests build
```

预期结果：安装命令以状态码 0 退出，并且 Python 从当前工作区导入本项目包。`.venv/` 继续被 Git 忽略。

#### 步骤 2：先编写失败测试

新建 `cli/tests/test_capability_commands.py`。测试应使用真实的 `_check_provider_health()` 和 `capability_doctor()` 汇总逻辑，只替换 Provider 发现和依赖本机环境的命令查找：

```python
from knowledge_studio import capability_commands


def _command_check(name: str, available: bool) -> dict:
    return {
        "type": "command",
        "name": name,
        "available": available,
        "path": f"/usr/bin/{name}" if available else None,
        "suggestion": None if available else f"Install {name}",
    }


def test_optional_command_failure_does_not_mark_doctor_unhealthy(
    tmp_path, monkeypatch
):
    provider = {
        "id": "yt-dlp",
        "label": "yt-dlp",
        "execution": "managed",
        "requirements": {
            "command": "yt-dlp",
            "optional_commands": ["ffmpeg"],
        },
    }
    availability = {"yt-dlp": True, "ffmpeg": False}
    monkeypatch.setattr(
        capability_commands, "_scan_providers", lambda _root: [provider]
    )
    monkeypatch.setattr(
        capability_commands,
        "_check_command",
        lambda name: _command_check(name, availability[name]),
    )

    result = capability_commands.capability_doctor(tmp_path)

    provider_result = result["providers"][0]
    optional_check = next(
        check for check in provider_result["checks"] if check["name"] == "ffmpeg"
    )
    assert provider_result["status"] == "ready"
    assert provider_result["healthy"] is True
    assert result["overall"] == "healthy"
    assert optional_check["available"] is False
    assert optional_check["required"] is False


def test_required_command_failure_still_marks_doctor_unhealthy(
    tmp_path, monkeypatch
):
    provider = {
        "id": "local-tool",
        "label": "Local Tool",
        "execution": "managed",
        "requirements": {"command": "required-tool"},
    }
    monkeypatch.setattr(
        capability_commands, "_scan_providers", lambda _root: [provider]
    )
    monkeypatch.setattr(
        capability_commands,
        "_check_command",
        lambda name: _command_check(name, False),
    )

    result = capability_commands.capability_doctor(tmp_path)

    provider_result = result["providers"][0]
    assert provider_result["status"] == "unavailable"
    assert provider_result["healthy"] is False
    assert result["overall"] == "issues_found"
```

这种写法不会调用真实的系统命令。它还会验证 `_check_provider_health()` 确实添加了 `required: false`，而不是仅测试人工构造的最终检查列表。

#### 步骤 3：运行测试并确认能够复现问题

```bash
.venv/bin/python -m pytest cli/tests/test_capability_commands.py -q
```

修改生产代码之前的预期结果：可选命令测试在 `assert provider_result["healthy"] is True` 处失败，因为实际值为 `False`；必需命令对照测试通过。

如果尚未修改生产代码，但可选命令测试已经通过，应停止实施并重新检查当前工作区，不能继续按已失效的问题假设修改代码。

#### 步骤 4：实施最小行为修改

在 `capability_doctor()` 中，将宽泛的 `has_failure` 判断替换为只判断必需检查失败的逻辑：

```python
has_required_failure = any(
    c.get("available") is False
    and c.get("type") != "note"
    and c.get("required") is not False
    for c in checks
)
if has_required_failure:
    all_healthy = False
```

Provider 的 `healthy` 字段改为使用 `not has_required_failure`。不要修改检查列表或 `_provider_status()`。

#### 步骤 5：运行定向测试

```bash
.venv/bin/python -m pytest cli/tests/test_capability_commands.py -q
```

预期结果：显示 `2 passed`，并以状态码 0 退出。

#### 步骤 6：运行完整行为测试套件

```bash
.venv/bin/python -m pytest -q
```

预期结果：没有失败测试，并以状态码 0 退出。

#### 步骤 7：验证构建产物和仓库状态

```bash
git status --porcelain
.venv/bin/python -m build --outdir dist ./cli
git status --porcelain
.venv/bin/python cli/scripts/check_dist.py dist
git diff --check
```

预期结果：

- 两次状态快照只列出计划内的源代码、测试文件和本文档；构建过程本身没有产生新的已跟踪文件变更。
- 构建命令以状态码 0 退出。
- `check_dist.py` 以状态码 0 退出。
- `git diff --check` 不输出任何内容，并以状态码 0 退出。

如果出现其他已跟踪文件变更，应停止并检查其来源，不能将它直接纳入本次修复。

#### 步骤 8：审阅并提交实现

检查最终差异，然后只暂存实现代码和回归测试：

```bash
git diff -- cli/knowledge_studio/capability_commands.py cli/tests/test_capability_commands.py
git add cli/knowledge_studio/capability_commands.py cli/tests/test_capability_commands.py
git commit -m "fix(capability): ignore missing optional commands in health status"
```

预期结果：生成一个聚焦的提交，只包含一处生产代码修改和两项回归测试。未经用户针对该操作的单独明确授权，不得推送或创建 Pull Request。

## 8. Pull Request 交付说明

建议的 PR 标题：

```text
fix(capability): ignore missing optional commands in health status
```

PR 描述应说明修改前后的语义差异，指出可选失败仍会保留在 `checks` 中，并列出定向 pytest、完整 pytest 和打包校验结果。不要声称本次改动涉及安装方式、Provider 定义或能力探测。
