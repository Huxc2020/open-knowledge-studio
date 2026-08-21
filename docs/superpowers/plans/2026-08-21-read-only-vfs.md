# OKS Read-Only Virtual Filesystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为当前 OKS 实例增加安全、严格只读的 `oks://` 虚拟命名空间、六个浏览命令，并为所有 recall hit 补充 canonical URI。

**Architecture:** 新建单一 `knowledge_studio.vfs` 模块，集中承载 URI 类型、白名单 mount、路径 containment、只读服务和稳定错误码；CLI 只负责 Typer 参数与 text/JSON 渲染。Recall 使用同一 resolver 从已存在的 `file_path`/`source_path` 生成 URI，避免出现第二套路径拼接规则。

**Tech Stack:** Python 3.12+、`pathlib`、`urllib.parse`、Typer、Rich、pytest；不增加运行时依赖。

**Spec:** `openspec/changes/add-read-only-vfs/proposal.md`、`openspec/changes/add-read-only-vfs/specs/virtual-context-filesystem/spec.md`、`openspec/changes/add-read-only-vfs/design.md`

## Global Constraints

- VFS MUST 严格只读；不得增加 `write`、`mkdir`、`mv`、`rm`、`cp` 或任何持久化操作。
- 公开 scope 固定为 `profiles`、`raw`、`wiki`、`drafts`、`mail`、`skills`、`traces`。
- `settings`、`_meta`、`.oks` 和实例根目录中的其他路径不得通过 VFS 暴露。
- `raw/executions` 只能映射到 `oks://traces/`；`raw/.logs` 不公开。
- 任意路径 component 是 symlink 时一律返回 `SYMLINK_NOT_ALLOWED`。
- `tree` 默认 depth=3、最大 depth=10、默认最大 1000 节点。
- `read` 默认最多 20,000 字符，单次最多 1,000,000 字符，只接受 UTF-8 普通文件。
- `find` 是大小写不敏感的字面搜索，默认最多 50 项，范围为 1–200，snippet 最多 200 字符。
- JSON 外壳版本固定为 `oks-fs-response/v1`。
- Recall 保持 `recall-response/v1`、`recall-hit/v1`、既有字段、分数和排序不变，只增加 `uri`。
- 不生成 L0/L1 sidecar，不调用 LLM、embedding 或 reranker，不复制 OpenViking 代码。

---

## File Structure

- Create `cli/knowledge_studio/vfs.py`: URI parse/render、mount 定义、安全 resolver、只读服务与 JSON response helper。第一版保持在一个可完整审阅的模块中；若实现超过约 500 行，再在后续独立重构中拆分，当前任务不提前抽象。
- Create `cli/tests/test_vfs.py`: URI、安全、服务操作、限界和只读快照测试。
- Modify `cli/knowledge_studio/cli.py:101-149`: 注册 `fs_app`；在命令定义区增加六个子命令和统一错误渲染。
- Modify `cli/tests/test_cli.py`: `oks fs` 的 JSON/text、参数、退出码和未知写命令测试。
- Modify `cli/knowledge_studio/recall.py:336-440, 530-565, 620-657`: 为 episodic/profile/native/backend knowledge hit 添加 URI。
- Modify `cli/tests/test_recall.py`: 验证 knowledge/raw/profile URI 与原 schema、排序、分数兼容。
- Modify `docs/reference/cli.md:10-33`: 记录 `oks fs` 命令和 JSON 契约。
- Modify `docs/concepts/file-system-paradigm.md:10-24`: 将 L0/L1/L2 的“实现对应”改成“设计启发与明确差异”。
- Modify `docs/concepts/architecture.md:179-186`: 增加 VFS 访问层和只读边界。

---

### Task 1: Canonical URI and Safe Resolver

**Files:**
- Create: `cli/knowledge_studio/vfs.py`
- Create: `cli/tests/test_vfs.py`
- Reference: `cli/knowledge_studio/config.py:137-174`
- Reference: `cli/knowledge_studio/store.py:37-55`

**Interfaces:**
- Consumes: `knowledge_studio.config.get_kb_root() -> Path` when no explicit test root is supplied.
- Produces: `FS_RESPONSE_SCHEMA`, `VfsError`, `OksUri`, `ResolvedNode`, `VfsResolver.parse()`, `VfsResolver.resolve()`, `VfsResolver.uri_for_path()`.

- [ ] **Step 1: Write failing URI grammar tests**

