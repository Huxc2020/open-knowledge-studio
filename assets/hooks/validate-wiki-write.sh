#!/usr/bin/env bash
# validate-wiki-write.sh — Validate frontmatter before writing to wiki/.
# Triggered by PreToolUse for Claude/Qoder writes and Codex apply_patch.
# Blocks writes to wiki/ that lack required frontmatter fields.

set -euo pipefail

input=$(cat)

if printf '%s' "$input" | python3 -c '
import json
import re
import sys
from pathlib import Path


def fail(message):
    print("BLOCKED: " + message, file=sys.stderr)
    return False


def resolve_path(raw, cwd):
    path = Path(str(raw or "").strip())
    if not path.is_absolute():
        path = Path(cwd) / path
    return path


def is_wiki_path(raw):
    normalized = str(raw or "").replace("\\", "/").lstrip("./")
    return normalized == "wiki" or normalized.startswith("wiki/") or "/wiki/" in normalized


def valid_frontmatter(content, label):
    lines = str(content or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return fail(f"{label} must start with YAML frontmatter (---)")
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        return fail(f"{label} frontmatter is not closed (---)")
    frontmatter = "\n".join(lines[1:end])
    for field in ("title", "type", "area"):
        if not re.search(r"(?m)^" + re.escape(field) + r"\s*:", frontmatter):
            return fail(f"wiki/ frontmatter missing required field: {field}:")
    return True


def validate_update_delta(content, entry, label):
    lines = str(content or "").splitlines()
    try:
        end = lines[1:].index("---") + 1
    except (IndexError, ValueError):
        return fail(f"{label} frontmatter is not closed (---)")
    counts = {}
    for field in ("title", "type", "area"):
        counts[field] = sum(
            1 for line in lines[1:end]
            if re.match(r"^" + re.escape(field) + r"\s*:", line)
        )
    delimiters = 2
    for line in entry["removed"]:
        if line.strip() == "---":
            delimiters -= 1
        for field in counts:
            if re.match(r"^" + re.escape(field) + r"\s*:", line):
                counts[field] -= 1
    for line in entry["added"]:
        if line.strip() == "---":
            delimiters += 1
        for field in counts:
            if re.match(r"^" + re.escape(field) + r"\s*:", line):
                counts[field] += 1
    if delimiters < 2:
        return fail(f"{label} frontmatter delimiters would be incomplete")
    for field, count in counts.items():
        if count < 1:
            return fail(f"wiki/ frontmatter missing required field: {field}:")
    return True


def validate_codex_patch(command, cwd):
    header = re.compile(r"^\*\*\*\s+(Add|Update|Delete) File:\s*(.+?)\s*$")
    move = re.compile(r"^\*\*\*\s+Move to:\s*(.+?)\s*$")
    entries = []
    current = None
    for line in str(command or "").splitlines():
        match = header.match(line)
        if match:
            current = {
                "op": match.group(1),
                "path": match.group(2),
                "added": [],
                "removed": [],
            }
            entries.append(current)
            continue
        move_match = move.match(line)
        if move_match and current is not None:
            current["path"] = move_match.group(1)
            continue
        if current is not None and line.startswith("+") and not line.startswith("+++"):
            current["added"].append(line[1:])
        elif current is not None and line.startswith("-") and line != "---":
            current["removed"].append(line[1:])

    for entry in entries:
        raw_path = entry["path"]
        if not is_wiki_path(raw_path) or entry["op"] == "Delete":
            continue
        path = resolve_path(raw_path, cwd)
        if entry["op"] == "Add":
            content = "\n".join(entry["added"])
        else:
            if not path.is_file():
                return fail(f"cannot validate existing Wiki file before patch: {raw_path}")
            content = path.read_text(encoding="utf-8")
        if not valid_frontmatter(content, raw_path):
            return False
        if entry["op"] == "Update" and not validate_update_delta(content, entry, raw_path):
            return False
    return True


try:
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        sys.exit(0)
    params = data.get("tool_input", data)
    if not isinstance(params, dict):
        sys.exit(0)
    cwd = str(data.get("cwd", "") or "") or str(Path.cwd())
    tool_name = str(data.get("tool_name", "") or "")
    command = str(params.get("command", "") or "")

    if tool_name == "apply_patch" or command:
        if not validate_codex_patch(command, cwd):
            sys.exit(2)
        sys.exit(0)

    file_path = str(params.get("file_path", params.get("path", "")) or "")
    if not is_wiki_path(file_path):
        sys.exit(0)
    content = str(params.get("content", "") or "")
    if not content:
        content = str(params.get("new_string", "") or "")
        existing = resolve_path(file_path, cwd)
        if existing.is_file():
            content = existing.read_text(encoding="utf-8")
    if not valid_frontmatter(content, file_path):
        sys.exit(2)
except Exception as exc:
    print(f"BLOCKED: unable to validate Wiki write: {exc}", file=sys.stderr)
    sys.exit(2)
'; then
    exit 0
else
    exit $?
fi
