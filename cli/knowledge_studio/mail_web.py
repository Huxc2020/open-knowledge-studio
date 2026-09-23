"""Packaged loopback Mail workspace; all persistence uses Mail Core."""
import json
import hashlib
import re
from datetime import date, datetime, time, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from knowledge_studio import identity, mail, store
from knowledge_studio.mail_activity import activity_data, delivery_records
from knowledge_studio.mail_setup import asset_root, validate_root, agent_id
from knowledge_studio import team_sync

ROOT = asset_root() / "mail-web"
FILES = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"), "/app.js": ("app.js", "text/javascript"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
FILES["/layout.css"] = ("layout.css", "text/css")
UI_AGENT = "human"


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def visible_message(root, message_id):
    return next(
        (item for item in mail.iter_messages(root, UI_AGENT)
         if str(item["meta"].get("message_id", "")) == str(message_id)),
        None,
    )


def reply_recipients(root, thread_id):
    messages = mail.thread_messages(root, thread_id, UI_AGENT)
    if not messages:
        raise ValueError("thread not found")
    latest = messages[-1]
    recipients = sorted(
        {
            value for value in ({str(latest["meta"].get("from", ""))} | set(latest["meta"].get("to", [])))
            if value not in {"@human", "@all", "human", "all", ""}
        }
    )
    if not recipients:
        raise ValueError("thread has no reply recipient")
    return recipients, latest


def _mail_verification(root):
    """Derive Mail verification only from an observed lifecycle, never a heartbeat.

    Evidence is KB-wide: an Agent proves itself by acknowledging one delivery
    or by authoring a reply inside a Thread, whether or not the human can see
    that Thread.  Presence still never implies online or executable.
    """
    messages = list(mail.iter_messages(root))
    deliveries = delivery_records(root, messages)
    verified = {}
    for row in messages:
        meta = row["meta"]
        message_id = str(meta.get("message_id", ""))
        sender = mail._normalise_agent(str(meta.get("from", "")))
        # A reply inside a Thread is lifecycle evidence for its author.
        if str(meta.get("reply_to", "") or "") and sender not in {"@human", "@unknown"}:
            verified.setdefault(sender, {"message_id": message_id, "evidence": "reply"})
        for recipient in meta.get("to", []):
            agent = mail._normalise_agent(str(recipient))
            if agent in {"@human", "@all", "@unknown"}:
                continue
            recipient_delivery = next((item for item in deliveries.get(message_id, []) if item.get("agent_id") == agent), None)
            sessions = recipient_delivery.get("sessions", []) if recipient_delivery else []
            ack = next((session for session in sessions if session.get("acknowledged_at")), None)
            if ack:
                verified[agent] = {"message_id": message_id, "session_id": ack.get("session_id"), "evidence": "acknowledged"}
    return verified


