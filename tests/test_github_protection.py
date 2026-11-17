
import pytest
from unittest.mock import MagicMock, patch
from holocron.providers.github import GitHubProvider
from holocron.providers.base import Repository

@pytest.fixture
def repo():
    return Repository(name="owner/repo", clone_url="url")

@patch("requests.get")
@patch("requests.put")
def test_github_prepare_push_enables_force(mock_put, mock_get, repo):
    provider = GitHubProvider("token")
    
    # 1. Get Repo -> Success
    repo_resp = MagicMock()
    repo_resp.status_code = 200
    repo_resp.json.return_value = {"default_branch": "main"}
    
    # 2. Get Protection -> Success, allow_force_pushes=False
    prot_resp = MagicMock()
    prot_resp.status_code = 200
    prot_resp.json.return_value = {
        "required_status_checks": {"strict": True, "contexts": []},
        "enforce_admins": {"enabled": False},
        "allow_force_pushes": {"enabled": False},
        "required_pull_request_reviews": None,
        "restrictions": None
    }
    
    # 3. Put -> Success
    put_resp = MagicMock()
    put_resp.status_code = 200
    
    mock_get.side_effect = [repo_resp, prot_resp]
    mock_put.return_value = put_resp
    
    provider.prepare_push(repo)
    
    args, kwargs = mock_put.call_args
    assert "branches/main/protection" in args[0]
    assert kwargs['json']['allow_force_pushes'] is True
    assert kwargs['json']['enforce_admins'] is False

@patch("requests.get")
@patch("requests.put")
def test_github_prepare_push_already_enabled(mock_put, mock_get, repo):
    provider = GitHubProvider("token")
    
    repo_resp = MagicMock()
    repo_resp.status_code = 200
    repo_resp.json.return_value = {"default_branch": "main"}
    
    prot_resp = MagicMock()
    prot_resp.status_code = 200
    prot_resp.json.return_value = {
        "allow_force_pushes": {"enabled": True}
    }
    
    mock_get.side_effect = [repo_resp, prot_resp]
    
    provider.prepare_push(repo)
    
    mock_put.assert_not_called()

@patch("requests.get")
@patch("requests.put")
def test_github_prepare_push_not_protected(mock_put, mock_get, repo):
    provider = GitHubProvider("token")
    
    repo_resp = MagicMock()
    repo_resp.status_code = 200
    repo_resp.json.return_value = {"default_branch": "main"}
    
    prot_resp = MagicMock()
    prot_resp.status_code = 404 # Not protected
    
    mock_get.side_effect = [repo_resp, prot_resp]
    
    provider.prepare_push(repo)
    
    mock_put.assert_not_called()


def test_prepare_push_addresses_repository_by_owner_repo_and_id():
    """`repo.name` is a bare slug on every producer; /repos/{slug} always 404s.

    The relaxation must look the repository up by its fully-qualified name and
    then address it by numeric id, so it actually executes.
    """
    repo = Repository(name="repo", clone_url="url", full_name="acme/repo")
    provider = GitHubProvider("token")

    repo_resp = MagicMock()
    repo_resp.status_code = 200
    repo_resp.json.return_value = {"id": 4242, "default_branch": "main"}

    prot_resp = MagicMock()
    prot_resp.status_code = 200
    prot_resp.json.return_value = {"allow_force_pushes": {"enabled": False}}

    put_resp = MagicMock()
    put_resp.status_code = 200

    with patch("requests.get", side_effect=[repo_resp, prot_resp]) as mock_get, \
         patch("requests.put", return_value=put_resp) as mock_put:
        provider.prepare_push(repo)

    lookup_url = mock_get.call_args_list[0][0][0]
    assert lookup_url.endswith("/repos/acme/repo")

    put_url = mock_put.call_args[0][0]
    assert "/repos/4242/branches/main/protection" in put_url
    assert mock_put.call_args[1]["json"]["allow_force_pushes"] is True


def test_prepare_push_skips_names_that_are_not_owner_repo():
    """A payload-supplied name with an extra separator must not steer the path."""
    repo = Repository(name="repo", clone_url="url", full_name="acme/extra/repo")
    provider = GitHubProvider("token")

    with patch("requests.get") as mock_get:
        provider.prepare_push(repo)

    mock_get.assert_not_called()


def test_prepare_push_warns_when_destination_repo_missing(caplog):
    """A silent 404 is what hid the never-executing relaxation."""
    import logging
    from holocron.providers.github import logger as gh_logger

    repo = Repository(name="repo", clone_url="url", full_name="acme/repo")
    provider = GitHubProvider("token")

    repo_resp = MagicMock()
    repo_resp.status_code = 404

    with patch("requests.get", return_value=repo_resp), \
         patch.object(gh_logger, "warning") as mock_warning:
        provider.prepare_push(repo)

    mock_warning.assert_called()
    assert "branch protection was not inspected" in mock_warning.call_args[0][0]
