import pytest
from unittest.mock import MagicMock, patch

from holocron import ci_bridge
from holocron.ci_bridge import handle_pull_request, _map_status, _await_pipeline_result
from holocron.providers.base import PullRequestEvent


def _pr(action="opened", is_fork=False, number=7):
    return PullRequestEvent(
        action=action, number=number,
        repo_full_name="owner/holocron", repo_name="holocron",
        clone_url="https://github.com/owner/holocron.git",
        head_sha="a" * 40, head_ref="feature", base_ref="main",
        is_fork=is_fork, merged=(action == "closed"),
    )


CONFIG = {
    "ci_status_context": "holocron/gitlab-ci",
    "ci_branch_prefix": "holocron/pr-",
    "ci_poll_interval": 0,
    "ci_poll_timeout": 1,
    "ci_allow_forks": False,
    "dry_run": False,
}


@pytest.fixture
def providers():
    source = MagicMock()
    source.get_remote_url.return_value = "https://oauth2:x@github.com/owner/holocron.git"
    gitlab = MagicMock()
    github = MagicMock()
    return source, gitlab, github


def _states(github):
    """Ordered list of (state) passed to set_commit_status."""
    return [c.args[2] if len(c.args) > 2 else c.kwargs["state"]
            for c in github.set_commit_status.call_args_list]


# --- status mapping ---

def test_map_status():
    assert _map_status("success") == "success"
    assert _map_status("failed") == "failure"
    assert _map_status("canceled") == "error"
    assert _map_status("manual") == "error"


# --- happy path: pending -> success ---

@patch("holocron.ci_bridge._push_pr_head")
@patch("holocron.ci_bridge._ensure_local_mirror")
def test_happy_path_pending_then_success(mock_mirror, mock_push, providers):
    source, gitlab, github = providers
    gitlab.get_project_id.return_value = 77
    gitlab.create_or_update_merge_request.return_value = {"iid": 5, "web_url": "http://gl/mr/5"}
    gitlab.get_latest_mr_pipeline.return_value = {"id": 1, "status": "success", "web_url": "http://gl/p/1"}

    handle_pull_request(_pr(), "/tmp/store", source, gitlab, github, CONFIG)

    assert _states(github) == ["pending", "success"]
    mock_push.assert_called_once()
    # final status carries the pipeline URL
    last = github.set_commit_status.call_args_list[-1]
    assert last.kwargs["target_url"] == "http://gl/p/1"


@patch("holocron.ci_bridge._push_pr_head")
@patch("holocron.ci_bridge._ensure_local_mirror")
def test_failed_pipeline_maps_to_failure(mock_mirror, mock_push, providers):
    source, gitlab, github = providers
    gitlab.get_project_id.return_value = 77
    gitlab.create_or_update_merge_request.return_value = {"iid": 5, "web_url": "u"}
    gitlab.get_latest_mr_pipeline.return_value = {"id": 1, "status": "failed", "web_url": "u"}

    handle_pull_request(_pr(), "/tmp/store", source, gitlab, github, CONFIG)
    assert _states(github) == ["pending", "failure"]


# --- error paths ---

@patch("holocron.ci_bridge._push_pr_head")
@patch("holocron.ci_bridge._ensure_local_mirror")
def test_project_not_mirrored_sets_error(mock_mirror, mock_push, providers):
    source, gitlab, github = providers
    gitlab.get_project_id.return_value = None

    handle_pull_request(_pr(), "/tmp/store", source, gitlab, github, CONFIG)

    assert _states(github) == ["error"]
    gitlab.create_or_update_merge_request.assert_not_called()
    mock_push.assert_not_called()


def test_fork_denied_sets_error_without_touching_gitlab(providers):
    source, gitlab, github = providers
    handle_pull_request(_pr(is_fork=True), "/tmp/store", source, gitlab, github, CONFIG)
    assert _states(github) == ["error"]
    gitlab.get_project_id.assert_not_called()


@patch("holocron.ci_bridge._push_pr_head")
@patch("holocron.ci_bridge._ensure_local_mirror")
def test_pipeline_never_appears_times_out_to_error(mock_mirror, mock_push, providers):
    source, gitlab, github = providers
    gitlab.get_project_id.return_value = 77
    gitlab.create_or_update_merge_request.return_value = {"iid": 5, "web_url": "u"}
    gitlab.get_latest_mr_pipeline.return_value = None
    gitlab.get_pipeline_for_sha.return_value = None

    cfg = dict(CONFIG, ci_poll_timeout=0)
    handle_pull_request(_pr(), "/tmp/store", source, gitlab, github, cfg)
    assert _states(github) == ["pending", "error"]


@patch("holocron.ci_bridge._push_pr_head")
@patch("holocron.ci_bridge._ensure_local_mirror")
def test_trigger_exception_sets_error(mock_mirror, mock_push, providers):
    source, gitlab, github = providers
    gitlab.get_project_id.return_value = 77
    mock_push.side_effect = RuntimeError("push blew up")

    handle_pull_request(_pr(), "/tmp/store", source, gitlab, github, CONFIG)
    assert _states(github) == ["error"]


# --- closed PR: tears down MR, no poll ---

def test_closed_pr_closes_mr_and_skips_status(providers):
    source, gitlab, github = providers
    gitlab.get_project_id.return_value = 77

    handle_pull_request(_pr(action="closed"), "/tmp/store", source, gitlab, github, CONFIG)

    gitlab.close_merge_request.assert_called_once_with(77, "holocron/pr-7")
    gitlab.delete_branch.assert_called_once_with(77, "holocron/pr-7")
    github.set_commit_status.assert_not_called()


# --- dry run: no side effects ---

@patch("holocron.ci_bridge._push_pr_head")
@patch("holocron.ci_bridge._ensure_local_mirror")
def test_dry_run_makes_no_calls(mock_mirror, mock_push, providers):
    source, gitlab, github = providers
    handle_pull_request(_pr(), "/tmp/store", source, gitlab, github, dict(CONFIG, dry_run=True))
    mock_push.assert_not_called()
    github.set_commit_status.assert_not_called()
    gitlab.get_project_id.assert_not_called()


# --- unsafe input is refused before any side effect ---

def test_unsafe_sha_refused(providers):
    source, gitlab, github = providers
    bad = _pr()
    bad.head_sha = "not-hex"
    handle_pull_request(bad, "/tmp/store", source, gitlab, github, CONFIG)
    github.set_commit_status.assert_not_called()
    gitlab.get_project_id.assert_not_called()


# --- _await_pipeline_result directly ---

def test_await_polls_until_terminal():
    gl = MagicMock()
    gl.get_latest_mr_pipeline.return_value = {"id": 1, "status": "running", "web_url": "u"}
    # first status poll running, then success
    gl.get_pipeline_status.side_effect = [
        {"id": 1, "status": "running", "web_url": "u"},
        {"id": 1, "status": "success", "web_url": "u2"},
    ]
    state, url, _ = _await_pipeline_result(gl, 77, 5, "a" * 40, interval=0, timeout=5)
    assert state == "success"
    assert url == "u2"
