from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
            raise VfsError(
                "INVALID_URI",
                "URI must use oks:// without credentials, port, query, or fragment",
            )
        scope = parsed.netloc
        if scope not in MOUNTS:
            raise VfsError("UNSUPPORTED_SCOPE", f"Unsupported OKS scope: {scope}")
        path_body = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
        if "//" in parsed.path:
            raise VfsError("INVALID_URI", "Empty URI path segments are not allowed")
        if path_body.endswith("/"):
            path_body = path_body[:-1]
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
        try:
            raw_relative = candidate.relative_to(self.root.absolute())
        except ValueError:
            raise VfsError("PATH_NOT_EXPOSED", "Physical path is outside public OKS scopes")
        for part in raw_relative.parts:
            if part in {"", ".", ".."} or "\x00" in part or "\\" in part:
                raise VfsError("PATH_NOT_EXPOSED", "Physical path contains unsafe segments")
        self._reject_symlinks(self.root, raw_relative.parts)

        candidate = candidate.resolve(strict=False)
        ordered = [MOUNTS["traces"]] + [
            mount for scope, mount in MOUNTS.items() if scope != "traces"
        ]
        for mount in ordered:
            mount_root = self.root.joinpath(*mount.relative_root).resolve(strict=False)
            try:
                relative = candidate.relative_to(mount_root)
            except ValueError:
                continue
            parts = relative.parts
            if any(
                part in {"", ".", ".."} or "\x00" in part or "\\" in part
                for part in parts
            ):
                raise VfsError("PATH_NOT_EXPOSED", "Physical path contains unsafe segments")
            if any(parts[: len(prefix)] == prefix for prefix in mount.excluded_prefixes):
                continue
            self._reject_symlinks(self.root, mount.relative_root + parts)
            return OksUri(mount.scope, tuple(parts)).render()
        raise VfsError("PATH_NOT_EXPOSED", "Physical path is outside public OKS scopes")


def _node_type(path: Path) -> str:
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "special"


def _validate_slice(offset: int, limit: int) -> None:
    if offset < 0 or not 1 <= limit <= 1_000_000:
        raise VfsError(
            "INVALID_ARGUMENT",
            "offset must be non-negative and limit must be 1..1000000",
        )


class VfsService:
    def __init__(self, resolver: VfsResolver | None = None):
        self.resolver = resolver or VfsResolver()

    def _entry(self, path: Path) -> dict[str, str]:
        uri = self.resolver.uri_for_path(path)
        node_type = _node_type(path)
        if node_type == "directory" and not uri.endswith("/"):
            uri += "/"
        return {"name": path.name, "type": node_type, "uri": uri}

    def ls(self, uri: str) -> dict[str, object]:
        node = self.resolver.resolve(uri)
        if node.synthetic_root:
            return {
                "uri": node.uri.render(),
                "entries": [
                    {
                        "name": scope,
                        "type": "directory",
                        "uri": f"oks://{scope}/",
                    }
                    for scope in MOUNTS
                ],
            }
        assert node.path is not None and node.mount is not None
        if not node.path.is_dir():
            raise VfsError("NOT_DIRECTORY", f"Not a directory: {node.uri.render()}")

        mount_root = self.resolver.root.joinpath(*node.mount.relative_root)
        entries: list[dict[str, str]] = []
        for child in sorted(
            node.path.iterdir(), key=lambda path: (path.name.casefold(), path.name)
        ):
            relative = child.relative_to(mount_root).parts
            if any(
                relative[: len(prefix)] == prefix
                for prefix in node.mount.excluded_prefixes
            ):
                continue
            entries.append(self._entry(child))
        return {"uri": node.uri.render(), "entries": entries}

    def stat(self, uri: str) -> dict[str, object]:
        node = self.resolver.resolve(uri)
        if node.synthetic_root:
            return {
                "uri": node.uri.render(),
                "type": "directory",
                "size": None,
                "modified_at": None,
                "mount": None,
            }
        assert node.path is not None and node.mount is not None
        node_type = _node_type(node.path)
        metadata = node.path.stat()
        canonical_uri = self.resolver.uri_for_path(node.path)
        if node_type == "directory" and not canonical_uri.endswith("/"):
            canonical_uri += "/"
        return {
            "uri": canonical_uri,
            "type": node_type,
            "size": metadata.st_size if node_type != "directory" else None,
            "modified_at": datetime.fromtimestamp(
                metadata.st_mtime, tz=timezone.utc
            ).isoformat(),
            "mount": node.mount.scope,
        }

    def read(
        self, uri: str, *, offset: int = 0, limit: int = 20_000
    ) -> dict[str, object]:
        _validate_slice(offset, limit)
        node = self.resolver.resolve(uri)
        if node.synthetic_root or node.path is None or not node.path.is_file():
            raise VfsError(
                "UNSUPPORTED_CONTENT", f"Not a readable file: {node.uri.render()}"
            )
        try:
            content = node.path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            raise VfsError(
                "UNSUPPORTED_CONTENT", f"File is not readable UTF-8: {node.uri.render()}"
            ) from exc
        if "\x00" in content:
            raise VfsError(
                "UNSUPPORTED_CONTENT",
                f"File is not readable UTF-8 text: {node.uri.render()}",
            )

        page = content[offset : offset + limit]
        next_offset = offset + len(page)
        truncated = next_offset < len(content)
        return {
            "uri": self.resolver.uri_for_path(node.path),
            "content": page,
            "offset": offset,
            "returned_chars": len(page),
            "total_chars": len(content),
            "truncated": truncated,
            "next_offset": next_offset if truncated else None,
        }
