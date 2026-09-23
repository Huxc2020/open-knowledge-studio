"""Contract tests for the human timeline projection.

The point of these tests is honesty, not formatting: protocol fields must not
leak into the default reading path, and ``acknowledged`` must never be readable
as "done".
"""
import pytest

from knowledge_studio import mail, mail_timeline


@pytest.fixture
def kb(tmp_path, monkeypatch):
    root = tmp_path / "kb"
    (root / "mail").mkdir(parents=True)
    (root / "wiki").mkdir()
    monkeypatch.setenv("OKS_MACHINE_ID", "timeline-test-machine")
    return root


def send(root, **kwargs):
    kwargs.setdefault("sender", "human")
    kwargs.setdefault("sender_kind", "human")
    kwargs.setdefault("origin_machine_id", "timeline-test-machine")
    return mail.write_message(root, **kwargs)


def find(projection, message_id):
    return next(node for node in projection["nodes"] if node["id"] == message_id)


def test_action_sentences_replace_protocol_vocabulary(kb):
    mail.register_session(kb, "sess-codex", "codex")
    handoff = send(kb, recipients=["@codex"], title="把面板做完", body="继续实现", delivery_reason="handoff", record_kind="handoff")
    review = send(kb, recipients=["@judge-agent"], title="复核口径", body="请确认", delivery_reason="review_request", record_kind="knowledge_ref")
    conflict = send(kb, recipients=["@writer-agent"], title="同一文件被改动", body="冲突", delivery_reason="conflict", record_kind="note")
    result = send(kb, recipients=["@human"], title="已完成初稿", body="写完了", delivery_reason="direct", record_kind="result")
    system = send(kb, recipients=["@all"], title="Wiki 已更新", body="已入库", delivery_reason="system", record_kind="note")

    projection = mail_timeline.timeline_data(kb)

    assert find(projection, handoff["message_id"])["action"] == "把这件事交给 codex 继续"
    assert find(projection, review["message_id"])["action"] == "请 judge-agent 查看并审核"
    assert find(projection, conflict["message_id"])["action"] == "发现文件冲突，提醒 writer-agent 一起确认"
    assert find(projection, result["message_id"])["action"] == "报告了一项结果"
    assert find(projection, system["message_id"])["action"] == "系统记录了这次协作的事实"

    # The default reading path carries no protocol vocabulary at all.
    for node in projection["nodes"]:
        for field in ("action", "result", "excerpt"):
            for token in ("handoff", "review_request", "thread_reply", "conflict", "system", "record_kind", "delivery_reason"):
                assert token not in node[field], (field, token, node[field])


def test_protocol_fields_are_kept_for_expansion_only(kb):
    sent = send(kb, recipients=["@codex"], title="复核口径", body="请确认", delivery_reason="review_request", record_kind="knowledge_ref")
    node = find(mail_timeline.timeline_data(kb), sent["message_id"])

    assert node["protocol"]["message_id"] == sent["message_id"]
    assert node["protocol"]["delivery_reason"] == "review_request"
    assert node["protocol"]["record_kind"] == "knowledge_ref"
    assert node["protocol"]["delivery_reason_label"] == "请求审核"


def test_acknowledged_is_never_phrased_as_completion(kb):
    sent = send(kb, recipients=["@research-agent"], title="请确认收到", body="只是确认读到")
    message = next(item for item in mail.iter_messages(kb, "@research-agent") if item["meta"]["message_id"] == sent["message_id"])
    mail.register_session(kb, "sess-a", "research-agent")
    mail.record_delivery(kb, "sess-a", message, agent_id="@research-agent")
    mail.acknowledge_delivery(kb, "sess-a", sent["message_id"], agent_id="@research-agent")

    node = find(mail_timeline.timeline_data(kb), sent["message_id"])
    state = node["delivery"][0]
    assert state["state"] == "acknowledged"
    assert state["state_label"] == "已确认收到（不代表完成）"
    assert "完成" not in mail_timeline.STATE_LABELS["read"]


