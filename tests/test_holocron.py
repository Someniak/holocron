import argparse
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from holocron.__main__ import main
from holocron.providers.base import Repository


def _make_args(**overrides):
    """Helper to create a standard argparse.Namespace with defaults."""
    defaults = dict(
        watch=False,
        dry_run=False,
        concurrency=1,
        backup_only=False,
        window=10,
        verbose=False,
        storage="/tmp/data",
        source="github",
        destination="gitlab",
        credits=False,
        gitlab_namespace=None,
        checkout=False,
        interval=10,
        include=None,
        exclude=None,
        cleanup=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh_token", "GITLAB_TOKEN": "gl_token"})
@patch("holocron.__main__.get_provider")
@patch("holocron.__main__.sync_one_repo")
@patch("holocron.__main__.logger")
def test_main_single_run(mock_logger, mock_sync, mock_get_provider, mock_parse):
    mock_parse.return_value = _make_args()

    mock_source = MagicMock()
    repo1 = Repository(name="repo1", clone_url="url", pushed_at=datetime(2023, 1, 1))
    mock_source.fetch_repos.return_value = [repo1]

    mock_dest = MagicMock()

    mock_get_provider.side_effect = [mock_source, mock_dest]

    main()

    mock_source.fetch_repos.assert_called_once()
    mock_sync.assert_called_once()
    assert mock_logger.info.call_count >= 1


@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {}, clear=True)
def test_main_missing_tokens(mock_parse):
    mock_parse.return_value = _make_args()

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1


@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh"}, clear=True)
def test_main_missing_gitlab_token_normal_mode(mock_parse):
    mock_parse.return_value = _make_args()

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1


@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_provider")
def test_main_backup_only_no_gitlab_token(mock_get_provider, mock_parse):
    mock_parse.return_value = _make_args(
        backup_only=True,
        destination="local",
    )

    mock_source = MagicMock()
    mock_source.fetch_repos.return_value = []
    mock_get_provider.return_value = mock_source

    try:
        main()
    except SystemExit:
        pytest.fail("Should not exit in backup-only mode without GITLAB_TOKEN")


@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_provider")
@patch("holocron.__main__.sync_one_repo")
@patch("time.sleep")
def test_main_watch_loop(mock_sleep, mock_sync, mock_get_provider, mock_parse):
    mock_parse.return_value = _make_args(
        watch=True,
        interval=60,
    )

    pushed_at = datetime(2023, 1, 1, 12, 0, 0)
    repo1 = Repository(name="repo1", clone_url="url", pushed_at=pushed_at)

    mock_source = MagicMock()
    mock_source.fetch_repos.side_effect = [
        [repo1],  # Cycle 1
        [repo1],  # Cycle 2
    ]

    mock_get_provider.return_value = mock_source

    mock_sleep.side_effect = [None, RuntimeError("Break Loop")]

    with pytest.raises(RuntimeError, match="Break Loop"):
        main()

    # Sync should only be called ONCE despite 2 cycles, because of redundancy check
    assert mock_sync.call_count == 1


@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_provider")
@patch("holocron.__main__.sync_one_repo")
@patch("holocron.__main__.logger")
def test_main_verbose_no_sync(mock_logger, mock_sync, mock_get_provider, mock_parse):
    mock_parse.return_value = _make_args(verbose=True)

    mock_source = MagicMock()
    mock_source.fetch_repos.return_value = []
    mock_get_provider.return_value = mock_source

    main()

    mock_logger.debug.assert_called()
    log_calls = [str(call) for call in mock_logger.debug.call_args_list]
    assert any("No changes detected" in call for call in log_calls)


@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_provider")
@patch("holocron.__main__.sync_one_repo")
@patch("holocron.__main__.logger")
def test_main_exception_logging(mock_logger, mock_sync, mock_get_provider, mock_parse):
    mock_parse.return_value = _make_args()

    repo = Repository(name="fail", clone_url="url", pushed_at=datetime(2023, 1, 1))
    mock_source = MagicMock()
    mock_source.fetch_repos.return_value = [repo]
    mock_get_provider.return_value = mock_source

    mock_sync.side_effect = Exception("Thread Boom")

    main()

    mock_logger.error.assert_called()
    log_calls = [str(call) for call in mock_logger.error.call_args_list]
    assert any("generated an exception: Thread Boom" in call for call in log_calls)


@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_provider")
@patch("holocron.__main__.sync_one_repo")
@patch("holocron.__main__.logger")
def test_main_include_filter(mock_logger, mock_sync, mock_get_provider, mock_parse):
    mock_parse.return_value = _make_args(include=["repo-*"])

    repo1 = Repository(name="repo-1", clone_url="url", pushed_at=datetime(2023, 1, 1))
    repo2 = Repository(name="other-repo", clone_url="url", pushed_at=datetime(2023, 1, 1))

    mock_source = MagicMock()
    mock_source.fetch_repos.return_value = [repo1, repo2]
    mock_get_provider.return_value = mock_source

    main()

    # Only repo-1 should be synced
    assert mock_sync.call_count == 1
    call_kwargs = mock_sync.call_args[1]
    assert call_kwargs["repo"].name == "repo-1"


@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_provider")
@patch("holocron.__main__.sync_one_repo")
@patch("holocron.__main__.logger")
def test_main_exclude_filter(mock_logger, mock_sync, mock_get_provider, mock_parse):
    mock_parse.return_value = _make_args(exclude=["test-*"])

    repo1 = Repository(name="my-app", clone_url="url", pushed_at=datetime(2023, 1, 1))
    repo2 = Repository(name="test-utils", clone_url="url", pushed_at=datetime(2023, 1, 1))

    mock_source = MagicMock()
    mock_source.fetch_repos.return_value = [repo1, repo2]
    mock_get_provider.return_value = mock_source

    main()

    assert mock_sync.call_count == 1
    call_kwargs = mock_sync.call_args[1]
    assert call_kwargs["repo"].name == "my-app"