Create `cli/tests/test_vfs.py` with a reusable instance fixture and explicit grammar cases:

```python
from pathlib import Path

import pytest


@pytest.fixture
def vfs_root(tmp_path: Path) -> Path:
    for relative in (
        "profiles",
        "raw/executions",
        "raw/.logs",
        "wiki/computing/concepts",
        "drafts",
        "mail",
        ".agents/skills/query",
    ):
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)
    (tmp_path / "profiles/team.md").write_text("# Team\n", encoding="utf-8")
    (tmp_path / "wiki/computing/concepts/中文 页面.md").write_text(
        "# 中文页面\n", encoding="utf-8"
    )
    return tmp_path


def test_parse_and_render_canonical_uri(vfs_root):
    from knowledge_studio.vfs import OksUri

    uri = OksUri.parse("oks://wiki/computing/concepts/%E4%B8%AD%E6%96%87%20%E9%A1%B5%E9%9D%A2.md")

    assert uri.scope == "wiki"
    assert uri.parts == ("computing", "concepts", "中文 页面.md")
    assert uri.render() == "oks://wiki/computing/concepts/%E4%B8%AD%E6%96%87%20%E9%A1%B5%E9%9D%A2.md"


@pytest.mark.parametrize(
    ("uri", "code"),
    [
        ("http://wiki/example.md", "INVALID_URI"),
        ("oks://settings/recall.yaml", "UNSUPPORTED_SCOPE"),
        ("oks://wiki/../profiles/team.md", "INVALID_URI"),
        ("oks://wiki/%2E%2E/profiles/team.md", "INVALID_URI"),
        ("oks://wiki/a%2Fb.md", "INVALID_URI"),
        ("oks://wiki/a%5Cb.md", "INVALID_URI"),
        ("oks://wiki/a.md?raw=1", "INVALID_URI"),
        ("oks://wiki/a.md#section", "INVALID_URI"),
    ],
)
def test_invalid_uri_is_rejected(uri, code):
    from knowledge_studio.vfs import OksUri, VfsError

    with pytest.raises(VfsError) as exc:
        OksUri.parse(uri)
    assert exc.value.code == code
```

- [ ] **Step 2: Run the grammar tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest cli/tests/test_vfs.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'knowledge_studio.vfs'`.

- [ ] **Step 3: Implement URI types, mount table, and stable errors**

Create the initial `cli/knowledge_studio/vfs.py` interface:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from knowledge_studio.config import get_kb_root

FS_RESPONSE_SCHEMA = "oks-fs-response/v1"


class VfsError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message


@dataclass(frozen=True)
class Mount:
    scope: str
    relative_root: tuple[str, ...]
    excluded_prefixes: tuple[tuple[str, ...], ...] = ()


MOUNTS: dict[str, Mount] = {
    "profiles": Mount("profiles", ("profiles",)),
    "raw": Mount("raw", ("raw",), (("executions",), (".logs",))),
    "wiki": Mount("wiki", ("wiki",)),
    "drafts": Mount("drafts", ("drafts",)),
    "mail": Mount("mail", ("mail",)),
    "skills": Mount("skills", (".agents", "skills")),
    "traces": Mount("traces", ("raw", "executions")),
}


@dataclass(frozen=True)
class OksUri:
    scope: str | None
    parts: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "OksUri":
        if value == "oks://":
            return cls(scope=None)
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise VfsError("INVALID_URI", "URI contains an invalid port") from exc
        if (
            parsed.scheme != "oks"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise VfsError("INVALID_URI", "URI must use oks:// without credentials, port, query, or fragment")
        scope = parsed.netloc
        if scope not in MOUNTS:
            raise VfsError("UNSUPPORTED_SCOPE", f"Unsupported OKS scope: {scope}")
        path_body = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
        if path_body.endswith("/"):
            path_body = path_body[:-1]
        if "//" in path_body:
            raise VfsError("INVALID_URI", "Empty URI path segments are not allowed")
        raw_parts = tuple(path_body.split("/")) if path_body else ()
        decoded: list[str] = []
        for raw_part in raw_parts:
            lowered = raw_part.lower()
            if "%2f" in lowered or "%5c" in lowered:
                raise VfsError("INVALID_URI", "Encoded path separators are not allowed")
            try:
                part = unquote(raw_part, encoding="utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise VfsError("INVALID_URI", "URI path is not valid UTF-8") from exc
            if part in {"", ".", ".."} or "\x00" in part or "\\" in part:
                raise VfsError("INVALID_URI", "Unsafe URI path segment")
            decoded.append(part)
        return cls(scope=scope, parts=tuple(decoded))

    def render(self) -> str:
        if self.scope is None:
            return "oks://"
        suffix = "/".join(quote(part, safe="-._~") for part in self.parts)
        return f"oks://{self.scope}/" + suffix if suffix else f"oks://{self.scope}/"


@dataclass(frozen=True)
class ResolvedNode:
    uri: OksUri
    path: Path | None
    mount: Mount | None
    synthetic_root: bool = False
```

