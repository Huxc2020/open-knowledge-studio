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
            raise VfsError(
                "INVALID_URI",
                "URI must use oks:// without credentials, port, query, or fragment",
            )
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
