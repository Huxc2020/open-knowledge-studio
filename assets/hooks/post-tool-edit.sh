#!/usr/bin/env bash
# post-tool-edit.sh — PostToolUse wrapper.
# Passes stdin (editor JSON payload) through to the Python hook, drops stderr.
# Fails open (exit 0) so a tool is never blocked.
# OKS_PYTHON is baked in by `oks hook install` to point at the interpreter
# that can import knowledge_studio (pipx/venv safe); falls back to python3.
exec "${OKS_PYTHON:-python3}" "$(dirname "$0")/post-tool-edit.py" 2>/dev/null
