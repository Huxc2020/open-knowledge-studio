"""HTTP contract tests for the real local Mail workspace."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from knowledge_studio import mail
from knowledge_studio.mail_web import connection_status, create_server


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
            assert b"recipient" in response.read()
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


def test_connection_status_does_not_count_the_unknown_machine_token(kb):
    """``machine_count`` must reflect real machines only.

    A Session with no machine on record, or one whose id literally is
    ``"unknown"``, is not a machine. Counting it inflated the roster the member
    page shows, and disagreed with the provenance loop right below it, which
    already filters the same token.
    """
    mail.register_session(kb, "s-unknown", "writer", machine_id="unknown")
    mail.register_session(kb, "s-real", "reviewer", machine_id="machine-z")
    status = connection_status(kb)
    assert "unknown" not in status["machines"]
    assert status["machines"] == ["machine-z"]
    assert status["machine_count"] == len(status["machines"]) == 1
