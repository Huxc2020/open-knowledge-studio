"""Read-only communication projections for messages visible to the Web identity."""
import json

from knowledge_studio import mail


def delivery_records(root, messages):
    """Project per-recipient facts without treating presentation as acknowledgement."""
    by_id = {str(row["meta"]["message_id"]): row for row in messages}
    pairs = set()
    for path in (root / "mail" / "receipts").glob("*/*.json"):
        if path.stem in by_id:
            pairs.add((path.parent.name, path.stem))
    for path in (root / "mail" / "receipt-events").glob("*/*"):
        if path.is_dir() and path.name in by_id:
            pairs.add((path.parent.name, path.name))
    receipts = {}
    for session_id, message_id in sorted(pairs):
        try:
            record = mail._load_receipt_snapshot(root, session_id, message_id)
        except (OSError, ValueError):
            continue
        if not record or record.get("message_id") != message_id:
            continue
        agent = mail._normalise_agent(str(record.get("agent_id", "")))
        recipients = by_id[message_id]["meta"].get("to", [])
        if agent not in recipients and "@all" not in recipients:
            continue
        receipts.setdefault((message_id, agent), []).append(record)
    result = {}
    for message_id, row in by_id.items():
        declared = row["meta"].get("to", [])
        # ``@all`` is an address, not a participant. Expanding it must resolve to
        # the agents that actually produced a receipt; the literal token must not
        # survive into the per-recipient projection, or every broadcast message
        # gains a phantom recipient and pollutes its delivery detail.
        recipients = set(declared) - {"", "@all"}
        if "@all" in declared:
            recipients.update(agent for mid, agent in receipts if mid == message_id)
        result[message_id] = []
        for agent in sorted(recipients):
            try:
                state = mail.load_state(root, agent, message_id)
            except (OSError, ValueError):
                state = {}
            if not isinstance(state, dict):
                state = {}
            result[message_id].append({
                "agent_id": agent,
                "read_at": state.get("read_at"),
                "sessions": [{key: record.get(key) for key in (
                    "session_id", "machine_id", "status", "injected_at",
                    "delivered_at", "acknowledged_at",
                )} for record in receipts.get((message_id, agent), [])],
            })
    return result


def activity_data(root, viewer="human", limit=200):
    rows = list(mail.iter_messages(root, viewer))
    # Bound the returned feed while retaining the roster from visible history.
    rows.sort(key=lambda row: (str(row["meta"].get("timestamp", "")), str(row["meta"].get("message_id", ""))))
    members = {}
    for row in rows:
        meta = row["meta"]
        sender = mail._normalise_agent(str(meta.get("from", "")))
        for participant in {sender, *meta.get("to", [])} - {"", "@all"}:
            member = members.setdefault(participant, {
                "id": participant, "kind": "human" if participant == "@human" else "unknown",
                "last_message_at": "", "session_count": 0, "machine_ids": [], "last_seen_at": "",
            })
            if participant == sender and meta.get("sender_kind") in {"human", "agent", "system"}:
                member["kind"] = meta["sender_kind"]
            member["last_message_at"] = max(member["last_message_at"], str(meta.get("timestamp", "")))
    for path in mail.sessions_dir(root).glob("*.json"):
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(session, dict) or not session.get("agent_id"):
            continue
        participant = mail._normalise_agent(str(session["agent_id"]))
        member = members.setdefault(participant, {
            "id": participant, "kind": "unknown", "last_message_at": "",
            "session_count": 0, "machine_ids": [], "last_seen_at": "",
        })
        if member["kind"] == "unknown":
            member["kind"] = "human" if participant == "@human" else "agent"
        member["session_count"] += 1
        machine = str(session.get("machine_id") or "")
        if machine and machine != "unknown" and machine not in member["machine_ids"]:
            member["machine_ids"].append(machine)
        member["last_seen_at"] = max(member["last_seen_at"], str(session.get("last_seen_at") or ""))
    recent = rows[-limit:]
    deliveries = delivery_records(root, recent)
    events = []
    for row in recent:
        meta = row["meta"]
        mid = str(meta["message_id"])
        base = {"message_id": mid, "thread_id": meta.get("thread_id") or mid,
                "title": row.get("title") or meta.get("title") or "未命名对话",
                "to": meta.get("to", [])}
        events.append({**base, "id": f"{mid}:message", "type": "reply" if meta.get("reply_to") else "message",
                       "actor": meta.get("from"), "timestamp": meta.get("timestamp"),
                       "excerpt": row.get("body", "")[:180],
                       "session_id": meta.get("origin_session_id"), "machine_id": meta.get("origin_machine_id")})
        for recipient in deliveries[mid]:
            if recipient["read_at"]:
                events.append({**base, "id": f"{mid}:{recipient['agent_id']}:read", "type": "read",
                               "actor": recipient["agent_id"], "timestamp": recipient["read_at"]})
            for session in recipient["sessions"]:
                for event_type, stamp in (("presented", session.get("delivered_at")),
                                          ("injected", session.get("injected_at") if not session.get("delivered_at") else None),
                                          ("acknowledged", session.get("acknowledged_at"))):
                    if stamp:
                        events.append({**base, "id": f"{mid}:{session['session_id']}:{event_type}",
                                       "type": event_type, "actor": recipient["agent_id"], "timestamp": stamp,
                                       "session_id": session["session_id"], "machine_id": session["machine_id"]})
    events.sort(key=lambda event: (str(event.get("timestamp") or ""), event["id"]), reverse=True)
    return {"events": events[:limit], "members": sorted(members.values(), key=lambda member: member["id"]),
            "truncated": len(rows) > limit or len(events) > limit, "scope": "current-knowledge-base"}
