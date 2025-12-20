
import pytest
from unittest.mock import MagicMock, patch
from holocron.providers.gitlab import GitLabProvider
from holocron.providers.base import Repository

@pytest.fixture
def repo():
    return Repository(name="test-repo", clone_url="http://src/test-repo.git")

@patch("requests.get")
@patch("requests.patch")
def test_prepare_push_enables_force_push(mock_patch, mock_get, repo):
    """
    Test that prepare_push enables allow_force_push if it is disabled.
    """
    provider = GitLabProvider("http://gitlab.com", "token", namespace="group")
    
    # Mock Responses
    # 1. Get Project -> Success
    project_resp = MagicMock()
    project_resp.status_code = 200
    project_resp.json.return_value = {"id": 123, "default_branch": "main"}
    
    # 2. Get Protected Branch -> Success, allow_force_push=False
    prot_resp = MagicMock()
    prot_resp.status_code = 200
    prot_resp.json.return_value = {"name": "main", "allow_force_push": False}
    
    # 3. Patch -> Success
    patch_resp = MagicMock()
    patch_resp.status_code = 200
    
    mock_get.side_effect = [project_resp, prot_resp]
    mock_patch.return_value = patch_resp
    
    provider.prepare_push(repo)
    
    # Verifications
    encoded_path = "group%2Ftest-repo"
    
    # Check checks
    assert mock_get.call_count == 2
    assert f"projects/{encoded_path}" in mock_get.call_args_list[0][0][0]
    
    # Check patch
    mock_patch.assert_called_once()
    args, kwargs = mock_patch.call_args
    assert "projects/123/protected_branches/main" in args[0]
    assert kwargs['json'] == {'allow_force_push': True}

@patch("requests.get")
@patch("requests.patch")
def test_prepare_push_already_enabled(mock_patch, mock_get, repo):
    """
    Test that preserve_push does nothing if allow_force_push is already True.
    """
    provider = GitLabProvider("http://gitlab.com", "token")
    
    # 1. Project
    project_resp = MagicMock()
    project_resp.status_code = 200
    project_resp.json.return_value = {"id": 123, "default_branch": "master"}
    
    # 2. Protected -> True
    prot_resp = MagicMock()
    prot_resp.status_code = 200
    prot_resp.json.return_value = {"name": "master", "allow_force_push": True}
    
    mock_get.side_effect = [project_resp, prot_resp]
    
    provider.prepare_push(repo)
    
    mock_patch.assert_not_called()

@patch("requests.get")
@patch("requests.patch")
def test_prepare_push_branch_not_protected(mock_patch, mock_get, repo):
    """
    Test that prepare_push does nothing if branch is not protected.
    """
    provider = GitLabProvider("http://gitlab.com", "token")
    
    project_resp = MagicMock()
    project_resp.status_code = 200
    project_resp.json.return_value = {"id": 123, "default_branch": "main"}
    
    # Protected -> 404
    prot_resp = MagicMock()
    prot_resp.status_code = 404
    
    mock_get.side_effect = [project_resp, prot_resp]
    
    provider.prepare_push(repo)
    
    mock_patch.assert_not_called()

@patch("requests.get")
@patch("requests.patch")
def test_prepare_push_project_not_found(mock_patch, mock_get, repo):
    provider = GitLabProvider("http://gitlab.com", "token")
    
    project_resp = MagicMock()
    project_resp.status_code = 404
    
    mock_get.return_value = project_resp
    
    provider.prepare_push(repo)
    
    mock_get.assert_called_once()
    mock_patch.assert_not_called()
