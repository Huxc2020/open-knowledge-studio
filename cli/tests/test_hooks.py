"""Tests for optional editor hook installation."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from knowledge_studio.cli import app


runner = CliRunner()


def _init_instance(tmp_path):
    target = tmp_path / "kb"
    result = runner.invoke(
        app,
        ["init", str(target), "--no-git", "--no-set-default"],
    )
    assert result.exit_code == 0, result.output
    return target


def _load_codex_hooks(target):
    return json.loads((target / ".codex" / "hooks.json").read_text(encoding="utf-8"))


def _codex_commands(hooks, event):
    return [
        handler["command"]
        for group in hooks.get("hooks", {}).get(event, [])
        for handler in group.get("hooks", [])
        if handler.get("type") == "command"
    ]


def _run_hook(script, payload, cwd, env_overrides=None):
    env = os.environ.copy()
    env["OKS_ROOT"] = str(cwd)
    if env_overrides:
        env.update(env_overrides)
    cli_root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join(
        [str(cli_root), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    command = ["bash", str(script)] if script.suffix == ".sh" else [sys.executable, str(script)]
    return subprocess.run(
        command,
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=str(cwd),
        env=env,
        check=False,
    )


def test_hook_install_wires_codex_prompt_recall(tmp_path):
    target = _init_instance(tmp_path)

    result = runner.invoke(app, ["hook", "install", "--editor", "codex", "--path", str(target)])

    assert result.exit_code == 0, result.output
    hooks = _load_codex_hooks(target)
    commands = _codex_commands(hooks, "UserPromptSubmit")
    assert len(commands) == 1
    assert commands[0].endswith("/.codex/hooks/user-prompt-recall.sh")
    assert (target / ".codex" / "hooks" / "user-prompt-recall.sh").is_file()
    assert (target / ".codex" / "hooks" / "user-prompt-recall.py").is_file()

    # Existing Codex hooks remain intact.
    assert _codex_commands(hooks, "SessionStart")
    assert _codex_commands(hooks, "PreCompact")

    post_commands = _codex_commands(hooks, "PostToolUse")
    assert len(post_commands) == 1
    assert post_commands[0].endswith("/.codex/hooks/post-tool-edit.sh")
    wrapper = target / ".codex" / "hooks" / "post-tool-edit.sh"
    assert f'${{OKS_PYTHON:-{sys.executable}}}' in wrapper.read_text(encoding="utf-8")
    assert "/hooks" in result.output


def test_hook_install_codex_is_idempotent_and_status_reports_wired(tmp_path):
    target = _init_instance(tmp_path)
    args = ["hook", "install", "--editor", "codex", "--path", str(target)]

    first = runner.invoke(app, args)
    second = runner.invoke(app, args)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    hooks = _load_codex_hooks(target)
    commands = _codex_commands(hooks, "UserPromptSubmit")
    assert len(commands) == 1

    status = runner.invoke(app, ["hook", "status", "--path", str(target)])
    assert status.exit_code == 0, status.output
    assert "codex: wired" in status.output
    assert "codex PostToolUse: wired" in status.output
    assert "codex trust: review with `/hooks`" in status.output


def test_hook_install_migrates_codex_relative_lifecycle_paths(tmp_path):
    target = _init_instance(tmp_path)
    hooks_path = target / ".codex" / "hooks.json"
    hooks = _load_codex_hooks(target)
    legacy_commands = {
        "PreToolUse": "validate-wiki-write.sh",
        "PreCompact": "pre-compact.sh",
        "SessionStart": "session-start.sh",
    }
    for event, script_name in legacy_commands.items():
        hooks["hooks"][event][0]["hooks"][0]["command"] = f".codex/hooks/{script_name}"
    hooks_path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["hook", "install", "--editor", "codex", "--path", str(target)],
    )

    assert result.exit_code == 0, result.output
    migrated = _load_codex_hooks(target)
    for event, script_name in legacy_commands.items():
        command = _codex_commands(migrated, event)[0]
        assert Path(command).resolve() == (target / ".codex" / "hooks" / script_name).resolve()


def test_codex_posttool_apply_patch_records_files_and_emits_json_context(tmp_path):
    target = _init_instance(tmp_path)
    script = target / ".codex" / "hooks" / "post-tool-edit.py"
    edited = target / "wiki" / "architecture.md"
    added = target / "docs" / "codex.md"
    target_records = target / "records" / "file-edits.jsonl"
    target_records.parent.mkdir(parents=True, exist_ok=True)
    target_records.write_text(
        json.dumps(
            {
                "agent_id": "other-agent",
                "file_path": str(edited),
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    patch_command = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: wiki/architecture.md",
            "@@",
            "+updated",
            "*** Add File: docs/codex.md",
            "+new",
            "*** End Patch",
        ]
    )
    result = _run_hook(
        script,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": patch_command},
            "tool_response": {"status": "completed"},
            "session_id": "codex-session",
            "cwd": str(target),
            "agent_id": "codex-agent",
            "model": "gpt-5",
        },
        target,
        {"PYTHONIOENCODING": "cp1252"},
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "文件冲突" in output["hookSpecificOutput"]["additionalContext"]
    records = [
        json.loads(line)
        for line in target_records.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {record["file_path"] for record in records[-2:]} == {
        str(edited),
        str(added),
    }


def test_codex_pretooluse_blocks_invalid_added_wiki_patch(tmp_path):
    target = _init_instance(tmp_path)
    script = target / ".codex" / "hooks" / "validate-wiki-write.sh"
    shell_python = sys.executable.replace("\\", "/")
    baked_python = f'${{OKS_PYTHON:-{shell_python}}}'
    assert baked_python in script.read_text(encoding="utf-8")
    invalid_patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Add File: wiki/invalid.md",
            "+# Missing frontmatter",
            "*** End Patch",
        ]
    )
    blocked = _run_hook(
        script,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": invalid_patch},
            "cwd": str(target),
        },
        target,
    )
    assert blocked.returncode == 2, (
        f"returncode={blocked.returncode}; "
        f"stdout={blocked.stdout.encode('unicode_escape').decode()}; "
        f"stderr={blocked.stderr.encode('unicode_escape').decode()}"
    )
    assert "frontmatter" in blocked.stderr

    valid_patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Add File: wiki/valid.md",
            "+---",
            "+title: Valid",
            "+type: concept",
            "+area: computing",
            "+---",
            "+",
            "+# Valid",
            "*** End Patch",
        ]
    )
    allowed = _run_hook(
        script,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": valid_patch},
            "cwd": str(target),
        },
        target,
    )
    assert allowed.returncode == 0, allowed.stderr

    legacy = _run_hook(
        script,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {
                "file_path": "wiki/legacy.md",
                "content": "# Missing frontmatter",
            },
            "cwd": str(target),
        },
        target,
    )
    assert legacy.returncode == 2
    assert "frontmatter" in legacy.stderr

    existing = target / "wiki" / "updated.md"
    existing.write_text(
        "---\ntitle: Existing\ntype: concept\narea: computing\n---\n\n# Existing\n",
        encoding="utf-8",
    )
    invalid_update = _run_hook(
        script,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "\n".join(
                    [
                        "*** Begin Patch",
                        "*** Update File: wiki/updated.md",
                        "@@",
                        "-title: Existing",
                        "+type: concept",
                        "*** End Patch",
                    ]
                )
            },
            "cwd": str(target),
        },
        target,
    )
    assert invalid_update.returncode == 2
    assert "title" in invalid_update.stderr


def test_codex_precompact_emits_json_system_message_and_saves_snapshot(tmp_path):
    target = _init_instance(tmp_path)
    script = target / ".codex" / "hooks" / "pre-compact.sh"
    shell_python = sys.executable.replace("\\", "/")
    baked_python = f'${{OKS_PYTHON:-{shell_python}}}'
    assert baked_python in script.read_text(encoding="utf-8")
    result = _run_hook(
        script,
        {
            "hook_event_name": "PreCompact",
            "session_id": "codex-session",
            "cwd": str(target),
            "model": "gpt-5",
        },
        target,
    )

    assert result.returncode == 0, (
        f"returncode={result.returncode}; "
        f"stdout={result.stdout.encode('unicode_escape').decode()}; "
        f"stderr={result.stderr.encode('unicode_escape').decode()}"
    )
    output = json.loads(result.stdout)
    assert output["systemMessage"].startswith("Snapshot saved:")
    assert list((target / ".oks" / "snapshots").glob("pre-compact-*.md"))

    legacy = _run_hook(
        script,
        {
            "hook_event_name": "PreCompact",
            "session_id": "claude-session",
            "cwd": str(target),
        },
        target,
    )
    assert legacy.returncode == 0, legacy.stderr
    assert legacy.stdout.startswith("Snapshot saved:")


def test_non_codex_posttool_keeps_plain_text_conflict_output(tmp_path):
    target = _init_instance(tmp_path)
    script = target / ".codex" / "hooks" / "post-tool-edit.py"
    edited = target / "wiki" / "legacy.md"
    records = target / "records" / "file-edits.jsonl"
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text(
        json.dumps(
            {
                "agent_id": "other-agent",
                "file_path": str(edited),
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _run_hook(
        script,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(edited)},
            "session_id": "claude-session",
            "cwd": str(target),
            "agent_id": "claude-agent",
        },
        target,
        {"PYTHONIOENCODING": "cp1252"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("[oks] 文件冲突:")
    assert not result.stdout.startswith("{")
