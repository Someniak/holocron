import pytest
from unittest.mock import MagicMock, patch
from holocron.providers.gitlab import GitLabProvider
from holocron.providers.base import Repository


@pytest.fixture
def repo():
    # full_name is the GitHub owner/repo the mirror came from.
    return Repository(
        name="test-repo",
        clone_url="http://src/test-repo.git",
        full_name="acme/test-repo",
    )


def _project_and_unprotected_gets():
    """Standard GET side_effect: project found, default branch not protected."""
    project_resp = MagicMock()
    project_resp.status_code = 200
    project_resp.json.return_value = {"id": 123, "default_branch": "main"}

    prot_resp = MagicMock()
    prot_resp.status_code = 404  # not protected -> no patch path
    return [project_resp, prot_resp]


@patch("requests.post")
@patch("requests.put")
@patch("requests.get")
def test_provisions_variable_via_put_when_it_exists(mock_get, mock_put, mock_post, repo):
    provider = GitLabProvider("http://gitlab.com", "token", provision_status_var=True)
    mock_get.side_effect = _project_and_unprotected_gets()

    put_resp = MagicMock()
    put_resp.status_code = 200
    mock_put.return_value = put_resp

    provider.prepare_push(repo)

    mock_put.assert_called_once()
    args, kwargs = mock_put.call_args
    assert "projects/123/variables/GITHUB_REPO" in args[0]
    assert kwargs["json"] == {"value": "acme/test-repo"}
    mock_post.assert_not_called()


@patch("requests.post")
@patch("requests.put")
@patch("requests.get")
def test_creates_variable_via_post_on_404(mock_get, mock_put, mock_post, repo):
    provider = GitLabProvider("http://gitlab.com", "token", provision_status_var=True)
    mock_get.side_effect = _project_and_unprotected_gets()

    put_resp = MagicMock()
    put_resp.status_code = 404  # variable doesn't exist yet
    mock_put.return_value = put_resp

    post_resp = MagicMock()
    post_resp.status_code = 201
    mock_post.return_value = post_resp

    provider.prepare_push(repo)

    mock_put.assert_called_once()
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0].endswith("projects/123/variables")
    assert kwargs["json"] == {
        "key": "GITHUB_REPO",
        "value": "acme/test-repo",
        "masked": False,
        "protected": False,
    }


@patch("requests.post")
@patch("requests.put")
@patch("requests.get")
def test_no_provisioning_when_flag_disabled(mock_get, mock_put, mock_post, repo):
    provider = GitLabProvider("http://gitlab.com", "token")  # default: disabled
    mock_get.side_effect = _project_and_unprotected_gets()

    provider.prepare_push(repo)

    mock_put.assert_not_called()
    mock_post.assert_not_called()


@patch("requests.post")
@patch("requests.put")
@patch("requests.get")
def test_no_provisioning_when_full_name_missing(mock_get, mock_put, mock_post):
    provider = GitLabProvider("http://gitlab.com", "token", provision_status_var=True)
    mock_get.side_effect = _project_and_unprotected_gets()

    repo_no_full = Repository(name="test-repo", clone_url="http://src/test-repo.git")
    provider.prepare_push(repo_no_full)

    mock_put.assert_not_called()
    mock_post.assert_not_called()