Use `urlsplit`, special-case the exact root `oks://`, and do not call `unquote()` a second time elsewhere in the resolver.

- [ ] **Step 4: Add failing containment, exclusion, and symlink tests**

Append tests that pin canonical mount behavior:

```python
def test_resolver_maps_public_mounts_and_trace_alias(vfs_root):
    from knowledge_studio.vfs import VfsResolver

    trace = vfs_root / "raw/executions/run-1/events.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text("{}\n", encoding="utf-8")
    resolver = VfsResolver(vfs_root)

    assert resolver.resolve("oks://profiles/team.md").path == vfs_root / "profiles/team.md"
    assert resolver.resolve("oks://traces/run-1/events.jsonl").path == trace
    assert resolver.uri_for_path(trace) == "oks://traces/run-1/events.jsonl"


@pytest.mark.parametrize("uri", ["oks://raw/executions/", "oks://raw/.logs/"])
def test_raw_internal_paths_are_not_exposed(vfs_root, uri):
    from knowledge_studio.vfs import VfsError, VfsResolver

    with pytest.raises(VfsError) as exc:
        VfsResolver(vfs_root).resolve(uri)
    assert exc.value.code == "PATH_NOT_EXPOSED"


def test_any_symlink_component_is_rejected(vfs_root):
    from knowledge_studio.vfs import VfsError, VfsResolver

    target = vfs_root / "profiles/team.md"
    link = vfs_root / "wiki/link.md"
    link.symlink_to(target)

    with pytest.raises(VfsError) as exc:
        VfsResolver(vfs_root).resolve("oks://wiki/link.md")
    assert exc.value.code == "SYMLINK_NOT_ALLOWED"
```

- [ ] **Step 5: Implement `VfsResolver` and make Task 1 tests pass**

Implement these exact signatures:

```python
class VfsResolver:
    def __init__(self, root: Path | None = None):
        self.root = (root or get_kb_root()).expanduser().resolve()

    def parse(self, value: str) -> OksUri:
        return OksUri.parse(value)

    @staticmethod
    def _reject_symlinks(root: Path, parts: tuple[str, ...]) -> None:
        current = root
        if current.is_symlink():
            raise VfsError("SYMLINK_NOT_ALLOWED", "Mount root is a symbolic link")
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise VfsError("SYMLINK_NOT_ALLOWED", "Symbolic links are not exposed")

    def resolve(self, value: str | OksUri, *, must_exist: bool = True) -> ResolvedNode:
        uri = value if isinstance(value, OksUri) else self.parse(value)
        if uri.scope is None:
            return ResolvedNode(uri=uri, path=None, mount=None, synthetic_root=True)
        mount = MOUNTS[uri.scope]
        if any(uri.parts[: len(prefix)] == prefix for prefix in mount.excluded_prefixes):
            raise VfsError("PATH_NOT_EXPOSED", f"Path is not exposed under oks://{uri.scope}/")
        mount_root = self.root.joinpath(*mount.relative_root)
        self._reject_symlinks(self.root, mount.relative_root + uri.parts)
        candidate = mount_root.joinpath(*uri.parts)
        resolved_root = mount_root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        if not resolved_candidate.is_relative_to(resolved_root):
            raise VfsError("INVALID_URI", "URI escapes its public scope")
        if must_exist and not candidate.exists():
            raise VfsError("PATH_NOT_FOUND", f"Path not found: {uri.render()}")
        return ResolvedNode(uri=uri, path=candidate, mount=mount)

    def uri_for_path(self, path: Path) -> str:
        candidate = path.expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        candidate = candidate.absolute()
        ordered = [MOUNTS["traces"]] + [
            mount for scope, mount in MOUNTS.items() if scope != "traces"
        ]
        for mount in ordered:
            mount_root = self.root.joinpath(*mount.relative_root).absolute()
            try:
                relative = candidate.relative_to(mount_root)
            except ValueError:
                continue
            parts = relative.parts
            if any(parts[: len(prefix)] == prefix for prefix in mount.excluded_prefixes):
                continue
            self._reject_symlinks(self.root, mount.relative_root + parts)
            return OksUri(mount.scope, tuple(parts)).render()
        raise VfsError("PATH_NOT_EXPOSED", "Physical path is outside public OKS scopes")
```

