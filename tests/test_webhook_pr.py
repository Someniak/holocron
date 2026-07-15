import os
import time
import hmac
import json
import hashlib
from unittest.mock import patch, MagicMock

import pytest
import requests

from holocron.webhook import build_pr_event_from_payload, start_webhook_server
from holocron.__main__ import start_webhook_listener

SECRET = "s3cr3t"


def sign(secret, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _pr_payload(action="opened", fork=False, number=7):
    head_repo_full = "someoneelse/holocron" if fork else "owner/holocron"
    return {
        "action": action,
        "number": number,
        "repository": {
            "name": "holocron",
            "full_name": "owner/holocron",
            "clone_url": "https://github.com/owner/holocron.git",
        },
        "pull_request": {
            "merged": action == "closed",
            "head": {"sha": "a" * 40, "ref": "feature", "repo": {"full_name": head_repo_full}},
            "base": {"ref": "main"},
        },
    }


# --- build_pr_event_from_payload ---

def test_build_pr_event_basic():
    pr = build_pr_event_from_payload(_pr_payload())
    assert pr.action == "opened"
    assert pr.number == 7
    assert pr.repo_full_name == "owner/holocron"
    assert pr.repo_name == "holocron"
    assert pr.head_sha == "a" * 40
    assert pr.base_ref == "main"
    assert pr.is_fork is False


def test_build_pr_event_detects_fork():
    assert build_pr_event_from_payload(_pr_payload(fork=True)).is_fork is True


def test_build_pr_event_null_head_repo_is_fork():
    payload = _pr_payload()
    payload["pull_request"]["head"]["repo"] = None
    assert build_pr_event_from_payload(payload).is_fork is True


def test_build_pr_event_missing_fields_returns_none():
    assert build_pr_event_from_payload({}) is None
    bad = _pr_payload()
    del bad["pull_request"]["head"]["sha"]
    assert build_pr_event_from_payload(bad) is None


# --- HTTP dispatch ---

@pytest.fixture
def server():
    pushes, prs = [], []
    srv = start_webhook_server(
        port=0, secret=SECRET, on_push=pushes.append,
        path="/webhook", host="127.0.0.1", on_pull_request=prs.append,
    )
    port = srv.server_address[1]
    yield f"http://127.0.0.1:{port}", pushes, prs
    srv.shutdown()
    srv.server_close()


def _post(base, payload, event="pull_request"):
    body = json.dumps(payload).encode()
    return requests.post(
        f"{base}/webhook", data=body,
        headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )


def test_actionable_pr_dispatches_202(server):
    base, _, prs = server
    r = _post(base, _pr_payload("opened"))
    assert r.status_code == 202
    assert len(prs) == 1
    assert prs[0].number == 7


def test_non_actionable_pr_action_returns_204(server):
    base, _, prs = server
    r = _post(base, _pr_payload("labeled"))
    assert r.status_code == 204
    assert prs == []


def test_pr_missing_fields_returns_400(server):
    base, _, prs = server
    r = _post(base, {"action": "opened"})
    assert r.status_code == 400
    assert prs == []


def test_pr_bad_signature_returns_404(server):
    base, _, prs = server
    body = json.dumps(_pr_payload()).encode()
    r = requests.post(
        f"{base}/webhook", data=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign("wrong", body)},
        timeout=5,
    )
    assert r.status_code == 404
    assert prs == []


def test_pr_event_ignored_when_bridge_disabled():
    """With on_pull_request=None, pull_request deliveries are acknowledged (204)
    and dropped — preserving the pre-bridge behavior."""
    srv = start_webhook_server(port=0, secret=SECRET, on_push=lambda r: None,
                               path="/webhook", host="127.0.0.1")
    try:
        port = srv.server_address[1]
        r = _post(f"http://127.0.0.1:{port}", _pr_payload("opened"))
        assert r.status_code == 204
    finally:
        srv.shutdown()
        srv.server_close()


# --- End-to-end through start_webhook_listener into handle_pull_request ---

def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@patch("holocron.__main__.handle_pull_request")
def test_listener_routes_pr_to_bridge(mock_handle):
    config = {
        "concurrency": 2, "storage": "/tmp/holo-ci", "dry_run": False,
        "backup_only": False, "checkout": False,
        "webhook_port": 0, "webhook_path": "/webhook",
        "ci_bridge": True,
    }
    gh, gl = MagicMock(), MagicMock()
    with patch.dict(os.environ, {"HOLOCRON_WEBHOOK_SECRET": SECRET}):
        server = start_webhook_listener(config, MagicMock(), MagicMock(), {},
                                        ci_github_provider=gh, ci_gitlab_provider=gl)
    try:
        port = server.server_address[1]
        r = _post(f"http://127.0.0.1:{port}", _pr_payload("opened"))
        assert r.status_code == 202
        assert _wait_for(lambda: mock_handle.called), "handle_pull_request never called"
        args, _ = mock_handle.call_args
        assert args[0].number == 7          # pr
        assert args[2] is gh                # source provider (clone PR head)
        assert args[3] is gl                # gitlab provider
        assert args[4] is gh                # github provider (status write-back)
    finally:
        server.shutdown()
        server.server_close()
