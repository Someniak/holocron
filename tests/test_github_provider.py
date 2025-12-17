import pytest
from unittest.mock import Mock, patch
from holocron.providers.github import GitHubProvider
from holocron.providers.base import Repository

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
    
    provider = GitHubProvider(token="test_token")
    items = provider._get_all_pages("http://api.github.com", {}, "test")
    
    assert len(items) == 150
    assert mock_get.call_count == 2

@patch("requests.get")
@patch("holocron.providers.github.logger")
def test_get_all_pages_error(mock_logger, mock_get):
    # Simulate a network error
    mock_get.side_effect = Exception("Boom")
    
    provider = GitHubProvider(token="test_token")
    items = provider._get_all_pages("url", {}, "context")
    
    assert len(items) == 0
    # Should have logged error
    mock_logger.error.assert_called()
    assert "ERROR fetching context" in mock_logger.error.call_args[0][0]

@patch("requests.get")
@patch("holocron.providers.github.logger")
def test_get_all_pages_http_error(mock_logger, mock_get):
    # Simulate 404
    mock_resp = Mock()
    mock_resp.raise_for_status.side_effect = Exception("404 Not Found")
    mock_get.return_value = mock_resp
    
    provider = GitHubProvider(token="test_token")
    items = provider._get_all_pages("url", {}, "context")
    assert len(items) == 0

@patch("holocron.providers.github.GitHubProvider._get_all_pages")
def test_fetch_repos_with_orgs(mock_get_pages):
    # Mock sequence:
    # 1. User repos (2 items)
    # 2. Org list (1 org)
    # 3. Org repos (1 item)
    
    user_repos = [{'id': 1, 'name': 'u1', 'clone_url': 'http://u1', 'size': 100}]
    user_repos_rep = [{'id': 2, 'name': 'u2', 'clone_url': 'http://u2', 'size': 100}]
    
    # _get_all_pages is called 3 times:
    # 1. user/repos
    # 2. user/orgs
    # 3. orgs/org1/repos
    
    # We need to structure the side_effect correctly.
    # The first call returns user repos.
    # The second call returns orgs.
    # The third call returns org repos.
    
    orgs = [{'login': 'org1'}]
    org_repos = [{'id': 3, 'name': 'o1', 'clone_url': 'http://o1', 'size': 100}, {'id': 1, 'name': 'u1', 'clone_url': 'http://u1', 'size': 100}] # Duplicate ID to test dedup
    
    mock_get_pages.side_effect = [
        user_repos + user_repos_rep, # user repos
        orgs, # orgs
        org_repos # org repos
    ]
    
    provider = GitHubProvider(token="token")
    repos = provider.fetch_repos()
    
    # Total unique: 1, 2, 3 = 3 repos
    assert len(repos) == 3
    names = {r.name for r in repos}
    assert names == {'u1', 'u2', 'o1'}