`resolve()` must return a synthetic `ResolvedNode` for `oks://`; for mounted paths, inspect the mount root and every candidate prefix with `lstat()`/`is_symlink()`, apply excluded prefixes before existence checks, verify `candidate.resolve(strict=False).is_relative_to(mount_root.resolve(strict=False))`, then return `PATH_NOT_FOUND` without exposing absolute paths when required nodes do not exist.

`uri_for_path()` must test `traces` before `raw`, reject excluded raw paths, then render each relative segment through `OksUri`. It must raise `PATH_NOT_EXPOSED` for any physical path outside a public mount.

- [ ] **Step 6: Run Task 1 tests**

Run:

```bash
.venv/bin/python -m pytest cli/tests/test_vfs.py -q
```

Expected: all URI/resolver tests pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add cli/knowledge_studio/vfs.py cli/tests/test_vfs.py
git commit -m "feat(vfs): add safe canonical URI resolver"
```

---

### Task 2: Basic Read-Only Operations

**Files:**
- Modify: `cli/knowledge_studio/vfs.py`
- Modify: `cli/tests/test_vfs.py`

**Interfaces:**
- Consumes: `VfsResolver.resolve()` and `VfsResolver.uri_for_path()` from Task 1.
- Produces: `VfsService.ls()`, `VfsService.stat()`, `VfsService.read()` returning JSON-serializable dictionaries.

- [ ] **Step 1: Write failing service tests**

Append:

```python
def test_ls_and_stat_return_canonical_nodes(vfs_root):
    from knowledge_studio.vfs import VfsResolver, VfsService

    service = VfsService(VfsResolver(vfs_root))
    listing = service.ls("oks://wiki/computing/concepts/")
    stat = service.stat("oks://profiles/team.md")

    assert listing["entries"] == [
        {
            "name": "中文 页面.md",
            "type": "file",
            "uri": "oks://wiki/computing/concepts/%E4%B8%AD%E6%96%87%20%E9%A1%B5%E9%9D%A2.md",
        }
    ]
    assert stat["type"] == "file"
    assert stat["uri"] == "oks://profiles/team.md"
    assert stat["size"] == len("# Team\n".encode())


def test_read_is_utf8_paginated(vfs_root):
    from knowledge_studio.vfs import VfsResolver, VfsService

    page = vfs_root / "wiki/computing/concepts/long.md"
    page.write_text("中文" * 15_000, encoding="utf-8")
    service = VfsService(VfsResolver(vfs_root))

    first = service.read("oks://wiki/computing/concepts/long.md")
    second = service.read(
        "oks://wiki/computing/concepts/long.md",
        offset=first["next_offset"],
        limit=10_000,
    )

    assert first["returned_chars"] == 20_000
    assert first["truncated"] is True
    assert second["offset"] == 20_000
    assert first["content"] + second["content"] == "中文" * 15_000


def test_read_rejects_binary(vfs_root):
    from knowledge_studio.vfs import VfsError, VfsResolver, VfsService

    binary = vfs_root / "raw/blob.bin"
    binary.write_bytes(b"\xff\x00")
    with pytest.raises(VfsError) as exc:
        VfsService(VfsResolver(vfs_root)).read("oks://raw/blob.bin")
    assert exc.value.code == "UNSUPPORTED_CONTENT"
```

- [ ] **Step 2: Run focused tests and verify missing service failure**

Run:

```bash
.venv/bin/python -m pytest cli/tests/test_vfs.py -k 'ls or stat or read' -q
```

Expected: FAIL because `VfsService` is not defined.

- [ ] **Step 3: Implement basic service methods**

Add the service class with the signatures declared in **Interfaces**. Start with these complete shared validators so all three operations enforce the same node-type and pagination rules:

```python
def _node_type(path: Path) -> str:
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "special"


def _validate_slice(offset: int, limit: int) -> None:
    if offset < 0 or not 1 <= limit <= 1_000_000:
        raise VfsError("INVALID_ARGUMENT", "offset must be non-negative and limit must be 1..1000000")