def test_presentation_and_acknowledgement_stay_distinct(kb):
    sent = send(kb, recipients=["@writer-agent"], title="验收标准", body="请确认")
    message = next(item for item in mail.iter_messages(kb, "@writer-agent") if item["meta"]["message_id"] == sent["message_id"])
    mail.register_session(kb, "sess-w", "writer-agent")
    mail.record_delivery(kb, "sess-w", message, agent_id="@writer-agent")

    node = find(mail_timeline.timeline_data(kb), sent["message_id"])
    assert node["delivery"][0]["state"] == "notified"
    # injected and delivered share one instant for a delivered message, so the
    # stronger fact stands alone and records what it absorbed.
    steps = node["delivery"][0]["steps"]
    assert [step["step"] for step in steps] == ["presented"]
    assert steps[0]["supersedes"] == "injected"
    assert node["sessions"] == ["sess-w"]


def test_cross_session_continuation_is_flagged(kb):
    first = send(kb, recipients=["@human"], title="第一段", body="开始", origin_session_id="sess-1")
    second = send(
        kb,
        recipients=["@human"],
        title="Re: 第一段",
        body="换了个 Session 继续",
        thread_id=first["thread_id"],
        reply_to=first["message_id"],
        delivery_reason="thread_reply",
        origin_session_id="sess-2",
    )

    node = find(mail_timeline.timeline_data(kb), second["message_id"])
    assert node["cross_session"] is True
    assert node["action"] == "换了一个 Session 继续这段协作"
    assert node["key_action"] is True


def test_projection_is_bounded_and_says_so(kb):
    for index in range(5):
        send(kb, recipients=["@human"], title=f"记录 {index}", body="内容")

    projection = mail_timeline.timeline_data(kb, limit=2)
    assert projection["scope"]["truncated"] is True
    assert projection["scope"]["visible_messages"] == 5
    assert projection["scope"]["returned_nodes"] == 2
    assert len(projection["nodes"]) == 2
    # 默认阅读路径上只留一句话；完整口径收进 why（展开说明用）。
    assert projection["scope"]["note"]
    assert "不等于对方已办妥" in projection["scope"]["why"]


def test_limit_is_clamped(kb):
    send(kb, recipients=["@human"], title="记录", body="内容")
    assert mail_timeline.timeline_data(kb, limit=10 ** 6)["scope"]["limit"] == mail_timeline.MAX_LIMIT
    assert mail_timeline.timeline_data(kb, limit=0)["scope"]["limit"] == 1


def test_latest_node_summarises_where_the_collaboration_stands(kb):
    send(kb, recipients=["@human"], title="早的记录", body="一")
    send(kb, recipients=["@human"], title="晚的记录", body="二")

    projection = mail_timeline.timeline_data(kb)
    assert projection["latest"]["result"] == "晚的记录"
    assert projection["nodes"][0]["id"] == projection["latest"]["id"]


def test_empty_knowledge_base_is_an_honest_empty_state(kb):
    projection = mail_timeline.timeline_data(kb)
    assert projection["nodes"] == []
    assert projection["latest"] is None
    assert projection["counts"]["participants"] == 0


def _stages(projection):
    return {stage["key"]: stage for stage in projection["stages"]}


def test_stage_ladder_always_renders_four_stages(kb):
    projection = mail_timeline.timeline_data(kb)
    assert [stage["key"] for stage in projection["stages"]] == [
        "extract", "extract_done", "draft_review", "stage_complete",
    ]
    # Existing fields must survive untouched for the in-flight frontend.
    assert projection["nodes"] == []
    assert "counts" in projection and "scope" in projection
    assert projection["stages_note"]


def test_empty_kb_renders_all_stages_pending_without_fabricated_time(kb):
    stages = _stages(mail_timeline.timeline_data(kb))
    for stage in stages.values():
        assert stage["status"] == "pending"
        assert stage["at"] == ""  # never a fabricated timestamp
        # Nothing reached at all -> no basis to claim any step.
        assert stage["pending_reason"] == "no_evidence"
        assert stage["evidence"] == []


