import pytest
import requests
from unittest.mock import Mock, patch
from holocron.github_provider import get_all_pages, get_github_repos

@patch("requests.get")
def test_get_all_pages_pagination(mock_get):
    # Setup mock to return 2 pages, then empty
    
    # Page 1: 100 items (full page)
    # Page 2: 50 items (partial page) -> Should stop here
    
    mock_resp_1 = Mock()
    mock_resp_1.json.return_value = [{'id': i} for i in range(100)]
    mock_resp_1.raise_for_status.return_value = None
    
    mock_resp_2 = Mock()
    mock_resp_2.json.return_value = [{'id': i} for i in range(100, 150)]
    mock_resp_2.raise_for_status.return_value = None
    
    mock_get.side_effect = [mock_resp_1, mock_resp_2]
    
    items = get_all_pages("http://api.github.com", {}, False, "test")
    
    assert len(items) == 150
    assert mock_get.call_count == 2

@patch("requests.get")
@patch("holocron.github_provider.log")
def test_get_all_pages_error(mock_log, mock_get):
    # Simulate a network error
    mock_get.side_effect = requests.RequestException("Boom")
    
    items = get_all_pages("url", {}, False, "context")
    
    assert len(items) == 0
    # Should have logged error
    mock_log.assert_called()
    assert "ERROR fetching context" in mock_log.call_args[0][0]

@patch("requests.get")
def test_get_all_pages_http_error(mock_get):
    # Simulate 404
    mock_resp = Mock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    mock_get.return_value = mock_resp
    
    items = get_all_pages("url", {}, False, "context")
    assert len(items) == 0

@patch("holocron.github_provider.get_all_pages")
def test_get_github_repos_with_orgs(mock_get_pages):
    # Mock sequence:
    # 1. User repos (2 items)
    # 2. Org list (1 org)
    # 3. Org repos (1 item)
    
    user_repos = [{'id': 1, 'name': 'u1'}, {'id': 2, 'name': 'u2'}]
    orgs = [{'login': 'org1'}]
    org_repos = [{'id': 3, 'name': 'o1'}, {'id': 1, 'name': 'u1'}] # Duplicate ID to test dedup
    
    mock_get_pages.side_effect = [user_repos, orgs, org_repos]
    
    # Mock token
    repos = get_github_repos("token", False)
    
    # Total unique: 1, 2, 3 = 3 repos
    assert len(repos) == 3
    ids = {r['id'] for r in repos}
    assert ids == {1, 2, 3}