```

Implement `VfsService.__init__(resolver: VfsResolver | None = None)`, `ls(uri)`, `stat(uri)`, and `read(uri, *, offset=0, limit=20_000)` without adding any write helper. Use one private `_entry(path)` helper for stable `name/type/uri` fields. Root `ls("oks://")` must synthesize one directory entry per `MOUNTS` key without checking whether the mount exists. Physical directory listings must sort by `name.casefold(), name`, skip mount-excluded children, and reject symlinks rather than silently follow them.

`stat()` returns `uri`, `type`, `size` (`None` for directories), `modified_at` as UTC ISO-8601, and `mount`. `read()` validates `offset >= 0`, `1 <= limit <= 1_000_000`, rejects directories/special files, catches `UnicodeDecodeError`, and returns the exact pagination fields from the spec.

- [ ] **Step 4: Add a read-only snapshot assertion**

Create a test helper that hashes relative path + bytes for every regular non-symlink file, call `ls/stat/read`, and assert the before/after dictionaries are identical. Do not compare access time.

- [ ] **Step 5: Run Task 2 tests**

```bash
.venv/bin/python -m pytest cli/tests/test_vfs.py -q
```

Expected: all Task 1–2 tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add cli/knowledge_studio/vfs.py cli/tests/test_vfs.py
git commit -m "feat(vfs): add bounded read-only file operations"
```

---

### Task 3: Bounded Tree, Mechanical Overview, and Literal Find

**Files:**
- Modify: `cli/knowledge_studio/vfs.py`
- Modify: `cli/tests/test_vfs.py`

**Interfaces:**
- Consumes: Task 2 `_entry()` and resolver safety rules.
- Produces: `VfsService.tree()`, `VfsService.overview()`, `VfsService.find()`.

- [ ] **Step 1: Write failing traversal tests**

Add tests with small explicit limits so truncation is deterministic:

```python
def test_tree_honors_depth_and_entry_limit(vfs_root):
    from knowledge_studio.vfs import VfsResolver, VfsService

    for name in ("a.md", "b.md", "c.md"):
        (vfs_root / "wiki" / name).write_text(name, encoding="utf-8")
    service = VfsService(VfsResolver(vfs_root))

    result = service.tree("oks://wiki/", depth=1, max_entries=2)

    assert len(result["entries"]) == 2
    assert result["truncated"] is True


def test_overview_is_mechanical_and_does_not_write_sidecars(vfs_root):
    from knowledge_studio.vfs import VfsResolver, VfsService

    index = vfs_root / "wiki/INDEX.md"
    index.write_text("# Index\n", encoding="utf-8")
    result = VfsService(VfsResolver(vfs_root)).overview("oks://wiki/")

    assert result["index_uri"] == "oks://wiki/INDEX.md"
    assert result["counts"]["file"] == 1
    assert not (vfs_root / "wiki/.abstract.md").exists()
    assert not (vfs_root / "wiki/.overview.md").exists()


def test_find_is_literal_bounded_and_reports_skips(vfs_root):
    from knowledge_studio.vfs import VfsResolver, VfsService

    (vfs_root / "wiki/a.md").write_text("Atomic Write prevents corruption", encoding="utf-8")
    (vfs_root / "wiki/b[1].md").write_text("literal brackets", encoding="utf-8")
    (vfs_root / "wiki/blob.bin").write_bytes(b"\xff\x00")
    service = VfsService(VfsResolver(vfs_root))

    result = service.find("atomic write", under="oks://wiki/", max_results=1)

    assert result["matches"][0]["uri"] == "oks://wiki/a.md"
    assert len(result["matches"][0]["snippet"]) <= 200
    assert result["skipped_count"] == 1
```

- [ ] **Step 2: Run focused traversal tests and verify failure**

```bash
.venv/bin/python -m pytest cli/tests/test_vfs.py -k 'tree or overview or find' -q
```

Expected: FAIL because the three methods are absent.

- [ ] **Step 3: Implement bounded traversal**

Use the exact signatures declared in **Interfaces** and add these complete limit validators before implementing traversal:

```python
def _validate_tree_limits(depth: int, max_entries: int) -> None:
    if not 0 <= depth <= 10 or not 1 <= max_entries <= 10_000:
        raise VfsError("INVALID_ARGUMENT", "depth must be 0..10 and max_entries must be 1..10000")


def _validate_find(query: str, max_results: int) -> str:
    normalized = query.casefold()
    if not normalized or not 1 <= max_results <= 200:
        raise VfsError("INVALID_ARGUMENT", "query must be non-empty and max_results must be 1..200")
    return normalized
```

