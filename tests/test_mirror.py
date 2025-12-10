import pytest
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from mirror import needs_sync, sync_one_repo

def test_needs_sync_true():
    # 5 minutes ago
    pushed_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    repo = {'pushed_at': pushed_at}
    assert needs_sync(repo, window_minutes=10) is True

def test_needs_sync_false():
    # 20 minutes ago
    pushed_at = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
    repo = {'pushed_at': pushed_at}
    assert needs_sync(repo, window_minutes=10) is False

def test_needs_sync_no_timestamp():
    repo = {}
    assert needs_sync(repo, window_minutes=10) is False

@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
def test_sync_one_repo_backup_only(mock_exists, mock_makedirs, mock_run):
    args = MagicMock()
    args.backup_only = True
    args.checkout = False
    args.dry_run = False
    args.storage = "/tmp/mirror"
    args.verbose = False
    
    repo = {
        'name': 'test-repo',
        'clone_url': 'https://github.com/user/test-repo.git'
    }
    
    mock_exists.return_value = False # Repo Doesn't exist, so it clones
    
    sync_one_repo(repo, args, "gh_token", "gl_token")
    
    # Verify Clone called
    assert mock_run.call_count >= 1
    call_args = mock_run.call_args_list[0][0][0]
    assert "git" in call_args
    assert "clone" in call_args
    assert "--mirror" in call_args

@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
def test_sync_one_repo_checkout(mock_exists, mock_makedirs, mock_run):
    args = MagicMock()
    args.backup_only = True
    args.checkout = True
    args.dry_run = False
    args.storage = "/tmp/mirror"
    args.verbose = False
    
    repo = {
        'name': 'test-repo',
        'clone_url': 'https://github.com/user/test-repo.git'
    }
    
    # Mocking existence: 
    # 1. repo_dir exists? True (Assume mirror exists, skip clone)
    # 2. checkout_dir exists? False (Trigger checkout clone)
    mock_exists.side_effect = [True, False] 
    
    sync_one_repo(repo, args, "gh_token", "gl_token")
    
    # Logic path:
    # 1. Fetch mirror
    # 2. Clone checkout
    
    # We expect multiple calls. Let's check args of the checkout clone
    checkout_call_found = False
    for call in mock_run.call_args_list:
        cmd = call[0][0]
        if "clone" in cmd and "/tmp/mirror/test-repo" in cmd and "/tmp/mirror/test-repo.git" in cmd:
             checkout_call_found = True
    
    assert checkout_call_found

@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
def test_sync_one_repo_git_failure(mock_exists, mock_makedirs, mock_run):
    # Test exception handling
    args = MagicMock()
    args.backup_only = False
    args.dry_run = False
    args.storage = "/tmp"
    args.verbose = True # To trigger verbose logging path if any
    
    repo = {'name': 'fail-repo', 'clone_url': 'https://github.com/cnt/fail.git'}
    
    mock_exists.return_value = False # Try to clone
    
    # Raise CalledProcessError on clone
    err = subprocess.CalledProcessError(128, ["git", "clone"], stderr=b"Authentication failed")
    mock_run.side_effect = err
    
    # Should not raise, but log error (caught in sync_one_repo)
    # We assume the function catches and logs.
    # Note: The implementation uses 'log()' which prints. We can mock log if we want to assert it was called.
    # But fundamentally we just want to ensure it doesn't crash the thread.
    
    # However, if sync_one_repo raises (it re-raises caught exception?? No wait)
    # Let's check source code: 
    # except subprocess.CalledProcessError as e: log(...)
    # It catches it. So it should be safe.
    
    sync_one_repo(repo, args, "token", "token")
    # Clean exit
    
@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
def test_sync_one_repo_checkout_failure(mock_exists, mock_makedirs, mock_run):
    # Test failure during checkout update
    args = MagicMock()
    args.checkout = True
    args.backup_only = True # Skip gitlab push
    args.dry_run = False
    args.storage = "/tmp"
    args.verbose = True
    
    repo = {'name': 'checkout-fail', 'clone_url': 'url'}
    
    # 1. repo_dir exists (True) -> Fetch
    # 2. checkout_dir exists (True) -> Pull
    mock_exists.return_value = True
    
    # Fetch succeeds, Pull fails
    err = subprocess.CalledProcessError(1, ["git", "pull"], stderr=b"Merge conflict")
    mock_run.side_effect = [None, err]  # 1st call (fetch), 2nd call (pull)
    
