"""Read-only human timeline projection over Mail facts.

Mail is the collaboration fact sidecar. Its canonical records are machine-shaped
(``delivery_reason``, ``record_kind``, ``receipt`` events). This module is the
single place where those protocol fields become plain language, so the serve
UI, the CLI and any future adapter cannot drift apart in wording.

Design rules enforced here (see ``.agent/DESIGN.md`` D3/D5):

* Nothing is written back; the canonical message schema is untouched.
* Lifecycle facts (presented / injected / acknowledged) are **evidence attached
  to a node**, never separate top level entries.
* ``acknowledged`` means "confirmed received inside one Session". It is never
  allowed to read as "done".
* The projection is bounded and one-recipient. When it truncates, it says so.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from knowledge_studio import mail
from knowledge_studio.mail_activity import delivery_records

SCHEMA = "mail.timeline.v1"

#: How many nodes a caller may ask for in one projection.
MAX_LIMIT = 500
DEFAULT_LIMIT = 200

# Lifecycle vocabulary.  ``acknowledged`` deliberately carries its own caveat so
# no caller has to remember the rule.
STEP_LABELS = {
    "presented": "已呈现给该 Session",
    "injected": "已注入该 Session 的上下文",
    "acknowledged": "该 Session 已确认收到",
}

# Lifecycle order of one recipient's steps. Same-instant steps used to fall back
# to alphabetical order, which puts "acknowledged" before "presented" and so
# renders the confirmation above the event it confirms.
STEP_ORDER = {"injected": 0, "presented": 1, "acknowledged": 2}

STATE_LABELS = {
    "created": "已保存，等待对方读取",
    "notified": "已通知，尚未读取",
    "read": "已标记已读",
    "acknowledged": "已确认收到（不代表完成）",
    "archived": "已归档",
}

REASON_LABELS = {
    "direct": "直接投递",
    "mention": "被提到",
    "conflict": "冲突提醒",
    "review_request": "请求审核",
    "thread_reply": "同一段对话的回复",
    "system": "系统记录",
    "handoff": "交接",
}

RECORD_KIND_LABELS = {
    "message": "留下一条记录",
    "handoff": "交接一项工作",
    "result": "报告结果",
    "blocked": "报告当前阻塞",
    "note": "补充说明",
    "knowledge_ref": "引用一条知识",
}

# Fixed phase skeleton for the OKS Mail read-only panel.  The ladder is a
# *lossy, evidence-bound* projection: the four stages always render, but a
# stage only leaves `pending` when a real event matches its rule.  We never
# invent a timestamp — `done`/`active` times come strictly from the matched
# event's own `timestamp` (or a real receipt `acknowledged_at`).  Rules live
# here (not in code paths) so the mapping can change without touching the
# projection logic.
#
# The ladder is projected with a forward-only *time cursor*, not by letting
# every stage pick its own earliest match.  That earlier shortcut was wrong:
# with more than one Thread in the knowledge base, stage ① could lock onto the
# first human record while stage ③ locked onto a `knowledge_ref` that happened
# *before* it, so the four stamps rendered out of order and told a pipeline
# story that never happened.  The cursor walks the real event stream in
# chronological order and never moves backwards, so the stamps are monotonic by
# construction and each advance is caused by an event that really came later.
#
# Predicate vocabulary (see `_stage_match`):
#   sender_kind / from / record_kind / delivery_reason : scalar or {set}
#   evidence_ref_type : true if any attached evidence_ref has this type
#   acknowledged      : true if a real delivery receipt reached `acknowledged`
STAGE_LADDER = [
    {
        "key": "extract",
        "label": "提取",
        # ① 提取：最早一条由 human 发起的需求记录。可靠信号 = 发送方为人类
        # (sender_kind "human")。我们刻意不限定 record_kind∈{message,note}：
        # 最早的人类消息本身就是需求，无论它如何被分类；过滤反而会漏掉。
        "enter": [{"sender_kind": "human"}],
        "done": [{"sender_kind": "human"}],
        "note": "以最早一条由你（human）发起的协作记录作为「提取」起点；无人类记录则永远 pending。",
    },
    {
        "key": "extract_done",
        "label": "提取完成",
        # ② 提取完成：出现交接（handoff）或结果报告（result）即证明提取已结束。
        "enter": [{"record_kind": {"handoff", "result"}}, {"delivery_reason": "handoff"}],
        "done": [{"record_kind": {"handoff", "result"}}, {"delivery_reason": "handoff"}],
        "note": "出现交接（handoff）或结果报告（result）即视为提取已完成。",
    },
    {
        "key": "draft_review",
        "label": "Draft 待审核",
        # ③ Draft 待审核：已产出 Draft（result）但尚未请审核 -> `active`；
        # 出现审核请求（review_request）或知识引用（knowledge_ref）-> `done`。
        "enter": [{"record_kind": "result"}],
        "done": [{"delivery_reason": "review_request"}, {"record_kind": "knowledge_ref"}],
        "note": "产出 Draft（result）后等待审核；出现审核请求（review_request）或知识引用（knowledge_ref）即视为已提交审核。",
    },
    {
        "key": "stage_complete",
        "label": "阶段完毕",
        # ④ 阶段完毕：可靠信号 = 一条 `system` 系统记录并附带 wiki 知识更新
        # （evidence_ref type "wiki"）。Owner 提出的另一条规则「result 且已被确认
        # (ack)」故意不使用：本 schema 中 `acknowledged` 仅表示「在一个 Session 内
        # 确认收到」，且被模块 docstring / STATE_LABELS 明确禁止读作「完成」。用 ack
        # 推断阶段完毕会与该语义冲突并夸大进度，故判定为「无法可靠推出」，落回 pending。
        "enter": [{"record_kind": "result"}, {"delivery_reason": "system", "evidence_ref_type": "wiki"}],
        "done": [{"delivery_reason": "system", "evidence_ref_type": "wiki"}],
        "note": "仅以系统记录的知识库更新（wiki）作为「阶段完毕」的可靠证据；用 ack 推断完成不可靠，已放弃。",
    },
]

STAGE_LADDER_NOTE = "时间一律取自真实事件；没走到的那一步不猜、不补。"

# 上面那句是给用户看的一句话。下面这段是同一件事的完整口径，放在「展开说明」里，
# 不占默认阅读路径 —— 诚实必须保留，但不必堆在首屏。
STAGE_LADDER_WHY = (
    "四个阶段是固定骨架，按真实事件的时间顺序推进：一个阶段只有在真的出现"
    "推进证据时才离开「未到达」，时间戳一律取自那个事件的自身时间，绝不用当前"
    "时间伪造。阶段之间只向前走、不回头，所以时间是递增的。本库若有多条并行"
    "协作线，这条阶梯会把它们串成同一条时间轴——它回答的是「这个库里最早走到"
    "哪一步」，不等于「某一个线程完整走完了流程」。"
)


def _plain_excerpt(value: str, limit: int = 140) -> str:
    """Keep a short single-line excerpt; the full value stays available."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _display(agent: str) -> str:
    value = str(agent or "").strip().removeprefix("@")
    if not value or value == "unknown":
        return "未知成员"
    if value == "human":
        return "你"
    if value == "all":
        return "全体成员"
    return value


