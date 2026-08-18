# Codex Hook Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Codex integration up to the current Claude Code/Qoder OKS hook behavior without changing the shared recall model.

**Architecture:** Keep the existing shared hook scripts, but normalize Codex lifecycle payloads at the hook boundary. Codex `apply_patch` file paths are extracted from `tool_input.command`; Codex model-visible hook output is emitted as event-specific JSON while Claude Code/Qoder retain their existing plain-text output. Project-local Codex commands resolve from the repository root, and installation/status make the required trust review explicit.

**Tech Stack:** Python 3, Bash, Typer, pytest, JSON hook payloads.

**Spec:** The approved Codex parity design in the preceding task discussion.

## Global Constraints

- Preserve existing Claude Code and Qoder behavior.
- Hooks remain fail-open for post-tool recall/conflict recording; Wiki validation may block invalid writes.
- Do not modify unrelated user-owned files under `benchmarks/` or existing benchmark plans.
- Keep `assets/` as the source of truth; generated `cli/knowledge_studio/_assets/` remains a build artifact.

---

### Task 1: Add failing Codex contract tests

**Files:**
- Modify: `cli/tests/test_hooks.py`

**Interfaces:**
- Tests exercise the real `post-tool-edit.py` and `validate-wiki-write.sh` hook boundaries with Codex-shaped stdin payloads.
- Tests assert install wiring, side effects, exit codes, and JSON output rather than source text.

- [x] **Step 1: Write tests for Codex PostToolUse wiring, apply_patch file extraction, JSON context output, and Wiki patch validation.**
- [x] **Step 2: Run the targeted tests and confirm they fail because Codex PostToolUse is skipped and the existing validator does not understand `tool_input.command`.**

### Task 2: Implement Codex PostToolUse compatibility

**Files:**
- Modify: `assets/hooks/post-tool-edit.py`
- Modify: `assets/hooks/post-tool-edit.sh`
- Modify: `cli/knowledge_studio/cli.py`

**Interfaces:**
- `post-tool-edit.py` accepts both Claude/Qoder and Codex payloads.
- Codex `apply_patch` paths are normalized to absolute paths relative to payload `cwd`.
- Codex output is a `PostToolUse` JSON object with `hookSpecificOutput.additionalContext`.

- [x] **Step 1: Add the smallest parser/output changes to make the new tests pass.**
- [x] **Step 2: Wire Codex `PostToolUse` idempotently and expose it in status/install output.**
- [x] **Step 3: Replace the non-portable Python fallback in the post-tool wrapper.**

### Task 3: Implement Codex Wiki patch validation and lifecycle path compatibility

**Files:**
- Modify: `assets/hooks/validate-wiki-write.sh`
- Modify: `assets/hooks/pre-compact.sh`
- Modify: `assets/agent-config/codex/hooks.json`

**Interfaces:**
- Codex `PreToolUse` validates added/updated Wiki content from `apply_patch` payloads and exits 2 with a clear stderr reason when invalid.
- Codex `PreCompact` keeps writing snapshots and emits JSON `systemMessage` instead of plain stdout.
- Repo-local Codex commands resolve via `git rev-parse --show-toplevel`.

- [x] **Step 1: Implement the payload-aware validator and Codex compact output.**
- [x] **Step 2: Run targeted hook tests and preserve Claude/Qoder plain-text behavior.**

### Task 4: Document and verify the integration

**Files:**
- Modify: `docs/usage/context-injection.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Documentation states Codex has prompt recall, PostToolUse recall/conflict detection, Wiki validation, and the `/hooks` trust step.

- [x] **Step 1: Update the Codex workflow and troubleshooting documentation.**
- [x] **Step 2: Run `git diff --check`, targeted hook tests, the full pytest suite, and inspect the final diff/status.**
