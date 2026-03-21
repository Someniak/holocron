from unittest.mock import MagicMock, patch

from holocron.providers.gitlab import GitLabProvider


@patch("holocron.providers.gitlab.GitLabProvider._get_all_pages")
def test_fetch_repos_basic(mock_get_pages):
    projects = [
        {
            "id": 1,
            "path": "project-a",
            "http_url_to_repo": "http://gl/a.git",
            "last_activity_at": "2024-01-01T12:00:00.000Z",
        },
        {"id": 2, "path": "project-b", "http_url_to_repo": "http://gl/b.git"},
    ]
    mock_get_pages.return_value = projects

    provider = GitLabProvider("http://gitlab.local/api/v4", "token")
    repos = provider.fetch_repos()

    assert len(repos) == 2
    assert repos[0].name == "project-a"
    assert repos[0].clone_url == "http://gl/a.git"
    assert repos[0].pushed_at is not None
    assert repos[1].name == "project-b"
    assert repos[1].pushed_at is None


@patch("holocron.providers.gitlab.GitLabProvider._get_all_pages")
def test_fetch_repos_deduplicates(mock_get_pages):
    projects = [
        {"id": 1, "path": "project-a", "http_url_to_repo": "http://gl/a.git"},
        {"id": 1, "path": "project-a", "http_url_to_repo": "http://gl/a.git"},  # Duplicate
        {"id": 2, "path": "project-b", "http_url_to_repo": "http://gl/b.git"},
    ]
    mock_get_pages.return_value = projects

    provider = GitLabProvider("http://gitlab.local/api/v4", "token")
    repos = provider.fetch_repos()

    assert len(repos) == 2
    names = {r.name for r in repos}
    assert names == {"project-a", "project-b"}


@patch("requests.get")
def test_get_all_pages_pagination(mock_get):
    page1 = MagicMock()
    page1.status_code = 200
    page1.headers = {}
    page1.json.return_value = [{"id": i} for i in range(100)]
    page1.raise_for_status.return_value = None

    page2 = MagicMock()
    page2.status_code = 200
    page2.headers = {}
    page2.json.return_value = [{"id": i} for i in range(100, 130)]
    page2.raise_for_status.return_value = None

    mock_get.side_effect = [page1, page2]

    provider = GitLabProvider("http://gitlab.local/api/v4", "token")
    items = provider._get_all_pages("http://gitlab.local/api/v4/projects", {"Private-Token": "t"}, "test")

    assert len(items) == 130
    assert mock_get.call_count == 2


def test_get_remote_url_with_namespace():
    provider = GitLabProvider("http://gitlab.local/api/v4", "token", namespace="mygroup")
    from holocron.providers.base import Repository

    repo = Repository(name="my-repo", clone_url="http://gitlab.local/my-repo.git")
    url = provider.get_remote_url(repo)

    assert url == "http://oauth2:token@gitlab.local/mygroup/my-repo.git"


def test_get_remote_url_without_namespace():
    provider = GitLabProvider("https://gitlab.com/api/v4", "token")
    from holocron.providers.base import Repository

    repo = Repository(name="my-repo", clone_url="https://gitlab.com/my-repo.git")
    url = provider.get_remote_url(repo)

    assert url == "https://oauth2:token@gitlab.com/my-repo.git"
