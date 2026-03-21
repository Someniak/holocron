from unittest.mock import MagicMock, patch

import pytest

from holocron.providers.base import Repository
from holocron.providers.bitbucket import BitbucketProvider


@pytest.fixture
def provider():
    return BitbucketProvider(username="testuser", app_password="testpass")


def test_get_remote_url(provider):
    repo = Repository(name="my-repo", clone_url="https://bitbucket.org/testuser/my-repo.git")
    url = provider.get_remote_url(repo)
    assert url == "https://testuser:testpass@bitbucket.org/testuser/my-repo.git"


@patch("requests.get")
def test_fetch_repos_user_repos(mock_get, provider):
    # Mock user repos response
    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.headers = {}
    user_resp.json.return_value = {
        "values": [
            {
                "uuid": "{abc-123}",
                "slug": "repo1",
                "name": "Repo 1",
                "updated_on": "2024-01-15T10:30:00.000Z",
                "size": 1048576,
                "links": {
                    "clone": [
                        {"name": "https", "href": "https://bitbucket.org/testuser/repo1.git"},
                        {"name": "ssh", "href": "git@bitbucket.org:testuser/repo1.git"},
                    ]
                },
            }
        ],
    }
    user_resp.raise_for_status.return_value = None

    # Mock workspaces response (empty)
    ws_resp = MagicMock()
    ws_resp.status_code = 200
    ws_resp.headers = {}
    ws_resp.json.return_value = {"values": []}
    ws_resp.raise_for_status.return_value = None

    mock_get.side_effect = [user_resp, ws_resp]

    repos = provider.fetch_repos()

    assert len(repos) == 1
    assert repos[0].name == "repo1"
    assert repos[0].clone_url == "https://bitbucket.org/testuser/repo1.git"
    assert repos[0].size == 1024  # 1048576 // 1024


@patch("requests.get")
def test_fetch_repos_with_workspaces(mock_get, provider):
    # User repos
    user_resp = MagicMock()
    user_resp.status_code = 200
    user_resp.headers = {}
    user_resp.json.return_value = {
        "values": [
            {
                "uuid": "{abc-123}",
                "slug": "repo1",
                "links": {"clone": [{"name": "https", "href": "https://bb.org/user/repo1.git"}]},
            }
        ],
    }
    user_resp.raise_for_status.return_value = None

    # Workspaces
    ws_resp = MagicMock()
    ws_resp.status_code = 200
    ws_resp.headers = {}
    ws_resp.json.return_value = {
        "values": [
            {"slug": "testuser"},  # Same as username, should skip
            {"slug": "team-ws"},
        ]
    }
    ws_resp.raise_for_status.return_value = None

    # Team workspace repos
    team_resp = MagicMock()
    team_resp.status_code = 200
    team_resp.headers = {}
    team_resp.json.return_value = {
        "values": [
            {
                "uuid": "{def-456}",
                "slug": "team-repo",
                "links": {"clone": [{"name": "https", "href": "https://bb.org/team/team-repo.git"}]},
            },
            {
                "uuid": "{abc-123}",  # Duplicate
                "slug": "repo1",
                "links": {"clone": [{"name": "https", "href": "https://bb.org/user/repo1.git"}]},
            },
        ],
    }
    team_resp.raise_for_status.return_value = None

    mock_get.side_effect = [user_resp, ws_resp, team_resp]

    repos = provider.fetch_repos()

    assert len(repos) == 2
    names = {r.name for r in repos}
    assert names == {"repo1", "team-repo"}


@patch("time.sleep")
@patch("requests.get")
@patch("holocron.providers.bitbucket.logger")
def test_fetch_repos_api_error(mock_logger, mock_get, mock_sleep, provider):
    import requests as req

    mock_get.side_effect = req.ConnectionError("Connection refused")

    # The retry decorator retries then raises; fetch_repos propagates it
    with pytest.raises(req.ConnectionError):
        provider.fetch_repos()

    # Verify retries happened (3 retries = 3 sleeps)
    assert mock_sleep.call_count == 3


def test_prepare_push_is_noop(provider):
    repo = Repository(name="test", clone_url="url")
    # Should not raise
    provider.prepare_push(repo)


@patch("requests.get")
def test_pagination(mock_get, provider):
    # Page 1: full page with "next" URL
    page1_resp = MagicMock()
    page1_resp.status_code = 200
    page1_resp.headers = {}
    page1_resp.json.return_value = {
        "values": [
            {
                "uuid": f"{{{i}}}",
                "slug": f"repo-{i}",
                "links": {"clone": [{"name": "https", "href": f"https://bb.org/u/repo-{i}.git"}]},
            }
            for i in range(100)
        ],
        "next": "https://api.bitbucket.org/2.0/repositories/testuser?page=2",
    }
    page1_resp.raise_for_status.return_value = None

    # Page 2: partial page, no "next"
    page2_resp = MagicMock()
    page2_resp.status_code = 200
    page2_resp.headers = {}
    page2_resp.json.return_value = {
        "values": [
            {
                "uuid": f"{{{100 + i}}}",
                "slug": f"repo-{100 + i}",
                "links": {"clone": [{"name": "https", "href": f"https://bb.org/u/repo-{100 + i}.git"}]},
            }
            for i in range(50)
        ],
    }
    page2_resp.raise_for_status.return_value = None

    # Workspaces: empty
    ws_resp = MagicMock()
    ws_resp.status_code = 200
    ws_resp.headers = {}
    ws_resp.json.return_value = {"values": []}
    ws_resp.raise_for_status.return_value = None

    mock_get.side_effect = [page1_resp, page2_resp, ws_resp]

    repos = provider.fetch_repos()
    assert len(repos) == 150