def test_stage_advances_one_step_at_a_time(kb):
    # ① human requirement
    req = send(kb, recipients=["@codex"], title="把面板做完", body="需求：提取面板")
    # ② extraction finished via an agent handoff
    handoff = send(kb, recipients=["@human"], title="开始处理", body="我接手", sender="@codex", sender_kind="agent", delivery_reason="handoff", record_kind="handoff")
    # ③ draft submitted for review
    review = send(kb, recipients=["@human"], title="请审核 Draft", body="请看", sender="@codex", sender_kind="agent", delivery_reason="review_request", record_kind="knowledge_ref")
    # ④ stage complete via a system wiki update
    wiki = mail.write_message(
        kb, body="知识库已更新", sender="human", sender_kind="human",
        recipients=["@codex"], title="Wiki 已更新", delivery_reason="system",
        record_kind="note", evidence_refs=[{"type": "wiki", "path": "wiki/panel.md"}],
    )

    stages = _stages(mail_timeline.timeline_data(kb))
    assert stages["extract"]["status"] == "done"
    assert stages["extract_done"]["status"] == "done"
    assert stages["draft_review"]["status"] == "done"
    assert stages["stage_complete"]["status"] == "done"

    # `done` timestamps come from the real event, not datetime.now().
    assert stages["extract"]["at"] == req["meta"]["timestamp"]
    assert stages["extract_done"]["at"] == handoff["meta"]["timestamp"]
    assert stages["draft_review"]["at"] == review["meta"]["timestamp"]
    assert stages["stage_complete"]["at"] == wiki["meta"]["timestamp"]


def test_done_stage_carries_time_pending_does_not(kb):
    # Only a human requirement exists: ① done, the rest pending.
    send(kb, recipients=["@codex"], title="需求", body="提取面板")
    stages = _stages(mail_timeline.timeline_data(kb))
    assert stages["extract"]["status"] == "done"
    assert stages["extract"]["at"]  # non-empty real time
    for key in ("extract_done", "draft_review", "stage_complete"):
        assert stages[key]["status"] == "pending"
        assert stages[key]["at"] == ""


def test_active_stage_reports_latest_evidence_time(kb):
    # Human requirement, then an agent result but no review request yet.
    send(kb, recipients=["@codex"], title="需求", body="提取面板")
    result = send(kb, recipients=["@human"], title="初稿完成", body="Draft 在此", sender="@codex", sender_kind="agent", delivery_reason="direct", record_kind="result")

    stages = _stages(mail_timeline.timeline_data(kb))
    # ③ entered (draft exists) but not submitted for review -> active.
    assert stages["draft_review"]["status"] == "active"
    assert stages["draft_review"]["at"] == result["meta"]["timestamp"]
    # ④ entered by the result, no wiki update yet -> active.
    assert stages["stage_complete"]["status"] == "active"
    assert stages["stage_complete"]["at"] == result["meta"]["timestamp"]


def test_stage_evidence_is_traceable_to_real_events(kb):
    req = send(kb, recipients=["@codex"], title="需求", body="提取面板")
    stages = _stages(mail_timeline.timeline_data(kb))
    extract = stages["extract"]
    assert extract["evidence"]
    hit = next(item for item in extract["evidence"] if item["message_id"] == req["message_id"])
    assert hit["at"] == req["meta"]["timestamp"]
    assert hit["message_id"] == req["message_id"]  # traceable back to the real event
    assert hit["summary"]  # plain-language, not raw protocol


def test_acknowledgement_does_not_falsely_complete_the_stage(kb):
    # A result that is acknowledged must NOT mark stage_complete done: the
    # schema's `acknowledged` only means "received in one Session", never done.
    # The result is delivered to both @human (so it is visible to the viewer and
    # enters stage ④) and @research-agent (so it can be acknowledged).
    send(kb, recipients=["@codex"], title="需求", body="提取面板")
    result = send(kb, recipients=["@human", "@research-agent"], title="初稿", body="完成", sender="@codex", sender_kind="agent", record_kind="result")
    message = next(item for item in mail.iter_messages(kb, "@research-agent") if item["meta"]["message_id"] == result["message_id"])
    mail.register_session(kb, "sess-r", "research-agent")
    mail.record_delivery(kb, "sess-r", message, agent_id="@research-agent")
    mail.acknowledge_delivery(kb, "sess-r", result["message_id"], agent_id="@research-agent")

    stages = _stages(mail_timeline.timeline_data(kb))
    # The ack must not promote stage_complete to done. It may only sit as
    # `active` (entered by the result) at most; never `done`.
    assert stages["stage_complete"]["status"] != "done"
    # Any `at` comes from the real result event, never from the ack receipt.
    if stages["stage_complete"]["at"]:
        assert stages["stage_complete"]["at"] == result["meta"]["timestamp"]


