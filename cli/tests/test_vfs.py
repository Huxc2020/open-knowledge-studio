import hashlib
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

    uri = OksUri.parse(
        "oks://wiki/computing/concepts/%E4%B8%AD%E6%96%87%20%E9%A1%B5%E9%9D%A2.md"
    )

    assert uri.scope == "wiki"
    assert uri.parts == ("computing", "concepts", "中文 页面.md")
    assert (
        uri.render()
        == "oks://wiki/computing/concepts/%E4%B8%AD%E6%96%87%20%E9%A1%B5%E9%9D%A2.md"
    )


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
        ("oks://wiki//", "INVALID_URI"),
    ],
)
def test_invalid_uri_is_rejected(uri, code):
    from knowledge_studio.vfs import OksUri, VfsError

    with pytest.raises(VfsError) as exc:
        OksUri.parse(uri)
    assert exc.value.code == code


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


@pytest.mark.parametrize(
    "path",
    [
        Path("wiki") / ".." / "settings" / "recall.yaml",
        Path("wiki") / "unsafe\\name.md",
    ],
)
def test_uri_for_path_rejects_noncanonical_or_unsafe_paths(vfs_root, path):
    from knowledge_studio.vfs import VfsError, VfsResolver

    with pytest.raises(VfsError) as exc:
        VfsResolver(vfs_root).uri_for_path(path)
    assert exc.value.code == "PATH_NOT_EXPOSED"


def test_uri_for_path_rejects_external_symlink_into_public_mount(vfs_root, tmp_path):
    from knowledge_studio.vfs import VfsError, VfsResolver

    external_link = tmp_path.parent / f"{tmp_path.name}-outside-link.md"
    external_link.symlink_to(vfs_root / "wiki/computing/concepts/中文 页面.md")

    with pytest.raises(VfsError) as exc:
        VfsResolver(vfs_root).uri_for_path(external_link)
    assert exc.value.code == "PATH_NOT_EXPOSED"


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
    assert stat["modified_at"].endswith("+00:00")
    assert stat["mount"] == "profiles"


def test_root_ls_is_synthetic_and_physical_ls_hides_exclusions(vfs_root):
    from knowledge_studio.vfs import MOUNTS, VfsResolver, VfsService

    service = VfsService(VfsResolver(vfs_root))

    assert service.ls("oks://")["entries"] == [
        {"name": scope, "type": "directory", "uri": f"oks://{scope}/"}
        for scope in MOUNTS
    ]
    assert service.ls("oks://raw/")["entries"] == []


def test_ls_rejects_files_and_symlink_children(vfs_root):
    from knowledge_studio.vfs import VfsError, VfsResolver, VfsService

    service = VfsService(VfsResolver(vfs_root))
    with pytest.raises(VfsError) as exc:
        service.ls("oks://profiles/team.md")
    assert exc.value.code == "NOT_DIRECTORY"

    (vfs_root / "wiki/link.md").symlink_to(vfs_root / "profiles/team.md")
    with pytest.raises(VfsError) as exc:
        service.ls("oks://wiki/")
    assert exc.value.code == "SYMLINK_NOT_ALLOWED"


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


@pytest.mark.parametrize("payload", [b"\xff\x00", b"\x00\x01"])
def test_read_rejects_binary(vfs_root, payload):
    from knowledge_studio.vfs import VfsError, VfsResolver, VfsService

    binary = vfs_root / "raw/blob.bin"
    binary.write_bytes(payload)
    with pytest.raises(VfsError) as exc:
        VfsService(VfsResolver(vfs_root)).read("oks://raw/blob.bin")
    assert exc.value.code == "UNSUPPORTED_CONTENT"


@pytest.mark.parametrize(
    ("offset", "limit"),
    [(-1, 1), (0, 0), (0, 1_000_001)],
)
def test_read_validates_pagination(vfs_root, offset, limit):
    from knowledge_studio.vfs import VfsError, VfsResolver, VfsService

    with pytest.raises(VfsError) as exc:
        VfsService(VfsResolver(vfs_root)).read(
            "oks://profiles/team.md", offset=offset, limit=limit
        )
    assert exc.value.code == "INVALID_ARGUMENT"


def _file_snapshot(root: Path) -> dict[str, str]:
    snapshot = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        snapshot[path.relative_to(root).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return snapshot


def test_basic_vfs_operations_do_not_change_files(vfs_root):
    from knowledge_studio.vfs import VfsResolver, VfsService

    service = VfsService(VfsResolver(vfs_root))
    before = _file_snapshot(vfs_root)

    service.ls("oks://wiki/computing/concepts/")
    service.stat("oks://profiles/team.md")
    service.read("oks://profiles/team.md")

    assert _file_snapshot(vfs_root) == before
