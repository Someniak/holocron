import os
import sys
import pytest
import argparse
from unittest.mock import MagicMock, patch, call
from holocron.__main__ import main
from holocron.providers.base import Repository
from datetime import datetime

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh_token", "GITLAB_TOKEN": "gl_token"})
@patch("holocron.__main__.get_provider")
@patch("holocron.__main__.sync_one_repo")
@patch("holocron.__main__.logger")
def test_main_single_run(mock_logger, mock_sync, mock_get_provider, mock_parse):
    # Setup args: single run (not watch), dry run False
    args = argparse.Namespace(
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
        interval=10 # Required for run_sync_cycle? No, interval is used in main loop but config has it so it might be accessed
    )
    # Adding interval just in case, though technically run_sync_cycle only unpacks what it needs.
    # main loop uses interval in time.sleep(args.interval)
    
    # Actually run_sync_cycle unpacks "watch", "window" etc.
    # It seems safe.
    
    mock_parse.return_value = args

    # Mock Source Provider
    mock_source = MagicMock()
    repo1 = Repository(name='repo1', clone_url='url', pushed_at=datetime(2023,1,1))
    mock_source.fetch_repos.return_value = [repo1]
    
    # Mock Destination Provider
    mock_dest = MagicMock()
    
    mock_get_provider.side_effect = [mock_source, mock_dest]

    main()

    # Assertions
    mock_source.fetch_repos.assert_called_once()
    mock_sync.assert_called_once()
    assert mock_logger.info.call_count >= 1

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {}, clear=True) # Empty env
def test_main_missing_tokens(mock_parse):
    args = argparse.Namespace(
        backup_only=False,
        source="github",
        destination="gitlab",
        verbose=False, # Setup logger needs this
        credits=False,
        concurrency=1,
        watch=False,
        dry_run=False,
        storage="/tmp",
        window=10,
        checkout=False,
        gitlab_namespace=None,
        interval=10
    )
    mock_parse.return_value = args
    
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh"}, clear=True)
def test_main_missing_gitlab_token_normal_mode(mock_parse):
    # If backup_only is False, we NEED GitLab token
    args = argparse.Namespace(
        backup_only=False,
        source="github",
        destination="gitlab",
        verbose=False,
        credits=False,
        concurrency=1,
        watch=False,
        dry_run=False,
        storage="/tmp",
        window=10,
        checkout=False,
        gitlab_namespace=None,
        interval=10
    )
    mock_parse.return_value = args
    
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_provider")
def test_main_backup_only_no_gitlab_token(mock_get_provider, mock_parse):
    # Should NOT exit
    args = argparse.Namespace(
        backup_only=True,
        watch=False,
        concurrency=1,
        dry_run=False,
        verbose=False,
        storage="/tmp",
        source="github",
        destination="local", # implies backup_only=True usually, but set explicitly below
        credits=False,
        gitlab_namespace=None,
        window=10,
        checkout=False,
        interval=10
    )
    
    mock_parse.return_value = args
    
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
    # Test watch mode loop
    # We make mock_sleep raise an exception to break the infinite loop
    args = argparse.Namespace(
        watch=True,
        interval=60,
        concurrency=1,
        window=10,
        dry_run=False,
        verbose=False,
        storage="/tmp",
        backup_only=False,
        source="github",
        destination="gitlab",
        credits=False,
        gitlab_namespace=None,
        checkout=False
    )
    mock_parse.return_value = args

    # 2 cycles:
    # Cycle 1: repo1 with timestamp A -> Should sync
    # Cycle 2: repo1 with timestamp A -> Should SKIP (redundant)
    # Cycle 3: Break
    
    pushed_at = datetime(2023,1,1,12,0,0)
    repo1 = Repository(name='repo1', clone_url='url', pushed_at=pushed_at)
    
    mock_source = MagicMock()
    mock_source.fetch_repos.side_effect = [
        [repo1], # Cycle 1
        [repo1]  # Cycle 2
    ]
    mock_dest = MagicMock()
    
    mock_get_provider.return_value = mock_source
    
    # Break loop after 2 sleeps (end of cycle 2)
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
    # Test path where sync_count is 0 and verbose is True
    args = argparse.Namespace(
        watch=False,
        concurrency=1,
        backup_only=False,
        dry_run=False,
        verbose=True,
        storage="/tmp",
        source="github",
        destination="gitlab",
        credits=False,
        gitlab_namespace=None,
        window=10,
        checkout=False,
        interval=10
    )
    mock_parse.return_value = args
    
    mock_source = MagicMock()
    mock_source.fetch_repos.return_value = [] # No repos
    mock_get_provider.return_value = mock_source
    
    main()
    
    # Check for "No changes detected" log
    mock_logger.debug.assert_called()
    log_calls = [str(call) for call in mock_logger.debug.call_args_list]
    assert any("No changes detected" in call for call in log_calls)

@patch("holocron.__main__.parse_args")
@patch.dict(os.environ, {"GITHUB_TOKEN": "gh", "GITLAB_TOKEN": "gl"})
@patch("holocron.__main__.get_provider")
@patch("holocron.__main__.sync_one_repo")
@patch("holocron.__main__.logger")
def test_main_exception_logging(mock_logger, mock_sync, mock_get_provider, mock_parse):
    # Test exception within thread execution
    args = argparse.Namespace(
        watch=False,
        concurrency=1,
        backup_only=False,
        storage="/tmp",
        verbose=False,
        source="github",
        destination="gitlab",
        credits=False,
        gitlab_namespace=None,
        window=10,
        checkout=False,
        dry_run=False,
        interval=10
    )
    mock_parse.return_value = args
    
    repo = Repository(name='fail', clone_url='url', pushed_at=datetime(2023,1,1))
    mock_source = MagicMock()
    mock_source.fetch_repos.return_value = [repo]
    mock_get_provider.return_value = mock_source
    
    mock_sync.side_effect = Exception("Thread Boom")
    
    main()
    
    # Should catch and log exception
    mock_logger.error.assert_called()
    log_calls = [str(call) for call in mock_logger.error.call_args_list]
    assert any("generated an exception: Thread Boom" in call for call in log_calls)
