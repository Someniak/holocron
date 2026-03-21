from unittest.mock import MagicMock, patch

from holocron.providers.github import GitHubProvider


@patch("requests.get")
def test_get_all_pages_pagination(mock_get):
    mock_resp_1 = MagicMock()
    mock_resp_1.status_code = 200
    mock_resp_1.headers = {}
    mock_resp_1.json.return_value = [{"id": i} for i in range(100)]
    mock_resp_1.raise_for_status.return_value = None

    mock_resp_2 = MagicMock()
    mock_resp_2.status_code = 200
    mock_resp_2.headers = {}
    mock_resp_2.json.return_value = [{"id": i} for i in range(100, 150)]
    mock_resp_2.raise_for_status.return_value = None

    mock_get.side_effect = [mock_resp_1, mock_resp_2]

    provider = GitHubProvider(token="test_token")
    items = provider._get_all_pages("http://api.github.com", {}, "test")

    assert len(items) == 150
    assert mock_get.call_count == 2


@patch("requests.get")
@patch("holocron.providers.github.logger")
def test_get_all_pages_error(mock_logger, mock_get):
    import requests as req

    mock_get.side_effect = req.HTTPError("404 Not Found")

    provider = GitHubProvider(token="test_token")
    items = provider._get_all_pages("url", {}, "context")

    assert len(items) == 0
    mock_logger.error.assert_called()
    assert "HTTP error fetching context" in mock_logger.error.call_args[0][0]


@patch("requests.get")
@patch("holocron.providers.github.logger")
def test_get_all_pages_http_error(mock_logger, mock_get):
    import requests as req

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.headers = {}
    mock_resp.raise_for_status.side_effect = req.HTTPError("404 Not Found")
    mock_get.return_value = mock_resp

    provider = GitHubProvider(token="test_token")
    items = provider._get_all_pages("url", {}, "context")
    assert len(items) == 0


@patch("holocron.providers.github.GitHubProvider._get_all_pages")
def test_fetch_repos_with_orgs(mock_get_pages):
    user_repos = [{"id": 1, "name": "u1", "clone_url": "http://u1", "size": 100}]
    user_repos_rep = [{"id": 2, "name": "u2", "clone_url": "http://u2", "size": 100}]

    orgs = [{"login": "org1"}]
    org_repos = [
        {"id": 3, "name": "o1", "clone_url": "http://o1", "size": 100},
        {"id": 1, "name": "u1", "clone_url": "http://u1", "size": 100},  # Duplicate
    ]

    mock_get_pages.side_effect = [
        user_repos + user_repos_rep,
        orgs,
        org_repos,
    ]

    provider = GitHubProvider(token="token")
    repos = provider.fetch_repos()

    assert len(repos) == 3
    names = {r.name for r in repos}
    assert names == {"u1", "u2", "o1"}