@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("mirror.log")
def test_sync_one_repo_dry_run(mock_log, mock_exists, mock_makedirs, mock_run):
    args = MagicMock()
    args.dry_run = True
    args.backup_only = False
    args.storage = "/tmp"
    
    repo = {'name': 'dry-repo', 'clone_url': 'url'}
    
    sync_one_repo(repo, args, "t", "t")
    
    # Should not call git
    mock_run.assert_not_called()
    assert mock_log.call_count == 1
    assert "DRY-RUN" in mock_log.call_args[0][0]

@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("mirror.log")
def test_sync_one_repo_dry_run_backup_only(mock_log, mock_exists, mock_makedirs, mock_run):
    args = MagicMock()
    args.dry_run = True
    args.backup_only = True
    args.storage = "/tmp"
    
    repo = {'name': 'dry-repo', 'clone_url': 'url'}
    
    sync_one_repo(repo, args, "t", "t")
    
    # Should not call git
    mock_run.assert_not_called()
    assert mock_log.call_count == 1
    assert "Local Backup Only" in mock_log.call_args[0][0]

@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("mirror.log")
def test_sync_one_repo_full_flow_success(mock_log, mock_exists, mock_makedirs, mock_run):
    args = MagicMock()
    args.backup_only = False
    args.dry_run = False
    args.checkout = True
    args.storage = "/tmp"
    args.verbose = False
    
    repo = {'name': 'repo', 'clone_url': 'url'}
    
    # 1. Exists -> True (Fetch)
    # 2. Checkout dir Exists -> True (Pull)
    mock_exists.return_value = True
    
    sync_one_repo(repo, args, "t", "t")
    
    # Verify sequence:
    # 1. Fetch
    # 2. Remote set-url
    # 3. Push
    # 4. Checkout Pull
    
    cmds = [call[0][0] for call in mock_run.call_args_list]
    
    assert any("fetch" in cmd for cmd in cmds)
    assert any("remote" in cmd for cmd in cmds)
    assert any("push" in cmd for cmd in cmds)
    assert any("pull" in cmd for cmd in cmds)
    
@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("mirror.log")
def test_sync_one_repo_fetch_error(mock_log, mock_exists, mock_makedirs, mock_run):
    args = MagicMock()
    args.backup_only = True
    args.checkout = False
    args.dry_run = False
    args.storage = "/tmp"
    
    repo = {'name': 'repo', 'clone_url': 'url'}
    mock_exists.return_value = True # Update existing
    
    # Fetch fails
    err = subprocess.CalledProcessError(1, ["git", "fetch"], stderr=b"FetchErr")
    mock_run.side_effect = err
    
    # The code raises CalledProcessError to the caller (wrapped logic needs checking)
    # In mirror.py:
    # except subprocess.CalledProcessError as e: raise ...
    # Wait, inside the try/except block it raises?
    # lines 67-69: raises CalledProcessError
    # But line 87 catches it and logs it.
    
    sync_one_repo(repo, args, "t", "t")
    
    # Should log error
    mock_log.assert_called()
    assert "ERROR syncing repo" in mock_log.call_args[0][0]

@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("mirror.log")
def test_sync_one_repo_push_error(mock_log, mock_exists, mock_makedirs, mock_run):
    args = MagicMock()
    args.backup_only = False
    args.dry_run = False
    args.storage = "/tmp"
    
    repo = {'name': 'repo', 'clone_url': 'url'}
    mock_exists.return_value = True
    
    # Fetch OK, Remote OK, Push FAILS
    err = subprocess.CalledProcessError(1, ["git", "push"], stderr=b"PushErr")
    mock_run.side_effect = [None, None, err]
    
    sync_one_repo(repo, args, "t", "t")
    
    # Should log error
    mock_log.assert_called()
    assert "ERROR syncing repo" in mock_log.call_args[0][0]

