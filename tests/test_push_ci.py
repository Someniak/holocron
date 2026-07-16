import os
import time
import hmac
import json
import hashlib
from unittest.mock import patch, MagicMock

import pytest
import requests

from holocron.webhook import build_push_event_from_payload, start_webhook_server
from holocron.__main__ import start_webhook_listener
from holocron.ci_bridge import handle_push_ci
from holocron.providers.base import PushEvent

SECRET = "s3cr3t"


def sign(secret, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _push_payload(ref="refs/heads/feature", after="a" * 40, deleted=False):
    return {
        "ref": ref, "after": after, "deleted": deleted,
        "repository": {
            "name": "holocron", "full_name": "owner/holocron",
            "clone_url": "https://github.com/owner/holocron.git",
        },
    }


# --- build_push_event_from_payload ---

def test_build_push_event_branch():
    pe = build_push_event_from_payload(_push_payload())
    assert pe.branch == "feature"
    assert pe.after == "a" * 40
    assert pe.repo_full_name == "owner/holocron"
    assert pe.deleted is False


def test_build_push_event_ignores_tags():
    assert build_push_event_from_payload(_push_payload(ref="refs/tags/v1")) is None


def test_build_push_event_detects_deletion():
    pe = build_push_event_from_payload(_push_payload(after="0" * 40))
    assert pe.deleted is True
    pe2 = build_push_event_from_payload(_push_payload(deleted=True))
    assert pe2.deleted is True


def test_build_push_event_missing_fields_returns_none():
    bad = _push_payload()
    del bad["after"]
    assert build_push_event_from_payload(bad) is None


# --- handle_push_ci ---

def _pe():
    return PushEvent(repo_full_name="owner/holocron", repo_name="holocron",
                     clone_url="https://github.com/owner/holocron.git",
                     branch="feature", after="a" * 40)


CONFIG = {"ci_status_context": "holocron/gitlab-ci", "ci_poll_interval": 0,
          "ci_poll_timeout": 1, "dry_run": False}


@pytest.fixture
def providers():
    source = MagicMock()
    source.get_remote_url.return_value = "https://oauth2:x@github.com/owner/holocron.git"
    return source, MagicMock(), MagicMock()


def _states(github):
    return [c.args[2] if len(c.args) > 2 else c.kwargs["state"]
            for c in github.set_commit_status.call_args_list]


@patch("holocron.ci_bridge._push_branch")
@patch("holocron.ci_bridge._ensure_local_mirror")
def test_push_ci_happy_path(mock_mirror, mock_push, providers):
    source, gitlab, github = providers
    gitlab.get_project_id.return_value = 77
    gitlab.get_pipeline_for_sha.return_value = {"id": 1, "status": "success", "web_url": "http://gl/p/1"}

    handle_push_ci(_pe(), "/tmp/store", source, gitlab, github, CONFIG)

    assert _states(github) == ["pending", "success"]
    mock_push.assert_called_once()
    # never consults MR pipelines in push mode
    gitlab.get_latest_mr_pipeline.assert_not_called()
    last = github.set_commit_status.call_args_list[-1]
    assert last.kwargs["target_url"] == "http://gl/p/1"


@patch("holocron.ci_bridge._push_branch")
@patch("holocron.ci_bridge._ensure_local_mirror")
def test_push_ci_failed_pipeline(mock_mirror, mock_push, providers):
    source, gitlab, github = providers
    gitlab.get_project_id.return_value = 77
    gitlab.get_pipeline_for_sha.return_value = {"id": 1, "status": "failed", "web_url": "u"}
    handle_push_ci(_pe(), "/tmp/store", source, gitlab, github, CONFIG)
    assert _states(github) == ["pending", "failure"]


@patch("holocron.ci_bridge._push_branch")
@patch("holocron.ci_bridge._ensure_local_mirror")
def test_push_ci_project_missing(mock_mirror, mock_push, providers):
    source, gitlab, github = providers
    gitlab.get_project_id.return_value = None
    handle_push_ci(_pe(), "/tmp/store", source, gitlab, github, CONFIG)
    assert _states(github) == ["error"]
    mock_push.assert_not_called()


def test_push_ci_dry_run_no_calls(providers):
    source, gitlab, github = providers
    handle_push_ci(_pe(), "/tmp/store", source, gitlab, github, dict(CONFIG, dry_run=True))
    github.set_commit_status.assert_not_called()
    gitlab.get_project_id.assert_not_called()


def test_push_ci_unsafe_branch_refused(providers):
    source, gitlab, github = providers
    bad = _pe(); bad.branch = "bad ref"
    handle_push_ci(bad, "/tmp/store", source, gitlab, github, CONFIG)
    github.set_commit_status.assert_not_called()
    gitlab.get_project_id.assert_not_called()


# --- webhook dispatch ---

@pytest.fixture
def server():
    pushes, cis = [], []
    srv = start_webhook_server(port=0, secret=SECRET, on_push=pushes.append,
                               path="/webhook", host="127.0.0.1", on_push_ci=cis.append)
    port = srv.server_address[1]
    yield f"http://127.0.0.1:{port}", pushes, cis
    srv.shutdown()
    srv.server_close()


def _post(base, payload):
    body = json.dumps(payload).encode()
    return requests.post(f"{base}/webhook", data=body,
                         headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(SECRET, body)},
                         timeout=5)


def test_branch_push_dispatches_both_sync_and_ci(server):
    base, pushes, cis = server
    r = _post(base, _push_payload())
    assert r.status_code == 202
    assert len(pushes) == 1        # mirror sync still happens
    assert len(cis) == 1
    assert cis[0].branch == "feature"


def test_branch_delete_syncs_but_no_ci(server):
    base, pushes, cis = server
    r = _post(base, _push_payload(after="0" * 40))
    assert r.status_code == 202
    assert len(pushes) == 1
    assert cis == []               # nothing to test on a deletion


def test_tag_push_syncs_but_no_ci(server):
    base, pushes, cis = server
    r = _post(base, _push_payload(ref="refs/tags/v1"))
    assert r.status_code == 202
    assert len(pushes) == 1
    assert cis == []


# --- end-to-end through the listener ---

def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@patch("holocron.__main__.handle_push_ci")
@patch("holocron.__main__.sync_one_repo")
def test_listener_routes_push_to_ci(mock_sync, mock_handle):
    config = {"concurrency": 2, "storage": "/tmp/holo-pci", "dry_run": False,
              "backup_only": False, "checkout": False, "webhook_port": 0,
              "webhook_path": "/webhook", "ci_on_push": True}
    gh, gl = MagicMock(), MagicMock()
    with patch.dict(os.environ, {"HOLOCRON_WEBHOOK_SECRET": SECRET}):
        server = start_webhook_listener(config, MagicMock(), MagicMock(), {},
                                        ci_github_provider=gh, ci_gitlab_provider=gl)
    try:
        port = server.server_address[1]
        r = _post(f"http://127.0.0.1:{port}", _push_payload())
        assert r.status_code == 202
        assert _wait_for(lambda: mock_handle.called), "handle_push_ci never called"
        args, _ = mock_handle.call_args
        assert args[0].branch == "feature"
        assert args[3] is gl
        assert args[4] is gh
    finally:
        server.shutdown()
        server.server_close()