Implement `tree(uri, *, depth=3, max_entries=1_000)`, `overview(uri)`, and `find(query, *, under, max_results=50)`. `tree()` uses iterative traversal or guarded recursion and never follows symlinks. `overview()` only inspects direct children and returns `directories`, `files`, `counts`, and optional `index_uri`. `find()` treats the normalized query as a literal string, matches relative URI path first and decoded UTF-8 content second, skips unsupported/unreadable regular files, returns snippets bounded to 200 characters, and stops after `max_results` with `truncated=True` if another match exists.

- [ ] **Step 4: Add negative limit/type tests**

Parametrize invalid `depth`, `max_entries`, empty query, out-of-range `max_results`, file target for `tree/overview`, and assert stable codes `INVALID_ARGUMENT` or `NOT_DIRECTORY`.

- [ ] **Step 5: Run all VFS tests**

```bash
.venv/bin/python -m pytest cli/tests/test_vfs.py -q
```

Expected: all pass with no generated files under the temporary knowledge base.

- [ ] **Step 6: Commit Task 3**

```bash
git add cli/knowledge_studio/vfs.py cli/tests/test_vfs.py
git commit -m "feat(vfs): add bounded context traversal"
```

---

### Task 4: `oks fs` CLI and JSON Contract

**Files:**
- Modify: `cli/knowledge_studio/cli.py:101-149`
- Modify: `cli/tests/test_cli.py`
- Test: `cli/tests/test_vfs.py`

**Interfaces:**
- Consumes: all `VfsService` methods and `VfsError` from Tasks 1–3.
- Produces: Typer group `fs_app`, six public commands, `oks-fs-response/v1` success/error envelopes.

- [ ] **Step 1: Write failing CLI contract tests**

Add:

```python
def test_fs_ls_json_contract(tmp_path, monkeypatch):
    from knowledge_studio import cli

    monkeypatch.setenv("OKS_ROOT", str(tmp_path))
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki/page.md").write_text("# Page\n", encoding="utf-8")

    result = CliRunner().invoke(cli.app, ["fs", "ls", "oks://wiki/", "--format", "json"])
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["schema_version"] == "oks-fs-response/v1"
    assert payload["operation"] == "ls"
    assert payload["uri"] == "oks://wiki/"
    assert payload["result"]["entries"][0]["uri"] == "oks://wiki/page.md"


def test_fs_error_json_hides_physical_path(tmp_path, monkeypatch):
    from knowledge_studio import cli

    monkeypatch.setenv("OKS_ROOT", str(tmp_path))
    result = CliRunner().invoke(
        cli.app,
        ["fs", "read", "oks://settings/recall.yaml", "--format", "json"],
    )
    payload = json.loads(result.stdout)

    assert result.exit_code == 2
    assert payload["error"]["code"] == "UNSUPPORTED_SCOPE"
    assert str(tmp_path) not in result.stdout


@pytest.mark.parametrize("command", ["write", "mkdir", "mv", "rm", "cp"])
def test_fs_has_no_mutating_commands(command):
    from knowledge_studio import cli

    result = CliRunner().invoke(cli.app, ["fs", command])
    assert result.exit_code != 0
    assert "No such command" in result.stdout
```

Remember to add `import json` to `test_cli.py`.

- [ ] **Step 2: Run CLI tests and verify failure**

```bash
.venv/bin/python -m pytest cli/tests/test_cli.py -k 'fs_' -q
```

Expected: FAIL with `No such command 'fs'`.

- [ ] **Step 3: Register the Typer group and response helpers**

Near the existing app declarations, add:

```python
fs_app = typer.Typer(help="Read-only virtual context filesystem.", no_args_is_help=True)
app.add_typer(fs_app, name="fs")
```

Import `FS_RESPONSE_SCHEMA`, `VfsError`, and `VfsService`. Add private helpers:

```python
def _vfs_success(operation: str, uri: str, result: dict) -> dict:
    return {
        "schema_version": FS_RESPONSE_SCHEMA,
        "operation": operation,
        "uri": uri,
        "result": result,
    }


def _vfs_error(operation: str, uri: str, exc: VfsError) -> dict:
    return {
        "schema_version": FS_RESPONSE_SCHEMA,
        "operation": operation,
        "uri": uri,
        "error": {"code": exc.code, "message": exc.public_message},
    }
```