def _peers(values) -> str:
    """Render recipients inside a Chinese sentence.

    Pure-Chinese labels ("你", "全体成员") sit flush; Latin Agent IDs get one
    space of breathing room so the sentence stays readable.
    """
    labels = [_display(item) for item in values if str(item).strip() not in {"", "@all", "all"}]
    if not labels:
        return "全体成员"
    text = "、".join(dict.fromkeys(labels))
    return f" {text} " if re.search(r"[A-Za-z0-9@._-]", text) else text


def _title(meta: dict, row: dict) -> str:
    return str(row.get("title") or meta.get("title") or "未命名对话").strip()


def classify(meta: dict) -> tuple[str, bool, str]:
    """Translate one message into (action sentence, is_key_action, icon key).

    Order matters: an explicit conflict notice outranks the handoff it describes.
    """
    reason = str(meta.get("delivery_reason") or "direct").strip().lower()
    record_kind = str(meta.get("record_kind") or "message").strip().lower()
    reply_to = str(meta.get("reply_to") or "").strip()
    peers = _peers(meta.get("to") or [])

    if reason == "conflict":
        return (f"发现文件冲突，提醒{peers}一起确认", True, "conflict")
    if reason == "handoff" or record_kind == "handoff":
        return (f"把这件事交给{peers}继续", True, "handoff")
    if reason == "review_request" or record_kind == "knowledge_ref":
        return (f"请{peers}查看并审核", True, "review")
    if record_kind == "result":
        return ("报告了一项结果", True, "result")
    if record_kind == "blocked":
        return ("报告当前被阻塞", True, "blocked")
    if reason == "system":
        return ("系统记录了这次协作的事实", True, "system")
    if reply_to:
        return ("在同一段对话里回复", True, "reply")
    if reason == "mention":
        return (f"在这一段对话里提到{peers}", False, "mention")
    return ("留下一条协作记录", False, "record")


