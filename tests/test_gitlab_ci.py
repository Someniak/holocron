import pytest
from unittest.mock import MagicMock, patch

from holocron.providers.gitlab import GitLabProvider
from holocron.providers.base import Repository


@pytest.fixture
def provider():
    return GitLabProvider("http://gitlab.local/api/v4", "gltok", namespace="group")


@pytest.fixture
def repo():
    return Repository(name="holocron", clone_url="http://gh/holocron.git")


def _resp(status_code=200, json_data=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data if json_data is not None else {}
    return r


# --- get_project_id ---

@patch("requests.get")
def test_get_project_id(mock_get, provider, repo):
    mock_get.return_value = _resp(200, {"id": 77})
    assert provider.get_project_id(repo) == 77
    url = mock_get.call_args[0][0]
    assert "projects/group%2Fholocron" in url


@patch("requests.get")
def test_get_project_id_missing_returns_none(mock_get, provider, repo):
    mock_get.return_value = _resp(404, {})
    assert provider.get_project_id(repo) is None


# --- create_or_update_merge_request ---

@patch("requests.post")
@patch("requests.get")
def test_create_mr_when_none_open(mock_get, mock_post, provider):
    mock_get.return_value = _resp(200, [])           # no open MR
    mock_post.return_value = _resp(201, {"iid": 5, "web_url": "http://gl/mr/5"})

    mr = provider.create_or_update_merge_request(77, "holocron/pr-3", "main", "title")

    assert mr["iid"] == 5
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["source_branch"] == "holocron/pr-3"
    assert kwargs["json"]["target_branch"] == "main"


@patch("requests.post")
@patch("requests.get")
def test_create_mr_dedups_existing(mock_get, mock_post, provider):
    mock_get.return_value = _resp(200, [{"iid": 9, "web_url": "http://gl/mr/9"}])

    mr = provider.create_or_update_merge_request(77, "holocron/pr-3", "main", "title")

    assert mr["iid"] == 9
    mock_post.assert_not_called()   # reused, no duplicate MR


# --- close_merge_request ---

@patch("requests.put")
@patch("requests.get")
def test_close_merge_request(mock_get, mock_put, provider):
    mock_get.return_value = _resp(200, [{"iid": 9}])
    mock_put.return_value = _resp(200, {"iid": 9, "state": "closed"})

    provider.close_merge_request(77, "holocron/pr-3")

    mock_put.assert_called_once()
    args, kwargs = mock_put.call_args
    assert "merge_requests/9" in args[0]
    assert kwargs["json"] == {"state_event": "close"}


@patch("requests.put")
@patch("requests.get")
def test_close_merge_request_noop_when_none(mock_get, mock_put, provider):
    mock_get.return_value = _resp(200, [])
    provider.close_merge_request(77, "holocron/pr-3")
    mock_put.assert_not_called()


# --- pipeline lookups ---

@patch("requests.get")
def test_get_latest_mr_pipeline(mock_get, provider):
    mock_get.return_value = _resp(200, [{"id": 1, "status": "running"}])
    p = provider.get_latest_mr_pipeline(77, 5)
    assert p["id"] == 1
    assert "merge_requests/5/pipelines" in mock_get.call_args[0][0]


@patch("requests.get")
def test_get_latest_mr_pipeline_empty(mock_get, provider):
    mock_get.return_value = _resp(200, [])
    assert provider.get_latest_mr_pipeline(77, 5) is None


@patch("requests.get")
def test_get_pipeline_for_sha(mock_get, provider):
    mock_get.return_value = _resp(200, [{"id": 2}])
    p = provider.get_pipeline_for_sha(77, "deadbee")
    assert p["id"] == 2
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["sha"] == "deadbee"


@patch("requests.get")
def test_get_pipeline_status(mock_get, provider):
    mock_get.return_value = _resp(200, {"id": 2, "status": "success", "web_url": "http://gl/p/2"})
    p = provider.get_pipeline_status(77, 2)
    assert p["status"] == "success"
    assert "pipelines/2" in mock_get.call_args[0][0]


# --- delete_branch ---

@patch("requests.delete")
def test_delete_branch_encodes_and_tolerates_404(mock_delete, provider):
    mock_delete.return_value = _resp(404, {})
    provider.delete_branch(77, "holocron/pr-3")   # must not raise
    url = mock_delete.call_args[0][0]
    assert "repository/branches/holocron%2Fpr-3" in url
