#!/usr/bin/env bash
# pre-compact.sh — Snapshot wiki/ and drafts/ before context compaction.
# Triggered by PreCompact hook.

set -euo pipefail

OKS_PYTHON_CMD="${OKS_PYTHON:-python3}"
if [ -z "${OKS_PYTHON:-}" ] && [ "$OKS_PYTHON_CMD" = "python3" ]; then
    case "$(uname -s 2>/dev/null || true)" in
        MINGW*|MSYS*|CYGWIN*) OKS_PYTHON_CMD="python" ;;
    esac
fi
if ! "$OKS_PYTHON_CMD" -c 'import sys' >/dev/null 2>&1; then
    if [ -z "${OKS_PYTHON:-}" ] && [ "$OKS_PYTHON_CMD" != "python" ] && python -c 'import sys' >/dev/null 2>&1; then
        OKS_PYTHON_CMD="python"
    else
        OKS_PYTHON_CMD=""
    fi
fi

HOOK_INPUT=$(cat)
HOOK_IS_CODEX=""
if [ -n "$OKS_PYTHON_CMD" ]; then
    HOOK_IS_CODEX=$(printf '%s' "$HOOK_INPUT" | "$OKS_PYTHON_CMD" -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print("1" if data.get("hook_event_name") == "PreCompact" and data.get("model") else "")
except Exception:
    print("")
' 2>/dev/null || true)
fi

REPO_ROOT="${OKS_ROOT:-}"
if [ -z "$REPO_ROOT" ] && [ -n "$OKS_PYTHON_CMD" ]; then
    REPO_ROOT="$("$OKS_PYTHON_CMD" -c "import json,os;print(json.load(open(os.path.expanduser('~/.oks/config.json'))).get('knowledge_base_path',''))" 2>/dev/null || true)"
fi
if [ -z "$REPO_ROOT" ]; then
    REPO_ROOT="$(pwd)"
fi

# Only snapshot inside a real knowledge base — never litter other dirs.
[ -d "$REPO_ROOT/wiki" ] || exit 0

SNAPSHOT_DIR="$REPO_ROOT/.oks/snapshots"
mkdir -p "$SNAPSHOT_DIR"

TIMESTAMP=$(date -u +"%Y%m%d-%H%M%S")
SNAPSHOT_FILE="$SNAPSHOT_DIR/pre-compact-$TIMESTAMP.md"

WIKI_COUNT=0
DRAFT_COUNT=0

if [ -d "$REPO_ROOT/wiki" ]; then
    WIKI_COUNT=$(find "$REPO_ROOT/wiki" -name "*.md" -not -name "INDEX.md" | wc -l | tr -d ' ')
fi

if [ -d "$REPO_ROOT/drafts" ]; then
    DRAFT_COUNT=$(find "$REPO_ROOT/drafts" -name "*.md" | wc -l | tr -d ' ')
fi

cat > "$SNAPSHOT_FILE" << EOF
# Pre-Compact Snapshot — $TIMESTAMP

## Knowledge Base State

- Wiki pages: $WIKI_COUNT
- Drafts: $DRAFT_COUNT

## Status

\`\`\`
$(oks status 2>/dev/null || echo "(oks not available)")
\`\`\`
EOF

if [ "$HOOK_IS_CODEX" = "1" ]; then
    "$OKS_PYTHON_CMD" - "$SNAPSHOT_FILE" <<'PY'
import json
import sys

print(json.dumps({"systemMessage": f"Snapshot saved: {sys.argv[1]}"}, ensure_ascii=True))
PY
else
    echo "Snapshot saved: $SNAPSHOT_FILE"
fi
exit 0
