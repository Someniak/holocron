import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from holocron.providers.azure import AzureDevOpsProvider, _parse_azure_date, _slugify
from holocron.providers.base import Repository

ORG_URL = "https://dev.azure.com/acme"


def json_response(payload, headers=None, status_code=200):
    """A requests.Response stand-in carrying a JSON body."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def repo_item(name, project="Proj", repo_id="id-1", size=2048, **extra):
    item = {
        "id": repo_id,
        "name": name,
        "project": {"name": project},
        "defaultBranch": "refs/heads/main",
        "size": size,
        "remoteUrl": f"{ORG_URL}/{project}/_git/{name.replace(' ', '%20')}",
    }
    item.update(extra)
    return item


def provider(**kwargs):
    kwargs.setdefault("org_url", ORG_URL)
    kwargs.setdefault("token", "PAT")
    return AzureDevOpsProvider(**kwargs)


# --- fetch_repos -------------------------------------------------------------

@patch("requests.get")
def test_fetch_repos_maps_fields(mock_get):
    """Repository list + per-repo last push are mapped onto Repository objects."""
    mock_get.side_effect = [
        json_response({"count": 1, "value": [repo_item("api", size=4096)]}),
        json_response({"count": 1, "value": [{"date": "2024-05-04T10:11:12.1234567Z"}]}),
    ]

    repos = provider().fetch_repos()

    assert len(repos) == 1
    repo = repos[0]
    assert repo.name == "api"
    assert repo.clone_url == f"{ORG_URL}/Proj/_git/api"
    # Azure DevOps reports bytes; Repository.size is KB.
    assert repo.size == 4
    assert repo.pushed_at == datetime(2024, 5, 4, 10, 11, 12, 123456)
    assert repo.full_name == "Proj/api"

    # The repositories call must carry the API version and the PAT as Basic auth.
    list_call = mock_get.call_args_list[0]
    assert list_call[0][0] == f"{ORG_URL}/_apis/git/repositories"
    assert list_call[1]["params"]["api-version"] == "7.1"
    expected = base64.b64encode(b":PAT").decode()
    assert list_call[1]["headers"]["Authorization"] == f"Basic {expected}"


@patch("requests.get")
def test_fetch_repos_scoped_to_project(mock_get):
    mock_get.side_effect = [
        json_response({"value": [repo_item("api")]}),
        json_response({"value": []}),
    ]

    provider(project="My Project").fetch_repos()

    assert mock_get.call_args_list[0][0][0] == f"{ORG_URL}/My%20Project/_apis/git/repositories"


@patch("requests.get")
def test_fetch_repos_skips_disabled_and_empty(mock_get):
    """Disabled repos cannot be cloned; repos with no default branch have no refs."""
    mock_get.side_effect = [
        json_response({"value": [
            repo_item("live", repo_id="1"),
            repo_item("disabled", repo_id="2", isDisabled=True),
            repo_item("empty", repo_id="3", defaultBranch=None),
            {"id": "4", "project": {"name": "Proj"}},  # no name / remoteUrl
        ]}),
        json_response({"value": []}),
    ]

    repos = provider().fetch_repos()

    assert [r.name for r in repos] == ["live"]


@patch("requests.get")
def test_fetch_repos_slugifies_names(mock_get):
    mock_get.side_effect = [
        json_response({"value": [repo_item("My Cool Repo!")]}),
        json_response({"value": []}),
    ]

    repos = provider().fetch_repos()

    assert repos[0].name == "My-Cool-Repo"


@patch("requests.get")
def test_fetch_repos_qualifies_cross_project_name_collisions(mock_get):
    """Azure DevOps repo names are only unique per project, mirror names are global."""
    mock_get.side_effect = [
        json_response({"value": [
            repo_item("docs", project="Alpha", repo_id="1"),
            repo_item("docs", project="Beta", repo_id="2"),
            repo_item("api", project="Alpha", repo_id="3"),
        ]}),
        json_response({"value": []}),
        json_response({"value": []}),
        json_response({"value": []}),
    ]

    repos = provider().fetch_repos()

    assert [r.name for r in repos] == ["Alpha-docs", "Beta-docs", "api"]


@patch("requests.get")
def test_fetch_repos_disambiguates_identical_slugs(mock_get):
    """Distinct source names can slugify to the same string; both must survive."""
    mock_get.side_effect = [
        json_response({"value": [
            repo_item("my repo", project="Alpha", repo_id="1"),
            repo_item("my-repo", project="Alpha", repo_id="2"),
        ]}),
        json_response({"value": []}),
        json_response({"value": []}),
    ]

    repos = provider().fetch_repos()

    assert [r.name for r in repos] == ["my-repo", "my-repo-2"]


@patch("requests.get")
def test_fetch_repos_follows_continuation_token(mock_get):
    mock_get.side_effect = [
        json_response({"value": [repo_item("one", repo_id="1")]},
                      headers={"x-ms-continuationtoken": "next"}),
        json_response({"value": [repo_item("two", repo_id="2")]}),
        json_response({"value": []}),
        json_response({"value": []}),
    ]

    repos = provider().fetch_repos()

    assert {r.name for r in repos} == {"one", "two"}
    assert mock_get.call_args_list[1][1]["params"]["continuationToken"] == "next"


@patch("requests.get")
@patch("holocron.providers.azure.logger")
def test_fetch_repos_raises_on_partial_fetch(mock_logger, mock_get):
    """A mid-fetch failure must raise, not return a partial (repo-dropping) list."""
    mock_get.side_effect = Exception("Boom")

    with pytest.raises(RuntimeError, match="Incomplete fetch of Azure DevOps repositories"):
        provider().fetch_repos()

    mock_logger.error.assert_called()


@patch("requests.get")
def test_fetch_repos_rejects_html_signin_page(mock_get):
    """An unauthenticated dev.azure.com request answers with HTML, not a 401."""
    mock_get.return_value = json_response({}, headers={"Content-Type": "text/html"}, status_code=203)

    with pytest.raises(RuntimeError, match="Code \\(Read\\)"):
        provider().fetch_repos()


# --- last push / pushed_at ---------------------------------------------------

@patch("requests.get")
def test_last_push_requests_only_the_newest(mock_get):
    mock_get.side_effect = [
        json_response({"value": [repo_item("api", repo_id="abc")]}),
        json_response({"value": [{"date": "2024-05-04T10:11:12Z"}]}),
    ]

    provider().fetch_repos()

    push_call = mock_get.call_args_list[1]
    assert push_call[0][0] == f"{ORG_URL}/_apis/git/repositories/abc/pushes"
    assert push_call[1]["params"]["$top"] == 1


@patch("requests.get")
def test_repo_without_pushes_has_no_timestamp(mock_get):
    mock_get.side_effect = [
        json_response({"value": [repo_item("api")]}),
        json_response({"value": []}),
    ]

    assert provider().fetch_repos()[0].pushed_at is None


@patch("requests.get")
@patch("holocron.providers.azure.logger")
def test_unknown_last_push_falls_back_to_now(mock_logger, mock_get):
    """
    A failed activity lookup must not leave pushed_at empty: watch mode would
    then skip the repo forever once its mirror exists.
    """
    mock_get.side_effect = [
        json_response({"value": [repo_item("api")]}),
        Exception("pushes unavailable"),
    ]

    pushed_at = provider().fetch_repos()[0].pushed_at

    assert pushed_at is not None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert now - pushed_at < timedelta(minutes=1)
    mock_logger.warning.assert_called()


@pytest.mark.parametrize("value,expected", [
    # .NET round-trip format: 7 fractional digits, more than fromisoformat takes.
    ("2014-06-30T17:58:34.1765687Z", datetime(2014, 6, 30, 17, 58, 34, 176568)),
    ("2014-04-14T21:35:01.130535Z", datetime(2014, 4, 14, 21, 35, 1, 130535)),
    ("2024-01-02T03:04:05Z", datetime(2024, 1, 2, 3, 4, 5)),
    # Offsets are normalised to naive UTC (what mirror.needs_sync compares against).
    ("2024-01-02T05:04:05+02:00", datetime(2024, 1, 2, 3, 4, 5)),
    ("not a date", None),
    ("", None),
    (None, None),
])
def test_parse_azure_date(value, expected):
    assert _parse_azure_date(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("My Repo", "My-Repo"),
    ("repo.name_1-2", "repo.name_1-2"),
    ("--weird--", "weird"),
    ("..", ""),
    ("!!!", ""),
    (None, ""),
])
def test_slugify(value, expected):
    assert _slugify(value) == expected


# --- get_remote_url (token pinning) ------------------------------------------

def test_get_remote_url_injects_token():
    repo = Repository(name="api", clone_url=f"{ORG_URL}/Proj/_git/api")
    assert provider().get_remote_url(repo) == "https://oauth2:PAT@dev.azure.com/acme/Proj/_git/api"


def test_get_remote_url_strips_existing_userinfo():
    """Azure DevOps clone URLs often embed the org as userinfo."""
    repo = Repository(name="api", clone_url="https://acme@dev.azure.com/acme/Proj/_git/api")
    assert provider().get_remote_url(repo) == "https://oauth2:PAT@dev.azure.com/acme/Proj/_git/api"


def test_get_remote_url_without_token_is_unauthenticated():
    repo = Repository(name="api", clone_url="https://acme@dev.azure.com/acme/Proj/_git/api")
    assert provider(token=None).get_remote_url(repo) == "https://dev.azure.com/acme/Proj/_git/api"


def test_get_remote_url_refuses_foreign_host():
    repo = Repository(name="api", clone_url="https://attacker.tld/acme/Proj/_git/api")
    with pytest.raises(ValueError, match="does not match the configured"):
        provider().get_remote_url(repo)


def test_get_remote_url_refuses_other_organisation():
    repo = Repository(name="api", clone_url="https://dev.azure.com/other-org/Proj/_git/api")
    with pytest.raises(ValueError, match="outside the configured organisation"):
        provider().get_remote_url(repo)


def test_get_remote_url_refuses_non_http_scheme():
    repo = Repository(name="api", clone_url="ext::sh -c id")
    with pytest.raises(ValueError, match="unsupported clone URL"):
        provider().get_remote_url(repo)


def test_get_remote_url_supports_legacy_visualstudio_host():
    p = AzureDevOpsProvider(org_url="https://acme.visualstudio.com", token="PAT")
    repo = Repository(name="api", clone_url="https://acme.visualstudio.com/Proj/_git/api")
    assert p.get_remote_url(repo) == "https://oauth2:PAT@acme.visualstudio.com/Proj/_git/api"