Use one `_run_vfs(operation, uri, output_format, call)` wrapper so all six commands render errors and exit codes identically. Input/URI/domain errors exit 2; unexpected `OSError` is converted to `IO_ERROR` and exits 1 without absolute paths.

- [ ] **Step 4: Implement six commands with explicit options**

Implement:

```text
oks fs ls <uri> [--format table|json]
oks fs tree <uri> [--depth 3] [--max-entries 1000] [--format table|json]
oks fs stat <uri> [--format table|json]
oks fs read <uri> [--offset 0] [--limit 20000] [--format table|json]
oks fs overview <uri> [--format table|json]
oks fs find <query> --under <uri> [--max-results 50] [--format table|json]
```

Reuse `_validate_output_format()`. JSON output must call `_emit_json()` exactly once. Text output may use Rich tables/panels, but its content must include canonical URI and explicit truncation notices.

- [ ] **Step 5: Run CLI and VFS tests**

```bash
.venv/bin/python -m pytest cli/tests/test_cli.py cli/tests/test_vfs.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add cli/knowledge_studio/cli.py cli/tests/test_cli.py cli/knowledge_studio/vfs.py cli/tests/test_vfs.py
git commit -m "feat(cli): expose read-only oks fs commands"
```

---

### Task 5: Recall URI Compatibility Extension

**Files:**
- Modify: `cli/knowledge_studio/recall.py:336-440, 530-565, 620-657`
- Modify: `cli/tests/test_recall.py`
- Test: `cli/tests/test_cli.py`

**Interfaces:**
- Consumes: `VfsResolver(root).uri_for_path(path) -> str`.
- Produces: `uri: str` on every normal `recall-hit/v1` knowledge/raw/profile hit.

- [ ] **Step 1: Write failing URI regression tests**

Extend existing fixtures and assertions:

```python
def test_native_knowledge_hit_has_canonical_uri(kb_root):
    from knowledge_studio.recall import recall_knowledge

    hit = next(h for h in recall_knowledge("git branching") if h["slug"] == "git-branching")

    assert hit["uri"] == "oks://wiki/computing/concepts/git-branching.md"
    assert hit["schema_version"] == "recall-hit/v1"


def test_raw_and_authorized_profile_hits_have_uri(kb_root):
    from knowledge_studio.recall import recall_episodic

    raw = kb_root / "raw/2026/08/21/articles/atomic.md"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("atomic write", encoding="utf-8")
    profile = kb_root / "profiles/users/alice/profile.md"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text("atomic write preference", encoding="utf-8")

    hits = recall_episodic("atomic write", user_id="alice", limit=10)
    by_path = {hit["source_path"]: hit for hit in hits}

    assert by_path["raw/2026/08/21/articles/atomic.md"]["uri"] == (
        "oks://raw/2026/08/21/articles/atomic.md"
    )
    assert by_path["profiles/users/alice/profile.md"]["uri"] == (
        "oks://profiles/users/alice/profile.md"
    )
```

Also extend the existing FTS5/fusion backend test to assert its knowledge hit URI, and capture `(slug, relevance, score)` before/after to demonstrate ranking invariance.

- [ ] **Step 2: Run recall tests and verify missing field failure**

```bash
.venv/bin/python -m pytest cli/tests/test_recall.py -k 'canonical_uri or profile_hits_have_uri' -q
```

Expected: FAIL with `KeyError: 'uri'`.

- [ ] **Step 3: Add one shared path-to-URI helper**

In `recall.py`, import `VfsResolver` and add:

```python
def _uri_for_hit_path(root: Path, path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    return VfsResolver(root).uri_for_path(candidate)
```

When constructing raw/profile candidates, populate `uri` beside `source_path`. For native and backend knowledge hits, use `item["file_path"]` / `p["file_path"]`. Do not derive Wiki paths from area/type/slug because physical directories are the source of truth.

Do not catch `VfsError` for normal hit construction: an exposed recall hit without a valid URI violates the new contract and must fail tests instead of silently emitting a guessed path.

- [ ] **Step 4: Run recall and evaluation tests**

```bash
.venv/bin/python -m pytest cli/tests/test_recall.py cli/tests/test_evaluation.py -q
```

Expected: all pass; existing schema versions and score/rank assertions remain unchanged.

- [ ] **Step 5: Run CLI recall JSON compatibility test**

```bash
.venv/bin/python -m pytest cli/tests/test_cli.py cli/tests/test_recall.py -q
```

Expected: all pass; JSON now includes `uri` while existing text rendering is unaffected.

- [ ] **Step 6: Commit Task 5**