def test_receipts_are_not_counted_as_key_collaboration(kb):
    """「已确认收到」是送达凭据，不是推进了协作的事实。

    Regression guard: synthetic ack nodes used to carry key_action=True, so the
    default 「关键协作」 view was flooded with near-identical receipt rows and a
    newcomer could not see what actually moved.
    """
    send(kb, recipients=["@codex"], title="需求", body="提取面板")
    result = send(kb, recipients=["@human", "@research-agent"], title="初稿", body="完成",
                  sender="@codex", sender_kind="agent", record_kind="result")
    message = next(item for item in mail.iter_messages(kb, "@research-agent")
                   if item["meta"]["message_id"] == result["message_id"])
    mail.register_session(kb, "sess-ack", "research-agent")
    mail.record_delivery(kb, "sess-ack", message, agent_id="@research-agent")
    mail.acknowledge_delivery(kb, "sess-ack", result["message_id"], agent_id="@research-agent")

    projection = mail_timeline.timeline_data(kb)
    acks = [node for node in projection["nodes"] if node["action_key"] == "ack"]
    assert acks, "回执节点仍应存在，只是不占关键位"
    for node in acks:
        assert node["key_action"] is False
    # 仍然算得进「已确认收到」这个数字，也仍然能被该筛选找到。
    assert projection["counts"]["acknowledged"] >= 1
    assert projection["counts"]["primary"] == len(
        [node for node in projection["nodes"] if node["key_action"]]
    )


def test_stage_stamps_stay_monotonic_when_threads_overlap(kb):
    """A knowledge_ref older than the first human record must not drag ③ ahead of ①.

    Regression guard for the out-of-order ladder: with more than one Thread in
    the knowledge base, letting every stage pick its own earliest match produced
    提取(晚) → 提取完成(晚) → Draft 待审核(早) → 阶段完毕, i.e. a pipeline that ran
    backwards. The forward-only cursor must reject the older review request.
    """
    # An agent drops a review request before any human record exists at all.
    early_review = send(
        kb, recipients=["@human"], title="先扔一份复核", body="先看看",
        sender="@codex", sender_kind="agent",
        delivery_reason="review_request", record_kind="knowledge_ref",
    )
    requirement = send(kb, recipients=["@codex"], title="需求", body="提取面板")
    later_review = send(
        kb, recipients=["@human"], title="请审核 Draft", body="请看",
        sender="@codex", sender_kind="agent",
        delivery_reason="review_request", record_kind="knowledge_ref",
    )

    projection = mail_timeline.timeline_data(kb)
    stages = _stages(projection)

    stamps = [
        stages[key]["at"]
        for key in ("extract", "extract_done", "draft_review", "stage_complete")
        if stages[key]["at"]
    ]
    assert stamps == sorted(stamps), stamps
    assert projection["stages_coherent"] is True

    # ③ is stamped by a review that really came after the human requirement.
    assert stages["draft_review"]["at"] == later_review["meta"]["timestamp"]
    assert stages["draft_review"]["at"] != early_review["meta"]["timestamp"]
    assert stages["draft_review"]["at"] >= requirement["meta"]["timestamp"]


def test_stage_evidence_names_the_thread_it_came_from(kb):
    send(kb, recipients=["@codex"], title="需求", body="提取面板")
    hit = _stages(mail_timeline.timeline_data(kb))["extract"]["evidence"][0]
    assert hit["message_id"]  # traceable back to the real event
    assert hit["thread"]  # ...and to the Thread it belongs to


def test_stage_evidence_leads_with_the_event_that_stamped_it(kb):
    """The first evidence item must be the event the stamp came from.

    A stage is stamped with the *earliest* matching event; listing evidence
    newest-first puts newer matches on top, so the line reads as if the stage
    were stamped by an event that came later. Lead with the real cause.
    """
    stamp = send(kb, recipients=["@codex"], title="需求", body="提取面板")
    send(kb, recipients=["@human"], title="接单", body="我来",
         sender="@codex", sender_kind="agent",
         delivery_reason="handoff", record_kind="handoff")
    send(kb, recipients=["@human"], title="再要一份", body="还有个需求")

    extract = _stages(mail_timeline.timeline_data(kb))["extract"]
    assert extract["status"] == "done"
    assert extract["at"] == stamp["meta"]["timestamp"]
    assert extract["evidence"][0]["at"] == extract["at"]
    assert extract["evidence"][0]["message_id"] == stamp["message_id"]


