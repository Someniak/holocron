import os
import sys
import pytest
from unittest.mock import MagicMock, patch, call
from holocron.__main__ import main

@pytest.fixture
def mock_env():
    return {
        "GITHUB_TOKEN": "gh_token",
        "GITLAB_TOKEN": "gl_token"
    }

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh_token", "GITLAB_TOKEN": "gl_token"})
@patch("holocron.__main__.get_github_repos")
@patch("holocron.__main__.sync_one_repo")
@patch("holocron.__main__.log")
def test_main_single_run(mock_log, mock_sync, mock_get_repos, mock_parse, mock_env):
    # Setup args: single run (not watch), dry run False
    args = MagicMock()
    args.watch = False
    args.dry_run = False
    args.concurrency = 1
    args.backup_only = False
    args.window = 10
    args.verbose = False
    args.storage = "/tmp/data"
    mock_parse.return_value = args

    # Mock repos
    repo1 = {'name': 'repo1', 'pushed_at': '2023-01-01T00:00:00Z'}
    mock_get_repos.return_value = [repo1]

    main()

    # Assertions
    mock_get_repos.assert_called_once()
    mock_sync.assert_called_once()
    assert mock_log.call_count >= 2 # Starting + Found + Complete

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {}, clear=True) # Empty env
def test_main_missing_tokens(mock_parse):
    args = MagicMock()
    args.backup_only = False
    mock_parse.return_value = args
    
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh"}, clear=True)
def test_main_missing_gitlab_token_normal_mode(mock_parse):
    args = MagicMock()
    # If backup_only is False, we NEED GitLab token
    args.backup_only = False
    mock_parse.return_value = args
    
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_github_repos")
def test_main_backup_only_no_gitlab_token(mock_get_repos, mock_parse):
    # Should NOT exit
    args = MagicMock()
    args.backup_only = True
    args.watch = False
    args.concurrency = 1
    args.dry_run = False
    args.verbose = False
    args.storage = "/tmp"
    mock_parse.return_value = args
    mock_get_repos.return_value = [] # Empty repos to finish quickly

    try:
        main()
    except SystemExit:
        pytest.fail("Should not exit in backup-only mode without GITLAB_TOKEN")

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_github_repos")
@patch("holocron.__main__.sync_one_repo")
@patch("time.sleep")
def test_main_watch_loop(mock_sleep, mock_sync, mock_get_repos, mock_parse):
    # Test watch mode loop
    # We make mock_sleep raise an exception to break the infinite loop
    args = MagicMock()
    args.watch = True
    args.interval = 60
    args.concurrency = 1
    args.window = 10
    args.dry_run = False
    args.verbose = False
    args.storage = "/tmp"
    args.backup_only = False
    mock_parse.return_value = args

    # 2 cycles:
    # Cycle 1: repo1 with timestamp A -> Should sync
    # Cycle 2: repo1 with timestamp A -> Should SKIP (redundant)
    # Cycle 3: Break
    
    repo1_v1 = {'name': 'repo1', 'pushed_at': '2023-01-01T12:00:00Z'}
    
    mock_get_repos.side_effect = [
        [repo1_v1], # Cycle 1
        [repo1_v1]  # Cycle 2
    ]
    
    # Break loop after 2 sleeps (end of cycle 2)
    mock_sleep.side_effect = [None, RuntimeError("Break Loop")]

    with pytest.raises(RuntimeError, match="Break Loop"):
        main()
        
    # Sync should only be called ONCE despite 2 cycles, because of redundancy check
    assert mock_sync.call_count == 1

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_github_repos")
@patch("holocron.__main__.sync_one_repo")
@patch("holocron.__main__.log")
def test_main_verbose_no_sync(mock_log, mock_sync, mock_get_repos, mock_parse):
    # Test path where sync_count is 0 and verbose is True
    args = MagicMock()
    args.watch = False
    args.concurrency = 1
    args.backup_only = False
    args.dry_run = False
    args.verbose = True
    args.storage = "/tmp"
    mock_parse.return_value = args
    
    mock_get_repos.return_value = [] # No repos
    
    main()
    
    # Check for "No changes detected" log
    log_calls = [str(call) for call in mock_log.call_args_list]
    assert any("No changes detected" in call for call in log_calls)

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_github_repos")
@patch("holocron.__main__.sync_one_repo")
@patch("holocron.__main__.log")
def test_main_exception_logging(mock_log, mock_sync, mock_get_repos, mock_parse):
    # Test exception within thread execution
    args = MagicMock()
    args.watch = False
    args.concurrency = 1
    args.backup_only = False
    args.storage = "/tmp"
    mock_parse.return_value = args
    
    repo = {'name': 'fail', 'clone_url': 'url', 'pushed_at': 'ts'}
    mock_get_repos.return_value = [repo]
    
    mock_sync.side_effect = Exception("Thread Boom")
    
    main()
    
    # Should catch and log exception
    mock_log.assert_called()
    log_calls = [str(call) for call in mock_log.call_args_list]
    assert any("generated an exception: Thread Boom" in call for call in log_calls)