def _delivery_evidence(root: Path, rows: list, deliveries: dict) -> dict:
    """Attach per-recipient lifecycle facts to each message id."""
    evidence = {}
    for row in rows:
        meta = row["meta"]
        message_id = str(meta.get("message_id", ""))
        recipients = []
        for recipient in deliveries.get(message_id, []):
            agent = str(recipient.get("agent_id", ""))
            sessions = recipient.get("sessions") or []
            steps = []
            for session in sessions:
                injected_at = str(session.get("injected_at") or "")
                delivered_at = str(session.get("delivered_at") or "")
                acknowledged_at = str(session.get("acknowledged_at") or "")
                # `record_delivery` stamps injected and delivered together for a
                # delivered message. Showing two lines for one instant is noise,
                # so the stronger fact wins and says what it absorbed.
                if injected_at and (not delivered_at or injected_at < delivered_at):
                    steps.append({
                        "step": "injected",
                        "label": STEP_LABELS["injected"],
                        "at": injected_at,
                        "session_id": str(session.get("session_id") or ""),
                        "machine_id": str(session.get("machine_id") or ""),
                    })
                if delivered_at:
                    steps.append({
                        "step": "presented",
                        "label": STEP_LABELS["presented"],
                        "at": delivered_at,
                        "session_id": str(session.get("session_id") or ""),
                        "machine_id": str(session.get("machine_id") or ""),
                        "supersedes": "injected" if injected_at == delivered_at else "",
                    })
                if acknowledged_at:
                    steps.append({
                        "step": "acknowledged",
                        "label": STEP_LABELS["acknowledged"],
                        "at": acknowledged_at,
                        "session_id": str(session.get("session_id") or ""),
                        "machine_id": str(session.get("machine_id") or ""),
                    })
            steps.sort(key=lambda item: (item["at"], STEP_ORDER.get(item["step"], len(STEP_ORDER))))
            acknowledged = any(step["step"] == "acknowledged" for step in steps)
            read_at = recipient.get("read_at")
            if acknowledged:
                state = "acknowledged"
            elif read_at:
                state = "read"
            elif steps:
                state = "notified"
            else:
                state = "created"
            recipients.append({
                "peer": agent,
                "peer_label": _display(agent),
                "state": state,
                "state_label": STATE_LABELS[state],
                "read_at": read_at,
                "steps": steps,
                "sessions": len({step["session_id"] for step in steps if step["session_id"]}),
            })
        evidence[message_id] = recipients
    return evidence


def _evidence_refs(meta: dict) -> list[dict]:
    raw = meta.get("evidence_refs")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = []
    if not isinstance(raw, (list, tuple)):
        return []
    refs = []
    for item in raw:
        if isinstance(item, dict):
            refs.append({str(key): str(value) for key, value in item.items()})
        elif item:
            refs.append({"id": str(item)})
    return refs


def _stage_match(predicate: dict, meta: dict, refs: list, acknowledged: bool) -> bool:
    """True if one message satisfies every clause of a STAGE_LADDER predicate."""
    for key, value in predicate.items():
        if key == "sender_kind":
            if str(meta.get("sender_kind") or "") != value:
                return False
        elif key == "from":
            if str(meta.get("from") or "") != value:
                return False
        elif key in ("record_kind", "delivery_reason"):
            wanted = value if isinstance(value, set) else {value}
            if str(meta.get(key) or "") not in wanted:
                return False
        elif key == "evidence_ref_type":
            if not any(str(ref.get("type") or "") == value for ref in refs):
                return False
        elif key == "acknowledged":
            if bool(acknowledged) != bool(value):
                return False
        else:
            return False
    return True