def _raw_event(message_id, timestamp, **meta):
    """One row shaped like `enriched` expects, so the ladder can be driven
    directly with edge-case timestamps that the real writer never emits."""
    base = {
        "message_id": message_id,
        "timestamp": timestamp,
        "sender_kind": "human",
        "record_kind": "message",
        "delivery_reason": "direct",
        "thread_id": "th-raw",
        "to": ["@codex"],
    }
    base.update(meta)
    return {"meta": base, "title": message_id}


def test_done_stage_never_borrows_an_empty_timestamp(kb):
    """An untimed event must not stamp a finished stage, nor freeze the cursor.

    Regression guard: `min()` over every matching event let "" win, because the
    empty string sorts before any real timestamp. A stage that really finished at
    T read as "done, no time", and since the forward-only cursor advances on a
    real time only, it stayed put — so the next stage was free to pick an event
    *older* than T and the ladder ran backwards.
    """
    early = "2026-09-21T09:00:00+08:00"
    stamped = "2026-09-21T10:00:00+08:00"
    later = "2026-09-21T11:00:00+08:00"

    ladder = mail_timeline._build_stage_ladder(
        [
            _raw_event("m-untimed", ""),
            _raw_event("m-req", stamped),
            _raw_event("m-h-early", early, sender_kind="agent",
                       record_kind="handoff", delivery_reason="handoff"),
            _raw_event("m-h-late", later, sender_kind="agent",
                       record_kind="handoff", delivery_reason="handoff"),
        ],
        {},
    )
    stages = {stage["key"]: stage for stage in ladder}

    # ① is stamped by the real event, not by the untimed one that sorted first.
    assert stages["extract"]["status"] == "done"
    assert stages["extract"]["at"] == stamped
    # ...and because the cursor really moved, ② cannot reach back to the older
    # handoff: only the later one survives the forward-only walk.
    assert stages["extract_done"]["at"] == later
    assert stages["extract_done"]["at"] > stages["extract"]["at"]


def test_same_instant_steps_follow_lifecycle_order(kb):
    """Steps sharing an instant read in lifecycle order, not alphabetical order.

    Regression guard: the sort key was ``(at, step)``, so ``acknowledged`` fell
    before ``presented`` and the confirmation rendered above the delivery it
    confirms — the reader sees the receipt as the cause.
    """
    stamp = "2026-09-21T10:00:00+08:00"
    rows = [{"meta": {"message_id": "m-1", "timestamp": stamp, "to": ["@codex"]},
             "title": "需求"}]
    deliveries = {
        "m-1": [{
            "agent_id": "@codex",
            "sessions": [{
                "session_id": "s-1",
                "machine_id": "mach-1",
                "injected_at": "",
                "delivered_at": stamp,
                "acknowledged_at": stamp,
            }],
        }],
    }

    steps = mail_timeline._delivery_evidence(kb, rows, deliveries)["m-1"][0]["steps"]
    assert [step["step"] for step in steps] == ["presented", "acknowledged"]


def test_ack_node_lists_each_session_once(kb):
    """The ack node's roster is a set, not a tally of steps.

    Regression guard: one Session contributes several steps (delivered, then
    acknowledged), so listing a session per step repeated the same id. The rest
    of this module already treats the roster as a set; the ack node did not.
    """
    send(kb, recipients=["@human", "@research-agent"], title="初稿", body="完成",
         sender="@codex", sender_kind="agent", record_kind="result")
    message = next(item for item in mail.iter_messages(kb, "@research-agent")
                   if item["meta"].get("record_kind") == "result")
    mail.register_session(kb, "sess-dup", "research-agent", machine_id="mach-dup")
    mail.record_delivery(kb, "sess-dup", message, agent_id="@research-agent")
    mail.acknowledge_delivery(kb, "sess-dup", message["meta"]["message_id"],
                              agent_id="@research-agent")

    ack = next(node for node in mail_timeline.timeline_data(kb)["nodes"]
               if node["action_key"] == "ack")
    assert ack["sessions"] == ["sess-dup"]
    # The Session is stamped with the machine from the fixture environment, and
    # it must appear once even though two steps (delivered + acknowledged) carry it.
    assert ack["machines"] == ["timeline-test-machine"]

