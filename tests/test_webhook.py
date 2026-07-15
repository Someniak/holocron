import os
import time
import hmac
import json
import hashlib
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
import requests

from holocron.webhook import (
    verify_signature,
    build_repo_from_payload,
    start_webhook_server,
)
from holocron.__main__ import start_webhook_listener

SECRET = "s3cr3t"


def sign(secret, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- verify_signature ---

def test_verify_signature_valid():
    body = b'{"a": 1}'
    assert verify_signature(SECRET, body, sign(SECRET, body)) is True


def test_verify_signature_wrong_secret():
    body = b'{"a": 1}'
    assert verify_signature(SECRET, body, sign("other", body)) is False


def test_verify_signature_missing_or_malformed():
    body = b"x"
    assert verify_signature(SECRET, body, "") is False
    assert verify_signature(SECRET, body, "md5=abc") is False
    assert verify_signature("", body, sign(SECRET, body)) is False


# --- build_repo_from_payload ---

def test_build_repo_from_payload_basic():
    payload = {"repository": {"name": "myrepo", "clone_url": "https://github.com/u/myrepo.git", "size": 42}}
    repo = build_repo_from_payload(payload)
    assert repo.name == "myrepo"
    assert repo.clone_url == "https://github.com/u/myrepo.git"
    assert repo.size == 42
    assert repo.pushed_at is not None


def test_build_repo_parses_unix_pushed_at():
    ts = 1700000000
    payload = {"repository": {"name": "r", "clone_url": "https://x/r.git", "pushed_at": ts}}
    repo = build_repo_from_payload(payload)
    expected = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    assert repo.pushed_at == expected


def test_build_repo_missing_fields_returns_none():
    assert build_repo_from_payload({}) is None
    assert build_repo_from_payload({"repository": {"name": "r"}}) is None
    assert build_repo_from_payload({"repository": {"clone_url": "u"}}) is None


# --- HTTP server integration ---

@pytest.fixture
def server():
    received = []
    srv = start_webhook_server(
        port=0,  # ephemeral
        secret=SECRET,
        on_push=received.append,
        path="/webhook",
        host="127.0.0.1",
    )
    port = srv.server_address[1]
    yield f"http://127.0.0.1:{port}", received
    srv.shutdown()
    srv.server_close()


def _push_body():
    return json.dumps({
        "repository": {"name": "hook-repo", "clone_url": "https://github.com/u/hook-repo.git"}
    }).encode()


def test_valid_push_returns_202_and_triggers(server):
    base, received = server
    body = _push_body()
    r = requests.post(
        f"{base}/webhook",
        data=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )
    assert r.status_code == 202
    assert len(received) == 1
    assert received[0].name == "hook-repo"


def test_invalid_signature_returns_401(server):
    base, received = server
    body = _push_body()
    r = requests.post(
        f"{base}/webhook",
        data=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign("wrong", body)},
        timeout=5,
    )
    assert r.status_code == 401
    assert received == []


def test_ping_event_returns_200(server):
    base, received = server
    body = b"{}"
    r = requests.post(
        f"{base}/webhook",
        data=body,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )
    assert r.status_code == 200
    assert received == []


def test_wrong_path_returns_404(server):
    base, received = server
    body = _push_body()
    r = requests.post(
        f"{base}/nope",
        data=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )
    assert r.status_code == 404
    assert received == []


# --- End-to-end: HTTP delivery drives the real sync engine (via __main__) ---

def _wait_for(predicate, timeout=5.0):
    """Polls until predicate() is truthy (sync runs async on a worker thread)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@patch("holocron.__main__.sync_one_repo")
def test_webhook_delivery_triggers_sync_one_repo(mock_sync):
    """A signed push POST should flow through start_webhook_listener into
    sync_one_repo with the payload's repo, and update synced_pushes on success."""
    config = {
        "concurrency": 2,
        "storage": "/tmp/holo-test",
        "dry_run": False,
        "backup_only": False,
        "checkout": False,
        "webhook_port": 0,  # ephemeral
        "webhook_path": "/webhook",
    }
    synced_pushes = {}
    source_provider = MagicMock()
    destination_provider = MagicMock()

    with patch.dict(os.environ, {"HOLOCRON_WEBHOOK_SECRET": SECRET}):
        server = start_webhook_listener(config, source_provider, destination_provider, synced_pushes)

    try:
        port = server.server_address[1]
        body = _push_body()
        r = requests.post(
            f"http://127.0.0.1:{port}/webhook",
            data=body,
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(SECRET, body)},
            timeout=5,
        )
        assert r.status_code == 202

        # Sync runs on a background worker; wait for it to be invoked.
        assert _wait_for(lambda: mock_sync.called), "sync_one_repo was never called"

        _, kwargs = mock_sync.call_args
        assert kwargs["repo"].name == "hook-repo"
        assert kwargs["source_provider"] is source_provider
        assert kwargs["destination_provider"] is destination_provider
        assert kwargs["backup_only"] is False

        # done-callback records the push once the (mocked, successful) sync returns.
        assert _wait_for(lambda: "hook-repo" in synced_pushes)
    finally:
        server.shutdown()
        server.server_close()


@patch("holocron.__main__.sync_one_repo")
def test_webhook_missing_secret_exits(mock_sync):
    """--webhook without HOLOCRON_WEBHOOK_SECRET must refuse to start."""
    config = {"concurrency": 1, "storage": "/tmp/x", "dry_run": False,
              "backup_only": False, "checkout": False,
              "webhook_port": 0, "webhook_path": "/webhook"}
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(SystemExit):
            start_webhook_listener(config, MagicMock(), MagicMock(), {})
