/**
 * oks-posttool-recall — pi extension for PostToolUse recall supplement.
 *
 * Subscribes to `tool_result` (fires after tool execution, before the result
 * message is emitted — pi's PostToolUse equivalent). Derives a query from
 * the tool operation (no user prompt in long autonomous tasks), runs the OKS
 * post-tool-edit.py hook (recall + cooldown + inject trace), and appends the
 * `<recalled-memory>` block to the tool result so the LLM sees relevant wiki
 * memory after each tool call.
 *
 * Solves the long-task blind spot: UserPromptSubmit only fires when the user
 * speaks; a long autonomous task (Read → Edit → Bash → ...) has no new user
 * prompts, so recall never injects. This extension fills that gap.
 *
 * Delay optimization: reads recall-state-{session}.json in Node first to
 * check cooldown — if the likely slugs are all in cooldown, skip the Python
 * subprocess entirely (0ms instead of ~560ms Python startup).
 *
 * Prerequisite: `oks hook install` run in this project (post-tool-edit.py
 * exists). Tunables (same env as the .py hook):
 *   OKS_POSTTOOL_FLOOR  min relevance (default 0.9, higher than UserPromptSubmit)
 *   OKS_POSTTOOL_TOPN   max memories (default 2)
 *   OKS_RECALL_COOLDOWN shared with UserPromptSubmit (default 10)
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join, basename } from "node:path";
import { homedir } from "node:os";

const WATCHED = new Set(["edit", "write", "read", "bash", "grep", "glob", "multiedit"]);
const COOLDOWN = parseInt(process.env.OKS_RECALL_COOLDOWN ?? "10", 10);

function queryFromTool(toolName: string, input: any): string {
  // Edit/Write/Read → file_path stem
  for (const k of ["file_path", "path"]) {
    const fp = input?.[k];
    if (fp) {
      const stem = String(fp).split("/").pop()?.replace(/\.\w+$/, "");
      if (stem) return stem;
    }
  }
  // Bash → command first ~6 meaningful words
  const cmd = input?.command;
  if (cmd) {
    const words = String(cmd)
      .split(/\s+/)
      .filter(
        (w) =>
          w &&
          !w.startsWith("-") &&
          !w.startsWith("~") &&
          !w.includes("/") &&
          !["&&", "||", "|", "sudo", "cd", ";", "python", "python3", "bash", "sh"].includes(w)
      );
    return words.slice(0, 6).join(" ");
  }
  // Grep/Glob → pattern
  const pat = input?.pattern || input?.query;
  if (pat) return String(pat);
  return "";
}

function loadSeen(sessionId: string): { n: number; seen: Record<string, number> } {
  try {
    const safe = sessionId.replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 80) || "default";
    const p = join(process.cwd(), ".oks", `recall-state-${safe}.json`);
    if (!existsSync(p)) return { n: 0, seen: {} };
    const s = JSON.parse(readFileSync(p, "utf-8"));
    return { n: Number(s.n ?? 0), seen: (s.seen ?? {}) as Record<string, number> };
  } catch {
    return { n: 0, seen: {} };
  }
}

function _kbRoot(): string | null {
  // Resolution order: OKS_ROOT env → ~/.oks/config.json knowledge_base_path → cwd
  // (mirrors post-tool-edit.py _kb_root, so the extension works even when
  // pi's cwd is the dev repo, not the KB instance).
  const env = process.env.OKS_ROOT;
  if (env && existsSync(env) && existsSync(join(env, "wiki"))) return env;
  try {
    const cfg = join(homedir(), ".oks", "config.json");
    if (existsSync(cfg)) {
      const { knowledge_base_path } = JSON.parse(readFileSync(cfg, "utf-8"));
      if (
        knowledge_base_path &&
        existsSync(knowledge_base_path) &&
        existsSync(join(knowledge_base_path, "wiki"))
      ) {
        return knowledge_base_path;
      }
    }
  } catch {
    // ignore config read errors
  }
  const cwd = process.cwd();
  if (existsSync(join(cwd, "wiki"))) return cwd;
  return null;
}

// query-level cooldown: same tool-derived query within COOLDOWN turns
// skips Python subprocess entirely (0ms vs ~560ms startup). The .py hook
// does authoritative slug-level cooldown; this is a coarser pre-filter.
const queryCache = new Map<string, number>();
let turnCounter = 0;

export default function (pi: ExtensionAPI) {
  pi.on("tool_result", async (event, _ctx) => {
    const tn = (event.toolName || "").toLowerCase();
    if (!WATCHED.has(tn)) return;

    const query = queryFromTool(event.toolName, (event as any).input ?? {});
    if (!query || query.length < 3) return;

    // ── Delay optimization: query-level cooldown in Node ──
    // Same tool-derived query within COOLDOWN turns skips the Python
    // subprocess entirely (0ms instead of ~560ms Python startup). The .py
    // hook does the authoritative slug-level cooldown; this is a coarser
    // query-level pre-filter to avoid spawning Python for repeated queries.
    turnCounter += 1;
    const last = queryCache.get(query);
    if (last !== undefined && turnCounter - last < COOLDOWN) {
      return; // skip Python — 0ms
    }
    queryCache.set(query, turnCounter);

    // OKS_POSTTOOL_RECALL=0 disables recall supplement (keeps conflict detection
    // in post-tool-edit.py). This lets you switch to AI-driven recall mode: the
    // agent decides when to call `oks recall` instead of being force-injected.
    // Fallback: marker file .pi/oks-posttool-recall.disabled (no env needed).
    if (process.env.OKS_POSTTOOL_RECALL === "0") return;
    if (existsSync(join(process.cwd(), ".pi", "oks-posttool-recall.disabled"))) return;

    const kbRoot = _kbRoot();
    if (!kbRoot) return; // no KB instance found — skip silently
    const script = join(kbRoot, ".claude/hooks/post-tool-edit.py");
    if (!existsSync(script)) return;

    const ctxAny = _ctx as unknown as {
      sessionManager?: { getSessionId?: () => string };
    };
    const sessionId =
      ctxAny.sessionManager?.getSessionId?.() ?? "pi-default";

    const payload = JSON.stringify({
      tool_name: event.toolName,
      tool_input: (event as any).input ?? {},
      session_id: sessionId,
      cwd: kbRoot,
    });

    try {
      const out = execFileSync("python3", [script], {
        input: payload,
        encoding: "utf-8",
        timeout: 8000,
      }).trim();
      if (!out) return; // cooldown skip or no hit above floor — 0 bytes added

      // Append recall memory to tool result content so the LLM sees it.
      // event.content may be string | array of {type,text} | array of strings.
      const orig = Array.isArray((event as any).content)
        ? (event as any).content
            .map((c: any) => (typeof c === "string" ? c : c?.text ?? ""))
            .join("\n")
        : String((event as any).content ?? "");
      return {
        content: [{ type: "text" as const, text: `${orig}\n\n${out}` }],
      };
    } catch {
      return; // fail open — never block a tool result
    }
  });
}
