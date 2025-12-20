
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
