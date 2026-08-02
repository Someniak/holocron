"""Azure DevOps Services as a mirror *destination*.

The interesting part is not the URL string: it is that Azure DevOps, unlike
GitLab, does not create a repository on first push. prepare_push has to.
"""
import base64
from unittest.mock import MagicMock, patch

import pytest
import requests

from holocron.providers.azure import AzureDevOpsDestinationProvider
from holocron.providers.base import Repository

ORG_URL = "https://dev.azure.com/acme"


def json_response(payload=None, headers=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
    resp.json.return_value = payload if payload is not None else {}
    resp.raise_for_status.return_value = None
    return resp


def http_error(status_code):
    """A response whose raise_for_status raises, the way requests does."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Type": "application/json"}
    error = requests.exceptions.HTTPError(f"HTTP {status_code}", response=resp)
    resp.raise_for_status.side_effect = error
    return resp


def destination(**kwargs):
    kwargs.setdefault("org_url", ORG_URL)
    kwargs.setdefault("token", "PAT")
    kwargs.setdefault("project", "Proj")
    return AzureDevOpsDestinationProvider(**kwargs)


def repo(name="widgets"):
    return Repository(name=name, clone_url="https://github.com/acme/widgets.git")


# --- construction ------------------------------------------------------------

def test_project_is_required():
    """Without a project there is nowhere to create repositories -- fail early."""
    with pytest.raises(ValueError, match="needs a project"):
        AzureDevOpsDestinationProvider(ORG_URL, "PAT")


def test_org_url_is_required():
    with pytest.raises(ValueError, match="needs an organisation URL"):
        AzureDevOpsDestinationProvider("", "PAT", project="Proj")


# --- get_remote_url ----------------------------------------------------------

def test_push_url_is_built_from_config_not_from_the_source_url():
    """The clone URL belongs to the source; the push URL is org/project/_git/name."""
    assert destination().get_remote_url(repo()) == (
        f"https://oauth2:PAT@dev.azure.com/acme/Proj/_git/widgets"
    )


def test_push_url_encodes_project_and_token():
    url = destination(project="My Project", token="pat/with+chars").get_remote_url(repo())
    assert url == (
        "https://oauth2:pat%2Fwith%2Bchars@dev.azure.com/acme/My%20Project/_git/widgets"
    )


def test_push_url_without_a_token_carries_no_credentials():
    assert destination(token=None).get_remote_url(repo()) == (
        f"{ORG_URL}/Proj/_git/widgets"
    )


def test_push_url_keeps_a_non_default_port():
    url = destination(org_url="https://azure.local:8443/acme").get_remote_url(repo())
    assert url == "https://oauth2:PAT@azure.local:8443/acme/Proj/_git/widgets"


def test_push_url_rejects_a_nameless_repo():
    with pytest.raises(ValueError, match="no name"):
        destination().get_remote_url(Repository(name="", clone_url="x"))


def test_push_url_rejects_a_non_http_org_url():
    with pytest.raises(ValueError, match="unsupported Azure DevOps organisation URL"):
        destination(org_url="ssh://dev.azure.com/acme").get_remote_url(repo())


# --- push refspecs -----------------------------------------------------------

def test_push_is_restricted_to_branches_and_tags():
    """
    Azure DevOps owns refs/pull/*: a --mirror push would try to write the
    source's copies of them and delete the server's own.
    """
    assert destination().push_refspecs() == [
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    ]


def test_other_destinations_still_push_everything():
    """The refspec hook is opt-in; GitHub and GitLab keep the --mirror push."""
    from holocron.providers.github import GitHubProvider
    from holocron.providers.gitlab import GitLabProvider

    assert GitHubProvider("t").push_refspecs() is None
    assert GitLabProvider("http://gitlab.local/api/v4", "t").push_refspecs() is None


# --- prepare_push: the repository must exist ---------------------------------

@patch("requests.get")
def test_prepare_push_is_a_no_op_when_the_repository_exists(mock_get):
    mock_get.return_value = json_response({"id": "repo-guid", "name": "widgets"})

    with patch("requests.post") as mock_post:
        destination().prepare_push(repo())

    mock_post.assert_not_called()
    assert mock_get.call_args[0][0] == f"{ORG_URL}/Proj/_apis/git/repositories/widgets"


@patch("requests.post")
@patch("requests.get")
def test_prepare_push_creates_a_missing_repository(mock_get, mock_post):
    """404 on lookup -> resolve the project GUID -> POST the new repository."""
    mock_get.side_effect = [
        http_error(404),                                   # repository lookup
        json_response({"id": "project-guid", "name": "Proj"}),  # project lookup
    ]
    mock_post.return_value = json_response({"id": "new-repo-guid"})

    destination().prepare_push(repo())

    assert mock_get.call_args_list[1][0][0] == f"{ORG_URL}/_apis/projects/Proj"
    assert mock_post.call_args[0][0] == f"{ORG_URL}/Proj/_apis/git/repositories"
    assert mock_post.call_args[1]["json"] == {
        "name": "widgets",
        "project": {"id": "project-guid"},
    }
    assert mock_post.call_args[1]["params"]["api-version"] == "7.1"
    expected_auth = base64.b64encode(b":PAT").decode()
    assert mock_post.call_args[1]["headers"]["Authorization"] == f"Basic {expected_auth}"


@patch("requests.post")
@patch("requests.get")
def test_prepare_push_treats_a_conflict_as_success(mock_get, mock_post):
    """Two sync threads can race to create the same repo; 409 means it is there."""
    mock_get.side_effect = [http_error(404), json_response({"id": "project-guid"})]
    mock_post.return_value = json_response(status_code=409)

    destination().prepare_push(repo())  # must not raise


@patch("requests.post")
@patch("requests.get")
def test_prepare_push_caches_across_repeated_calls(mock_get, mock_post):
    """Every cycle calls prepare_push; a steady state must cost no API calls."""
    mock_get.side_effect = [http_error(404), json_response({"id": "project-guid"})]
    mock_post.return_value = json_response({"id": "new-repo-guid"})

    dest = destination()
    for _ in range(4):
        dest.prepare_push(repo())

    assert mock_post.call_count == 1
    assert mock_get.call_count == 2


@patch("requests.post")
@patch("requests.get")
def test_project_guid_is_looked_up_once_for_many_repositories(mock_get, mock_post):
    mock_get.side_effect = [
        http_error(404),
        json_response({"id": "project-guid"}),
        http_error(404),
    ]
    mock_post.return_value = json_response({"id": "new-repo-guid"})

    dest = destination()
    dest.prepare_push(repo("api"))
    dest.prepare_push(repo("web"))

    project_lookups = [c for c in mock_get.call_args_list
                       if c[0][0].endswith("/_apis/projects/Proj")]
    assert len(project_lookups) == 1
    assert mock_post.call_count == 2


# --- prepare_push: failures are fatal for the repository ---------------------

@patch("requests.post")
@patch("requests.get")
def test_creation_failure_raises_with_a_scope_hint(mock_get, mock_post):
    """An opaque 'git push' failure is a terrible way to learn the PAT is too weak."""
    mock_get.side_effect = [http_error(404), json_response({"id": "project-guid"})]
    mock_post.return_value = http_error(403)

    with pytest.raises(RuntimeError, match="Read, write, & manage"):
        destination().prepare_push(repo())


@patch("requests.get")
def test_a_failed_lookup_is_not_mistaken_for_a_missing_repository(mock_get):
    """Only 404 means 'create it'; 401 must not trigger a create attempt."""
    mock_get.return_value = http_error(401)

    with patch("requests.post") as mock_post:
        with pytest.raises(RuntimeError, match="Read & write"):
            destination().prepare_push(repo())

    mock_post.assert_not_called()


@patch("requests.get")
def test_a_missing_project_says_so(mock_get):
    mock_get.side_effect = [http_error(404), http_error(404)]

    with pytest.raises(RuntimeError, match="does not exist"):
        destination().prepare_push(repo())


@patch("requests.get")
def test_a_failed_repository_is_not_cached_as_existing(mock_get):
    """A transient failure must be retried next cycle, not remembered as done."""
    dest = destination()
    mock_get.return_value = http_error(500)

    with pytest.raises(RuntimeError):
        dest.prepare_push(repo())

    mock_get.return_value = json_response({"id": "repo-guid"})
    dest.prepare_push(repo())  # retried, and now succeeds
    assert mock_get.call_count == 2


# --- fetch_repos -------------------------------------------------------------

@patch("requests.get")
def test_fetch_repos_lists_the_destination_project(mock_get):
    mock_get.return_value = json_response({"value": [
        {"name": "widgets", "remoteUrl": f"{ORG_URL}/Proj/_git/widgets", "size": 4096},
        {"name": None},  # defensive: skipped rather than crashing
    ]})

    repos = destination().fetch_repos()

    assert [r.name for r in repos] == ["widgets"]
    assert repos[0].size == 4
    assert repos[0].full_name == "Proj/widgets"
    assert mock_get.call_args[0][0] == f"{ORG_URL}/Proj/_apis/git/repositories"
