"""HTTP contract tests for the real local Mail workspace."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from knowledge_studio import mail
from knowledge_studio.mail_web import create_server


@pytest.fixture
def kb(tmp_path, monkeypatch):
    root = tmp_path / "shared-kb"
    (root / "mail").mkdir(parents=True)
    (root / "wiki").mkdir()
    monkeypatch.setenv("OKS_MACHINE_ID", "web-test-machine")
    return root


def test_web_reads_real_threads_and_rejects_unsafe_requests(kb):
    server = create_server(kb, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(route, payload=None, headers=None):
        req = Request(
            base + route,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        return urlopen(req, timeout=5)

    try:
        with request("/") as response:
            # Phase 1 panel: read-only observation surface (rail nav + timeline + wiki graph).
            body = response.read()
            assert "协作时间线".encode() in body
            assert "Wiki 知识图".encode() in body
            assert b"<form" not in body
        with request("/api/mail/send", {"to": "custom-agent,reviewer", "title": "Hi", "body": "hello"}) as response:
            result = json.load(response)
        assert len(mail.snapshot_data(kb, "custom-agent")["threads"]) == 1

        long_body = "全文" * 3000
        mail.write_message(kb, sender="custom-agent", recipients="human", body=long_body, thread_id=result["thread_id"])
        with request("/api/mail/thread?id=" + result["thread_id"]) as response:
            full = json.load(response)
        assert full["messages"][-1]["body"] == long_body

        mail.write_message(kb, sender="custom-agent", recipients="human", body="reply", thread_id=result["thread_id"])
        with request("/api/mail/reply", {"thread_id": result["thread_id"], "body": "continue"}) as response:
            assert response.status == 201

        for route, payload, headers in [
            ("/api/mail/send", {"to": "../escape", "title": "bad", "body": "bad"}, {}),
            ("/api/mail/send", {"to": "x", "title": "bad", "body": "bad"}, {"Origin": "http://evil.invalid"}),
            ("/api/mail/snapshot", None, {"Host": "evil.invalid"}),
            ("/api/mail/process", {"message_id": "x"}, {}),
        ]:
            with pytest.raises(HTTPError) as error:
                request(route, payload, headers)
            assert error.value.code in {400, 403, 404}
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


# ── 独立 Wiki 页：面向普通读者的显示契约（R6）────────────────────────────
#
# 这一页是「有人点进来才看到」的深页面，最容易漏出维护者字段。
# 三条硬要求：分类值显示人话、维护信息降级进展开说明、不出现内部字段名。

def test_wiki_page_shows_human_labels_and_hides_maintenance_details():
    from knowledge_studio.mail_web import render_wiki_page

    item = {
        "path": "wiki/decision-card-on-blocker.md",
        "kind": "wiki",
        "kind_label": "已审核 Wiki",
        "title": "受阻时就地长出决策卡",
        "summary": "执行流受阻时就地展开候选方案。",
        "body": "## Summary\n\n正文一段。\n",
        "tags": ["collaboration", "checkpoint", "ui"],
        "tag_labels": ["团队与协作", "人类检查点", "界面与交互"],
        "area": "collaboration",
        "area_label": "团队与协作",
        "type": "strategy",
        "type_label": "策略",
        "status": "active",
        "status_label": "可复用",
        "updated_at": "2026-09-19T00:00:00+00:00",
        "timestamp_source": "updated_at",
    }
    page = render_wiki_page(item)

    # 分类值必须是人话
    assert "团队与协作、人类检查点、界面与交互" in page
    assert "治理类型" in page and "策略" in page
    # 原始字段值 / 字段名一个都不许露
    for leaked in ("collaboration", "updated_at", "file_mtime", "frontmatter"):
        assert leaked not in page, leaked
    # 维护信息保留但降级：默认收在展开说明里，且人话化
    assert "文件：wiki/decision-card-on-blocker.md" in page
    assert "时间来源：条目里记录的更新时间" in page
    assert '<details class="why">' in page
    # 点进来的人要有回头路
    assert "← 返回面板" in page


def test_wiki_page_never_invents_tag_labels_for_unknown_english_keys():
    """翻不出来的英文机器键不硬塞给读者；中文标签原样保留。"""
    from knowledge_studio.mail_knowledge import tag_label

    assert tag_label("collaboration") == "团队与协作"
    assert tag_label("checkpoint") == "人类检查点"
    assert tag_label("some_internal_key") == ""
    assert tag_label("自定义主题") == "自定义主题"
    assert tag_label(None) == ""
