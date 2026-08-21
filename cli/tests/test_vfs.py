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