def connection_status(root):
    """Return observed provenance plus explicit Mail lifecycle verification."""
    sessions = []
    agents = {}
    machines = set()
    verification = _mail_verification(root)
    directory = mail.sessions_dir(root)
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict):
                continue
            agent = mail._normalise_agent(str(record.get("agent_id", "@unknown")))
            session_id = str(record.get("session_id", path.stem))
            machine = str(record.get("machine_id", "unknown") or "unknown")
            last_seen = str(record.get("last_seen_at", "") or "")
            item = {
                "session_id": session_id,
                "agent_id": agent,
                "machine_id": machine,
                "verification_status": "verified" if agent in verification else "observed",
                "last_observed_at": last_seen,
                "scope": str(record.get("scope", "") or ""),
            }
            if agent in verification:
                item["verification_evidence"] = verification[agent]
            sessions.append(item)
            # A Session with no machine on record contributes no machine. The
            # provenance loop below already filters it; counting the literal
            # "unknown" here inflated machine_count with a non-machine.
            if machine != "unknown":
                machines.add(machine)
            if agent != "@human":
                summary = agents.setdefault(agent, {
                    "agent_id": agent,
                    "session_count": 0,
                    "machine_ids": set(),
                    "last_observed_at": "",
                })
                summary["session_count"] += 1
                summary["machine_ids"].add(machine)
                if last_seen > summary["last_observed_at"]:
                    summary["last_observed_at"] = last_seen

    # Message provenance can show a machine even when its Session record has
    # expired or was never registered on this clone.
    for message in mail.iter_messages(root, UI_AGENT):
        sender = mail._normalise_agent(str(message["meta"].get("from", "")))
        if sender not in {"@human", "@unknown"}:
            summary = agents.setdefault(sender, {
                "agent_id": sender,
                "session_count": 0,
                "machine_ids": set(),
                "last_observed_at": "",
            })
            timestamp = str(message["meta"].get("timestamp", "") or "")
            if timestamp > summary["last_observed_at"]:
                summary["last_observed_at"] = timestamp
        machine = str(message["meta"].get("origin_machine_id", "") or "")
        if machine and machine != "unknown":
            machines.add(machine)

    serialised_agents = []
    for summary in sorted(agents.values(), key=lambda item: item["agent_id"]):
        summary = dict(summary)
        summary["machine_ids"] = sorted(summary["machine_ids"])
        summary["verification_status"] = "verified" if summary["agent_id"] in verification else "observed"
        if summary["agent_id"] in verification:
            summary["verification_evidence"] = verification[summary["agent_id"]]
        serialised_agents.append(summary)
    return {
        "schema": "mail.connection-status.v1",
        "knowledge_base": str(root),
        "current_machine_id": identity.normalise_machine_id(),
        "sessions": sessions,
        "agents": serialised_agents,
        "machines": sorted(machines),
        "machine_count": len(machines),
        "sync": {
            "transport": "git",
            "state": "local-files",
            "message": "Mail 文件通过 Git 在机器之间交换；成员页可显式一键同步，不会后台 push/pull。",
        },
    }


def memory_status(root):
    """Return a read-only, bounded projection of the knowledge lifecycle."""
    def files_under(relative, *, exclude=()):
        base = root / relative
        if not base.is_dir():
            return []
        return [
            path for path in base.rglob("*")
            if path.is_file() and path.suffix.lower() == ".md"
            and not any(part in exclude for part in path.relative_to(base).parts)
        ]

    raw = files_under("raw", exclude={"executions", ".logs"})
    drafts = files_under("drafts")
    wiki = files_under("wiki")
    knowledge = wiki + drafts
    visible = []
    excluded = 0
    for path in knowledge:
        meta, _body = _memory_meta(path)
        if _memory_is_visible(meta):
            visible.append(path)
        else:
            excluded += 1
    latest = sorted(visible, key=lambda path: _memory_event_time(_memory_meta(path)[0], path)[0], reverse=True)[:12]
    recent = []
    facets = {"areas": {}, "types": {}}
    for path in visible:
        meta, body = _memory_meta(path)
        item = _memory_summary(meta, body, path)
        for facet_name, value in (("areas", item["area"]), ("types", item["type"])):
            facets[facet_name][value] = facets[facet_name].get(value, 0) + 1
        if path in latest:
            item["path"] = path.relative_to(root).as_posix()
            recent.append(item)
    recent.sort(key=lambda item: item["updated_at"], reverse=True)
    return {
        "schema": "memory.snapshot.v1",
        "counts": {
            "raw": len(raw),
            "drafts": sum(1 for path in drafts if path in visible),
            "wiki": sum(1 for path in wiki if path in visible),
            "drafts_total": len(drafts),
            "wiki_total": len(wiki),
            "excluded": excluded,
        },
        "lifecycle": ["raw", "candidate", "human_review", "wiki", "recall", "explicit_feedback"],
        "recent": recent,
        "facets": facets,
    }


def _memory_plain(value: str, limit: int = 240) -> str:
    """Make a small, safe human-facing summary without pretending to render Markdown."""
    value = re.sub(r"`([^`]*)`", r"\1", str(value or ""))
    value = re.sub(r"!?(?:\[([^\]]+)\]\([^)]*\))", r"\1", value)
    value = re.sub(r"[*_~]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


_MEMORY_TIME_KEYS = (
    "updated_at",
    "updated",
    "modified_at",
    "ingested_at",
    "human_reviewed_at",
    "created",
)


def _parse_memory_time(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(raw), time.min)
            except ValueError:
                return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _memory_event_time(meta: dict, path: Path) -> tuple[datetime, str]:
    """Prefer durable knowledge timestamps; use mtime only as a compatibility fallback."""
    for key in _MEMORY_TIME_KEYS:
        parsed = _parse_memory_time(meta.get(key))
        if parsed is not None:
            return parsed, key
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc), "file_mtime"