```bash
git add cli/knowledge_studio/recall.py cli/tests/test_recall.py
git commit -m "feat(recall): add canonical URI to hits"
```

---

### Task 6: Documentation, Full Verification, and OpenSpec Completion

**Files:**
- Modify: `docs/reference/cli.md:10-33`
- Modify: `docs/concepts/file-system-paradigm.md:10-24`
- Modify: `docs/concepts/architecture.md:179-186`
- Modify: `openspec/changes/add-read-only-vfs/tasks.md`
- Test: complete repository and distribution validation

**Interfaces:**
- Consumes: final CLI syntax and schemas from Tasks 1–5.
- Produces: user-facing documentation, completed OpenSpec checklist, verified wheel/sdist.

- [ ] **Step 1: Update CLI reference with exact commands and limits**

Add `oks fs` to the command table and a section containing the six exact command forms, public scopes, `oks-fs-response/v1`, read/tree/find limits, and the statement that mutation commands intentionally do not exist.

- [ ] **Step 2: Correct the OpenViking L0/L1/L2 claim**

Replace the current direct mapping table in `file-system-paradigm.md` with these facts:

- OpenViking L0/L1 are directory-level derived summaries; L2 is complete content in the same subtree.
- OKS Raw and Wiki are distinct lifecycle objects linked through provenance and human review.
- The current MVP borrows progressive browsing and stable URI ideas but does not claim existing frontmatter/Wiki/Raw are literal L0/L1/L2.

Add the read-only VFS access layer to `architecture.md` without changing the seven-bucket governance model.

- [ ] **Step 3: Run focused verification**

```bash
.venv/bin/python -m pytest cli/tests/test_vfs.py cli/tests/test_cli.py cli/tests/test_recall.py cli/tests/test_evaluation.py -q
openspec validate add-read-only-vfs --strict --no-interactive
git diff --check
```

Expected: all tests pass, OpenSpec reports valid, and `git diff --check` has no output.

- [ ] **Step 4: Run the complete test suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: zero failures. Record the actual pass count; do not copy a historical count into docs.

- [ ] **Step 5: Build and inspect distribution artifacts**

Use a clean temporary output directory so stale artifacts cannot satisfy the check:

```bash
VFS_DIST_DIR="$(mktemp -d /tmp/oks-vfs-dist-XXXXXX)"
.venv/bin/python -m build --outdir "$VFS_DIST_DIR" ./cli
.venv/bin/python cli/scripts/check_dist.py "$VFS_DIST_DIR"
```

Expected: exactly one wheel and one sdist are accepted, and `knowledge_studio/vfs.py` is present because it belongs to the declared `knowledge_studio` package. If `python -m build` is unavailable, install the existing development tool into `.venv`; do not add it to runtime dependencies.

- [ ] **Step 6: Mark OpenSpec tasks complete only from evidence**

Change each completed checkbox in `openspec/changes/add-read-only-vfs/tasks.md` from `[ ]` to `[x]` only after its referenced test/validation has passed. Then run:

```bash
openspec status --change add-read-only-vfs --json
openspec validate add-read-only-vfs --strict --no-interactive
```

Expected: proposal, specs, design and tasks are all complete and the change remains valid.

- [ ] **Step 7: Review the final diff and commit documentation**

```bash
git diff -- docs/reference/cli.md docs/concepts/file-system-paradigm.md docs/concepts/architecture.md openspec/changes/add-read-only-vfs/tasks.md
git add docs/reference/cli.md docs/concepts/file-system-paradigm.md docs/concepts/architecture.md openspec/changes/add-read-only-vfs/tasks.md
git commit -m "docs(vfs): document read-only context filesystem"
```

Expected: the commit includes only the three public docs and OpenSpec checklist. Do not push, create a PR, publish Pages, or archive the OpenSpec change without separate user authorization.

---

## Final Acceptance Checklist

- `oks fs --help` exposes exactly six read-only commands.
- Root listing exposes exactly seven public scopes.
- Traversal, encoded separators, excluded Raw paths and all symlinks fail closed.
- `ls/stat/read/tree/overview/find` pass content snapshot tests with no persistent writes.
- All size/depth/result limits are enforced and report truncation explicitly.
- All normal knowledge/raw/profile recall hits carry canonical URI without score or order changes.
- No schema version, runtime dependency, physical instance layout or governance command changes.
- OpenSpec strict validation, focused tests, full tests, wheel/sdist validation and `git diff --check` all pass.