def _stage_evidence_item(meta: dict, row: dict) -> dict:
    """One traceable pointer back to the real event that pushed a stage."""
    action, _, _ = classify(meta)
    return {
        "message_id": str(meta.get("message_id", "")),
        "at": str(meta.get("timestamp") or ""),
        "thread": str(meta.get("thread_id") or ""),
        "summary": f"{action}（{_title(meta, row)}）",
    }


def _build_stage_ladder(rows: list, deliveries: dict) -> list[dict]:
    """Project the fixed 4-stage ladder from real Mail events only.

    Every stage renders. A stage is `done` only when a real completion event
    matches; `active` when it has evidence but no completion; `pending` with an
    empty `at` and a `pending_reason` distinguishing "not_reached" (the ladder
    got here but this step simply has not happened) from "no_evidence" (we have
    no basis to claim it at all). Timestamps come from event `timestamp` only.

    Progress is decided by a forward-only cursor over the chronological event
    stream: once the ladder has advanced to time T, an event older than T can no
    longer push a later stage. That is what keeps the four stamps increasing —
    without it, each stage picks its own earliest match and a multi-Thread
    knowledge base renders a pipeline that never happened.
    """
    enriched = []
    for row in rows:
        meta = row["meta"]
        refs = _evidence_refs(meta)
        acknowledged = False
        for recipient in deliveries.get(str(meta.get("message_id", "")), []):
            if any(session.get("acknowledged_at") for session in recipient.get("sessions", [])):
                acknowledged = True
                break
        enriched.append((meta, refs, acknowledged, row))
    enriched.sort(key=lambda item: (str(item[0].get("timestamp") or ""), str(item[0].get("message_id") or "")))

    stages = []
    prev_status = None
    cursor = ""
    for rule in STAGE_LADDER:
        done_hits, enter_hits = [], []
        for meta, refs, acknowledged, row in enriched:
            at = str(meta.get("timestamp") or "")
            if cursor and (not at or at < cursor):
                continue  # the ladder already moved past this event
            if any(_stage_match(p, meta, refs, acknowledged) for p in rule["done"]):
                done_hits.append(_stage_evidence_item(meta, row))
            elif any(_stage_match(p, meta, refs, acknowledged) for p in rule["enter"]):
                enter_hits.append(_stage_evidence_item(meta, row))

        done_times = [hit["at"] for hit in done_hits if hit["at"]]
        enter_times = [hit["at"] for hit in enter_hits if hit["at"]]
        if done_hits:
            # An evidence item without a timestamp must not win ``min``: "" sorts
            # before every real timestamp, so a finished stage would be stamped
            # with no time at all, and the cursor (which only advances on a real
            # time) would then stop moving for every later stage.
            status, at = "done", min(done_times) if done_times else ""
        elif enter_hits:
            status, at = "active", max(enter_times) if enter_times else ""
        else:
            status, at = "pending", ""

        evidence = done_hits + enter_hits
        evidence.sort(key=lambda item: item["at"], reverse=True)
        # The event that actually stamped the stage goes first, so the evidence
        # line answers "why is this stamped at this time" instead of just
        # "which events matched" — otherwise a stage stamped on an older event
        # would list newer unrelated ones and read as a contradiction.
        evidence.sort(key=lambda item: item["at"] != at)
        evidence = evidence[:8]

        if status == "pending":
            pending_reason = "no_evidence" if prev_status is None else (
                "no_evidence" if prev_status == "pending" else "not_reached"
            )
        else:
            pending_reason = ""

        stages.append({
            "key": rule["key"],
            "label": rule["label"],
            "status": status,
            "at": at,
            "pending_reason": pending_reason,
            "evidence": evidence,
            "note": rule["note"],
        })
        prev_status = status
        if at:
            cursor = at
    return stages


def _stages_are_monotonic(stages: list) -> bool:
    """Invariant: the stamps the ladder hands out never go backwards.

    The cursor makes this true by construction; the check exists so a future
    predicate edit cannot quietly re-introduce the out-of-order ladder that
    `_build_stage_ladder` was rewritten to remove.
    """
    times = [str(stage.get("at") or "") for stage in stages if stage.get("at")]
    return all(earlier <= later for earlier, later in zip(times, times[1:]))