def _memory_is_visible(meta: dict) -> bool:
    status = str(meta.get("status") or "active").strip().lower()
    archived = meta.get("archived") is True or str(meta.get("archived") or "").strip().lower() in {"1", "true", "yes"}
    return not archived and status not in {"dropped", "superseded", "retired", "archived"}


def _memory_meta(path: Path) -> tuple[dict, str]:
    parsed = store.parse_wiki_file(path) or {}
    body = str(parsed.pop("body", "") or "")
    return parsed, body


def _memory_title(meta: dict, body: str, path: Path) -> str:
    for key in ("title", "name"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return _memory_plain(value.strip(), 140)
    for line in body.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match and match.group(1).strip():
            return _memory_plain(match.group(1), 140)
    return _memory_plain(path.stem.replace("-", " ").replace("_", " "), 140) or "未命名知识"


def _memory_summary(meta: dict, body: str, path: Path) -> dict:
    summary = ""
    for key in ("summary", "description", "abstract"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            summary = _memory_plain(value)
            break
    if not summary:
        paragraph = []
        for line in body.splitlines():
            clean = line.strip()
            if not clean:
                if paragraph:
                    break
                continue
            if clean.startswith("#") or clean.startswith("---"):
                continue
            paragraph.append(clean)
        summary = _memory_plain(" ".join(paragraph))
    if not summary:
        summary = "这份知识还没有可显示的摘要，打开详情查看原文。"
    kind = "wiki" if "wiki" in path.parts else "candidate"
    area = str(meta.get("area") or meta.get("domain") or "未分类").strip() or "未分类"
    memory_type = str(meta.get("type") or ("wiki" if kind == "wiki" else "candidate")).strip() or ("wiki" if kind == "wiki" else "candidate")
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
    elif not isinstance(tags, list):
        tags = []
    event_time, event_source = _memory_event_time(meta, path)
    status = str(meta.get("status", "active" if kind == "wiki" else "pending")).strip() or ("active" if kind == "wiki" else "pending")
    status_label = {
        "active": "可复用",
        "pending": "待审核",
        "provisional": "待确认",
        "stale": "需要更新",
    }.get(status, status)
    return {
        "path": str(path),
        "kind": kind,
        "kind_label": "已审核 Wiki" if kind == "wiki" else "Candidate 候选",
        "title": _memory_title(meta, body, path),
        "summary": summary,
        "area": area,
        "type": memory_type,
        "tags": [str(tag) for tag in tags[:12]],
        "status": status,
        "status_label": status_label,
        "updated_at": event_time.isoformat(),
        "timestamp_source": event_source,
    }


def _memory_item(root: Path, relative: str) -> dict:
    """Read one Markdown knowledge item, refusing paths outside wiki/drafts."""
    if not relative or Path(relative).is_absolute():
        raise ValueError("memory path must be a relative wiki/ or drafts/ path")
    candidate = (root / relative).resolve()
    allowed = [(root / "wiki").resolve(), (root / "drafts").resolve()]
    if candidate.suffix.lower() != ".md" or not candidate.is_file() or not any(candidate.is_relative_to(base) for base in allowed):
        raise FileNotFoundError("memory item not found")
    meta, body = _memory_meta(candidate)
    summary = _memory_summary(meta, body, candidate)
    summary["path"] = candidate.relative_to(root).as_posix()
    summary["body"] = body[:12000]
    summary["truncated"] = len(body) > 12000
    summary["metadata"] = {
        key: _json_safe(value)
        for key, value in meta.items()
        if key not in {"file_path", "slug"}
    }
    return summary


def member_profiles(root: Path) -> dict:
    """Project shared Agent role profiles without exposing private user files.

    A profile is descriptive shared context. It is not a runtime connection,
    installation record, or proof that an Agent is currently available.
    """
    base = root / "profiles"
    paths = []
    team_path = base / "team.md"
    if team_path.is_file():
        paths.append((team_path, "team"))
    agents_dir = base / "agents"
    if agents_dir.is_dir():
        paths.extend(
            (path, "assistant")
            for path in sorted(agents_dir.glob("*.md"))
            if not path.name.startswith("_")
        )
    profiles = []
    for path, kind in paths:
        try:
            meta, body = _memory_meta(path)
            stat = path.stat()
        except (OSError, ValueError):
            continue
        title = _memory_title(meta, body, path)
        summary = _memory_summary(meta, body, path)["summary"]
        role = meta.get("role") or meta.get("responsibility") or meta.get("responsibilities") or ""
        scope = meta.get("scope") or meta.get("areas") or meta.get("area") or ""
        if isinstance(scope, (list, tuple)):
            scope = "、".join(str(item) for item in scope)
        profiles.append({
            "id": str(meta.get("id") or path.stem),
            "kind": kind,
            "title": title,
            "summary": summary,
            "role": str(role),
            "scope": str(scope),
            "status": str(meta.get("status") or "active"),
            "profile_kind": str(meta.get("profile_kind") or ("agent" if kind == "assistant" else "team")),
            "path": path.relative_to(root).as_posix(),
            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return {"schema": "oks.members.v1", "profiles": profiles}


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send_json(403, {"error": "loopback Host required"})
            return
        route = self.path.split("?", 1)[0]
        if route == "/api/mail/activity":
            self.send_json(200, activity_data(self.server.kb_root, UI_AGENT))
            return
        if route == "/api/mail/thread":
            thread_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            rows = mail.thread_messages(self.server.kb_root, thread_id, UI_AGENT)
            if not rows:
                self.send_json(404, {"error": "thread not found"})
                return
            deliveries = delivery_records(self.server.kb_root, rows)
            messages = []
            for row in rows:
                meta = row["meta"]
                state = row.get("state") or {}
                messages.append({**meta, "body": row["body"], "read_at": state.get("read_at"),
                                 "deliveries": deliveries.get(str(meta.get("message_id")), [])})
            self.send_json(200, {"thread_id": thread_id, "messages": messages,
                                 "title": rows[0].get("title", "未命名对话"),
                                 "state": "archived" if all((row.get("state") or {}).get("archived_at") for row in rows) else "open"})
            return
        if route == "/api/mail/status":
            self.send_json(200, connection_status(self.server.kb_root))
            return
        if route == "/api/mail/team":
            self.send_json(200, team_sync.status(self.server.kb_root))
            return
        if route in {"/api/mail/members", "/api/members"}:
            self.send_json(200, member_profiles(self.server.kb_root))
            return
        if route in {"/api/memory", "/api/mail/memory"}:
            self.send_json(200, memory_status(self.server.kb_root))
            return
        if route in {"/api/memory/item", "/api/mail/memory/item"}:
            relative = parse_qs(urlsplit(self.path).query).get("path", [""])[0]
            try:
                self.send_json(200, _memory_item(self.server.kb_root, relative))
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if route == "/api/mail/snapshot":
            self.send_json(200, mail.snapshot_data(self.server.kb_root, UI_AGENT))
            return
        entry = FILES.get(route)
        if not entry:
            self.send_error(404)
            return
        data = (ROOT / entry[0]).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", entry[1] + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, value):
        data = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > 65536:
            raise ValueError("request body must be between 1 and 65536 bytes")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def do_POST(self):
        allowed_origin = f"http://127.0.0.1:{self.server.server_port}"
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}' or self.headers.get('Origin', allowed_origin) != allowed_origin or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            self.send_json(403, {'error': 'same-origin JSON requests required'})
            return
        route = self.path.split("?", 1)[0]
        if route not in {"/api/mail/send", "/api/mail/reply", "/api/mail/invite", "/api/mail/read", "/api/mail/archive", "/api/mail/unarchive", "/api/mail/team/sync"}:
            self.send_error(404)
            return
        try:
            payload = self.read_json()
            if route == "/api/mail/team/sync":
                result = team_sync.sync(
                    self.server.kb_root,
                    push=bool(payload.get("push", False)),
                    message=str(payload.get("message", "同步 OKS 团队资料")),
                )
                self.send_json(200, result)
                return
            if route == "/api/mail/send":
                title = str(payload.get("title", "")).strip()
                body = str(payload.get("body", "")).strip()
                recipient = str(payload.get("to", "")).strip()
                if not title or not body or not recipient:
                    raise ValueError("title, body and recipient are required")
                recipient = ["@" + agent_id(value) for value in recipient.split(",")]
                if len(title) > 120 or len(body) > 12000:
                    raise ValueError("title/body too long")
                record_kind = mail.normalise_record_kind(payload.get("record_kind", "message"))
                delivery_reason = str(payload.get("delivery_reason", "direct") or "direct").strip().lower()
                if delivery_reason not in mail.REASONS:
                    raise ValueError("unsupported delivery reason")
                evidence_refs = mail.normalise_evidence_refs(payload.get("evidence_refs", []))
                result = mail.write_message(
                    self.server.kb_root,
                    body=body,
                    sender=UI_AGENT,
                    sender_kind="human",
                    recipients=recipient,
                    title=title,
                    origin_session_id=self.server.session_id,
                    origin_machine_id=identity.normalise_machine_id(),
                    delivery_reason=delivery_reason,
                    record_kind=record_kind,
                    evidence_refs=evidence_refs,
                    session_policy="next_prompt",
                )
                self.send_json(201, {"status": "saved", "message_id": result["message_id"], "thread_id": result["thread_id"], "record_kind": record_kind, "evidence_refs": evidence_refs})
                return
            if route == "/api/mail/reply":
                thread_id = str(payload.get("thread_id", "")).strip()
                body = str(payload.get("body", "")).strip()
                if not thread_id or not body:
                    raise ValueError("thread_id and body are required")
                recipients, latest = reply_recipients(self.server.kb_root, thread_id)
                result = mail.write_message(
                    self.server.kb_root,
                    body=body,
                    sender=UI_AGENT,
                    sender_kind="human",
                    recipients=recipients,
                    title=f"Re: {latest['title']}",
                    thread_id=thread_id,
                    reply_to=str(latest["meta"].get("message_id", "")),
                    origin_session_id=self.server.session_id,
                    origin_machine_id=identity.normalise_machine_id(),
                    delivery_reason="thread_reply",
                    record_kind="note",
                    session_policy="next_prompt",
                )
                self.send_json(201, {"status": "saved", "message_id": result["message_id"], "thread_id": thread_id})
                return
            if route == "/api/mail/invite":
                thread_id = str(payload.get("thread_id", "")).strip()
                recipient = agent_id(str(payload.get("to", "")).strip())
                body = str(payload.get("body", "")).strip()
                if not thread_id or not body:
                    raise ValueError("thread_id, recipient and body are required")
                if recipient.lower() in {"human", "all", "unknown"}:
                    raise ValueError("Invite a named Agent, not human, all or unknown")
                messages = mail.thread_messages(self.server.kb_root, thread_id, UI_AGENT)
                if not messages:
                    raise ValueError("thread not found")
                latest = messages[-1]
                invited = "@" + recipient
                participants = {
                    "@" + value.lstrip("@").strip()
                    for message in messages
                    for value in ({str(message["meta"].get("from", ""))} | set(message["meta"].get("to", [])))
                    if value and value.lstrip("@").strip().lower() not in {"human", "all", "unknown"}
                }
                if invited.casefold() in {value.casefold() for value in participants}:
                    raise ValueError("Agent is already in this Thread")
                recipients = sorted(participants)
                result = mail.write_message(
                    self.server.kb_root,
                    body=body,
                    sender=UI_AGENT,
                    sender_kind="human",
                    recipients=sorted(set(recipients + [invited])),
                    title=f"邀请 {recipient} 加入对话",
                    thread_id=thread_id,
                    reply_to=str(latest["meta"].get("message_id", "")),
                    origin_session_id=self.server.session_id,
                    origin_machine_id=identity.normalise_machine_id(),
                    delivery_reason="direct",
                    record_kind="note",
                    session_policy="next_prompt",
                )
                self.send_json(201, {"status": "saved", "message_id": result["message_id"], "thread_id": thread_id, "recipients": result["recipients"]})
                return
            if route == "/api/mail/archive":
                thread_id = str(payload.get("thread_id", "")).strip()
                message_id = str(payload.get("message_id", "")).strip()
                target = thread_id or message_id
                if not target:
                    raise ValueError("thread_id or message_id is required")
                selected = [
                    item for item in mail.iter_messages(self.server.kb_root, UI_AGENT)
                    if item["meta"].get("thread_id") == target or item["meta"].get("message_id") == target
                ]
                if not selected:
                    raise ValueError("mail or thread not found")
                archived_at = mail.iso_now()
                for message in selected:
                    message_id = str(message["meta"].get("message_id"))
                    changes = {"archived_at": archived_at, "thread_state": "closed"}
                    if message["path"].parent != mail.messages_dir(self.server.kb_root):
                        # A legacy Markdown file is shared by all recipients.
                        # Seed a new recipient projection from its old read
                        # marker, but never rewrite the shared file.
                        state_path = mail.recipient_state_path(self.server.kb_root, UI_AGENT, message_id)
                        if not state_path.is_file() and str(message["meta"].get("read", "false")).lower() == "true":
                            changes["read_at"] = (
                                message["meta"].get("read_at")
                                or message["meta"].get("timestamp")
                                or mail.iso_now()
                            )
                    if message["path"].parent == mail.messages_dir(self.server.kb_root):
                        mail.update_recipient_state(
                            self.server.kb_root, UI_AGENT, message_id, **changes,
                        )
                    else:
                        mail.update_recipient_state(
                            self.server.kb_root, UI_AGENT, message_id, **changes,
                        )
                self.send_json(200, {"status": "archived", "target": target, "count": len(selected)})
                return
            if route == "/api/mail/unarchive":
                thread_id = str(payload.get("thread_id", "")).strip()
                message_id = str(payload.get("message_id", "")).strip()
                target = thread_id or message_id
                if not target:
                    raise ValueError("thread_id or message_id is required")
                selected = [
                    item for item in mail.iter_messages(self.server.kb_root, UI_AGENT)
                    if item["meta"].get("thread_id") == target or item["meta"].get("message_id") == target
                ]
                if not selected:
                    raise ValueError("mail or thread not found")
                for message in selected:
                    message_id = str(message["meta"].get("message_id"))
                    mail.update_recipient_state(
                        self.server.kb_root, UI_AGENT, message_id,
                        archived_at=None, thread_state="open",
                    )
                self.send_json(200, {"status": "unarchived", "target": target, "count": len(selected)})
                return
            if route == "/api/mail/read":
                message = visible_message(self.server.kb_root, str(payload.get("message_id", "")).strip())
                if not message:
                    raise ValueError("message not found")
                message_id = str(message["meta"].get("message_id", ""))
                if message["path"].parent == mail.messages_dir(self.server.kb_root):
                    state = mail.update_recipient_state(self.server.kb_root, UI_AGENT, message_id, read_at=mail.iso_now())
                else:
                    # Legacy Markdown is shared by all recipients; persist the
                    # read transition in this Agent's projection only.
                    state = mail.update_recipient_state(self.server.kb_root, UI_AGENT, message_id, read_at=mail.iso_now())
                self.send_json(200, {"status": "read", "message_id": message_id, "state": state})
                return
            self.send_error(404)
        except team_sync.TeamSyncError as exc:
            self.send_json(409, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

def create_server(root: Path, port: int = 3182) -> ThreadingHTTPServer:
    root = validate_root(root)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.kb_root = root
    machine_id = identity.normalise_machine_id()
    machine_token = hashlib.sha256(machine_id.encode("utf-8")).hexdigest()[:12]
    server.session_id = f"mail-ui-{server.server_port}-{machine_token}"
    mail.register_session(
        root,
        server.session_id,
        UI_AGENT,
        scope="mail-web",
        machine_id=machine_id,
    )
    return server
