import pytest
from unittest.mock import MagicMock, patch

from holocron.providers.github import GitHubProvider
from holocron.utils import is_safe_repo_full_name, is_safe_sha, is_safe_git_ref


# --- set_commit_status ---

@patch("requests.post")
def test_set_commit_status_posts_expected_request(mock_post):
    resp = MagicMock()
    resp.status_code = 201
    mock_post.return_value = resp

    provider = GitHubProvider("tok", "https://api.github.com")
    provider.set_commit_status(
        "owner/repo", "a" * 40, "success", "holocron/gitlab-ci",
        target_url="http://gitlab.local/pipe/1", description="GitLab pipeline success",
    )

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == f"https://api.github.com/repos/owner/repo/statuses/{'a' * 40}"
    assert kwargs["json"]["state"] == "success"
    assert kwargs["json"]["context"] == "holocron/gitlab-ci"
    assert kwargs["json"]["target_url"] == "http://gitlab.local/pipe/1"
    assert kwargs["headers"]["Authorization"] == "token tok"


@patch("requests.post")
def test_set_commit_status_truncates_long_description(mock_post):
    resp = MagicMock(); resp.status_code = 201
    mock_post.return_value = resp
    provider = GitHubProvider("tok")
    provider.set_commit_status("o/r", "b" * 40, "failure", "ctx", description="x" * 500)
    _, kwargs = mock_post.call_args
    assert len(kwargs["json"]["description"]) == 140


@patch("requests.post")
def test_set_commit_status_rejects_bad_state(mock_post):
    provider = GitHubProvider("tok")
    provider.set_commit_status("o/r", "c" * 40, "bogus", "ctx")
    mock_post.assert_not_called()


@patch("requests.post")
def test_set_commit_status_rejects_unsafe_repo_and_sha(mock_post):
    provider = GitHubProvider("tok")
    provider.set_commit_status("../evil", "d" * 40, "success", "ctx")
    provider.set_commit_status("o/r", "not-a-sha", "success", "ctx")
    mock_post.assert_not_called()


@patch("requests.post")
def test_set_commit_status_no_token_is_noop(mock_post):
    provider = GitHubProvider("")
    provider.set_commit_status("o/r", "e" * 40, "success", "ctx")
    mock_post.assert_not_called()


@patch("requests.post")
def test_set_commit_status_http_error_is_non_fatal(mock_post):
    import requests
    resp = MagicMock(); resp.status_code = 403
    err = requests.exceptions.HTTPError(response=resp)
    resp.raise_for_status.side_effect = err
    mock_post.return_value = resp
    provider = GitHubProvider("tok")
    # Must not raise.
    provider.set_commit_status("o/r", "f" * 40, "success", "ctx")


# --- validators ---

def test_is_safe_repo_full_name():
    assert is_safe_repo_full_name("owner/repo")
    assert not is_safe_repo_full_name("owner")
    assert not is_safe_repo_full_name("a/b/c")
    assert not is_safe_repo_full_name("../x")
    assert not is_safe_repo_full_name("o/..")
    assert not is_safe_repo_full_name("")


def test_is_safe_sha():
    assert is_safe_sha("abcdef1")
    assert is_safe_sha("0" * 64)
    assert not is_safe_sha("ABCDEF1")   # uppercase
    assert not is_safe_sha("short")
    assert not is_safe_sha("z" * 40)


def test_is_safe_git_ref():
    assert is_safe_git_ref("main")
    assert is_safe_git_ref("release/1.0")
    assert not is_safe_git_ref("-flag")
    assert not is_safe_git_ref("a..b")
    assert not is_safe_git_ref("a b")
    assert not is_safe_git_ref("a~1")
    assert not is_safe_git_ref("/leading")
    assert not is_safe_git_ref("trailing/")
    assert not is_safe_git_ref("ref@{0}")