def timeline_data(root: Path, viewer: str = "human", limit: int = DEFAULT_LIMIT) -> dict:
    """Project visible Mail records into a bounded, human-readable timeline.

    The caller receives newest-first nodes plus one ``latest`` summary. Every
    node keeps its protocol fields under ``protocol`` so an expanded view can be
    honest without putting machine names in the default reading path.
    """
    limit = DEFAULT_LIMIT if limit is None else int(limit)
    limit = max(1, min(limit, MAX_LIMIT))
    rows = sorted(
        mail.iter_messages(root, viewer),
        key=lambda row: (str(row["meta"].get("timestamp", "")), str(row["meta"].get("message_id", ""))),
    )
    deliveries = delivery_records(root, rows)
    evidence = _delivery_evidence(root, rows, deliveries)

    thread_titles = {}
    for row in rows:
        meta = row["meta"]
        thread_titles[str(meta.get("thread_id") or meta.get("message_id"))] = _title(meta, row)

    # A reply whose author changed Session in the same Thread is real
    # cross-Session continuation: that is exactly the fact Mail exists to keep.
    last_session_by_thread_actor = {}
    cross_session_ids = set()
    for row in rows:
        meta = row["meta"]
        thread_id = str(meta.get("thread_id") or meta.get("message_id"))
        actor = mail._normalise_agent(str(meta.get("from", "")))
        session_id = str(meta.get("origin_session_id") or "")
        key = (thread_id, actor)
        previous = last_session_by_thread_actor.get(key)
        if previous and session_id and previous != session_id:
            cross_session_ids.add(str(meta.get("message_id", "")))
        if session_id:
            last_session_by_thread_actor[key] = session_id

    nodes = []
    for row in rows:
        meta = row["meta"]
        message_id = str(meta.get("message_id", ""))
        actor = mail._normalise_agent(str(meta.get("from", "")))
        action, key_action, icon = classify(meta)
        thread_id = str(meta.get("thread_id") or message_id)
        if message_id in cross_session_ids:
            action = "换了一个 Session 继续这段协作"
            key_action = True
            icon = "session"
        title = _title(meta, row)
        delivery = evidence.get(message_id, [])
        sessions = sorted({step["session_id"] for item in delivery for step in item["steps"] if step["session_id"]})
        machines = sorted({step["machine_id"] for item in delivery for step in item["steps"] if step["machine_id"]})
        origin_machine = str(meta.get("origin_machine_id") or "")
        if origin_machine and origin_machine != "unknown" and origin_machine not in machines:
            machines.append(origin_machine)
        nodes.append({
            "id": message_id,
            "time": str(meta.get("timestamp") or ""),
            "actor": actor,
            "actor_label": _display(actor),
            "actor_kind": str(meta.get("sender_kind") or ("human" if actor == "@human" else "agent")),
            "action": action,
            "action_key": icon,
            "key_action": key_action,
            "result": title,
            "excerpt": _plain_excerpt(row.get("body", "")),
            "thread": {"id": thread_id, "title": thread_titles.get(thread_id, title)},
            "cross_session": message_id in cross_session_ids,
            "evidence_refs": _evidence_refs(meta),
            "delivery": delivery,
            "sessions": sessions,
            "machines": machines,
            "protocol": {
                "message_id": message_id,
                "thread_id": thread_id,
                "reply_to": str(meta.get("reply_to") or ""),
                "delivery_reason": str(meta.get("delivery_reason") or ""),
                "delivery_reason_label": REASON_LABELS.get(str(meta.get("delivery_reason") or ""), ""),
                "record_kind": str(meta.get("record_kind") or ""),
                "record_kind_label": RECORD_KIND_LABELS.get(str(meta.get("record_kind") or ""), ""),
                "origin_session_id": str(meta.get("origin_session_id") or ""),
                "origin_machine_id": origin_machine,
            },
        })

    # A real receipt-level acknowledgement becomes its own timeline node (D5):
    # "已确认收到" is the fact; it never claims the underlying work is done.
    ack_nodes = []
    seen_acks = set()
    for node in nodes:
        for item in node["delivery"]:
            if item.get("state") != "acknowledged":
                continue
            at = next(
                (step.get("at") for step in item.get("steps", []) if step.get("step") == "acknowledged"),
                "",
            )
            peer = str(item.get("peer") or "")
            key = (node["id"], peer, str(at))
            if key in seen_acks:
                continue
            seen_acks.add(key)
            ack_nodes.append({
                "id": f"{node['id']}::ack::{peer}",
                "time": str(at) or node["time"],
                "actor": peer,
                "actor_label": _display(peer) if peer else "对方",
                "actor_kind": "agent" if peer and not peer.startswith("@human") else "human",
                "action": "已确认收到",
                "action_key": "ack",
                # 回执是「送到了」的凭据，不是推进了协作的事实。设计包 02 列的
                # 「进入时间线」清单（交接 / 请求审核 / 回复结果 / 冲突 / Wiki 更新 /
                # 跨 Session）不含回执。把它们算成关键，首屏就会被成片的
                # 「已确认收到」淹没 —— 这正是它们该待在「已确认收到」筛选里的原因。
                "key_action": False,
                "result": f"确认读到了「{node['result']}」这条记录；确认收到不等于事情已办妥。",
                "excerpt": "",
                "thread": node["thread"],
                "cross_session": False,
                "evidence_refs": [],
                "delivery": [],
                # One Session contributes several steps, so the id would repeat.
                # This module already treats the roster as a set elsewhere; the
                # ack node was the one place that did not.
                "sessions": sorted({step["session_id"] for step in item.get("steps", []) if step.get("session_id")}),
                "machines": sorted({step["machine_id"] for step in item.get("steps", []) if step.get("machine_id")}),
                "protocol": {"ack_of": node["id"], "ack_peer": peer},
            })
    nodes.extend(ack_nodes)

    # 节点级投递口径：多个收件人的状态要收拢成一句话，由后端给，前端不再自造。
    # 回执只证明「送到了」，所以确认类文案一律带「不代表完成」的限定——
    # 前端曾经自己拼过一版并把限定吞掉了，而测试断言的是带限定的版本，
    # 两边各写一套时谁都不会知道另一套改了。
    for node in nodes:
        recipients = node.get("delivery") or []
        if node.get("action_key") == "ack":
            node["delivery_chip"] = {"label": "已确认收到", "tone": "ok"}
        elif any(item.get("state") == "acknowledged" for item in recipients):
            node["delivery_chip"] = {"label": "对方已确认收到（不代表完成）", "tone": "ok"}
        elif recipients:
            node["delivery_chip"] = {"label": "已呈现，尚未确认", "tone": "wait"}
        else:
            node["delivery_chip"] = {"label": "已留在知识库", "tone": "wait"}

    nodes.sort(key=lambda node: (node["time"], node["id"]), reverse=True)
    truncated = len(nodes) > limit
    visible = nodes[:limit]
    acknowledged = sum(
        1 for node in nodes for item in node["delivery"] if item["state"] == "acknowledged"
    )
    stages = _build_stage_ladder(rows, deliveries)
    return {
        "schema": SCHEMA,
        "scope": {
            "viewer": viewer,
            "knowledge_base": str(root),
            "visible_messages": len(nodes),
            "returned_nodes": len(visible),
            "limit": limit,
            "truncated": truncated,
            "note": "只看你有权看到的协作事实，最新在前。记录在，不等于事情已经办妥。",
            "why": (
                "只投影对该身份可见的 Mail 记录，最新在前；不含 Agent 内部执行过程、"
                "工具调用与 Recall。这里只说「留下了什么协作事实」，不等于对方已办妥。"
            ),
        },
        "latest": visible[0] if visible else None,
        "counts": {
            "primary": sum(1 for node in nodes if node["key_action"]),
            "supporting": sum(1 for node in nodes if not node["key_action"]),
            "cross_session": len(cross_session_ids),
            "acknowledged": acknowledged,
            "participants": len({node["actor"] for node in nodes}),
        },
        "nodes": visible,
        "stages": stages,
        "stages_coherent": _stages_are_monotonic(stages),
        "stages_note": STAGE_LADDER_NOTE,
        "stages_why": STAGE_LADDER_WHY,
    }
