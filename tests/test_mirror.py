import pytest
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from holocron.mirror import needs_sync, sync_one_repo
from holocron.providers.base import Repository

def test_needs_sync_true():
    # 5 minutes ago
    pushed_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
    repo = Repository(name="test", clone_url="url", pushed_at=pushed_at)
    assert needs_sync(repo, window_minutes=10) is True

def test_needs_sync_false():
    # 20 minutes ago
    pushed_at = (datetime.now(timezone.utc) - timedelta(minutes=20)).replace(tzinfo=None)
    repo = Repository(name="test", clone_url="url", pushed_at=pushed_at)
    assert needs_sync(repo, window_minutes=10) is False

def test_needs_sync_no_timestamp():
    repo = Repository(name="test", clone_url="url", pushed_at=None)
    assert needs_sync(repo, window_minutes=10) is False

@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
def test_sync_one_repo_backup_only(mock_exists, mock_makedirs, mock_run):
    repo = Repository(name='test-repo', clone_url='https://github.com/user/test-repo.git')
    
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = "https://oauth2:token@github.com/user/test-repo.git"
    
    mock_exists.return_value = False # Repo Doesn't exist, so it clones
    
    sync_one_repo(repo, storage_path="/tmp/mirror", backup_only=True, source_provider=source_provider)
    
    # Verify Clone called
    assert mock_run.call_count >= 1
    call_args = mock_run.call_args_list[0][0][0]
    assert "git" in call_args
    assert "clone" in call_args
    assert "--mirror" in call_args

@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_sync_one_repo_checkout(mock_logger, mock_exists, mock_makedirs, mock_run):
    repo = Repository(name='test-repo', clone_url='https://github.com/user/test-repo.git')
    
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = "url"

    # Mocking existence: 
    # 1. repo_dir exists? True (Assume mirror exists, skip clone)
    # 2. checkout_dir exists? False (Trigger checkout clone)
    
    # Note: os.path.join is used, so valid paths are checked.
    # The order of checks in code: 
    # _ensure_local_mirror check -> repo_dir
    # _update_sidecar_checkout check -> checkout_dir
    mock_exists.side_effect = [True, False] 
    
    sync_one_repo(repo, storage_path="/tmp/mirror", backup_only=True, checkout=True, source_provider=source_provider)
    
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
@patch("holocron.mirror.logger")
def test_sync_one_repo_git_failure(mock_logger, mock_exists, mock_makedirs, mock_run):
    # Test exception handling
    repo = Repository(name='fail-repo', clone_url='https://github.com/cnt/fail.git')
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = "url"
    
    mock_exists.return_value = False # Try to clone
    
    # Raise CalledProcessError on clone
    err = subprocess.CalledProcessError(128, ["git", "clone"], stderr=b"Authentication failed")
    mock_run.side_effect = err
    
    sync_one_repo(repo, storage_path="/tmp", source_provider=source_provider)
    
    # Should catch and log
    mock_logger.error.assert_called()
    assert "ERROR syncing fail-repo" in mock_logger.error.call_args[0][0]
    
@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_sync_one_repo_checkout_failure(mock_logger, mock_exists, mock_makedirs, mock_run):
    # Test failure during checkout update
    repo = Repository(name='checkout-fail', clone_url='url')
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = "url"
    
    # 1. repo_dir exists (True) -> Fetch
    # 2. checkout_dir exists (True) -> Pull
    mock_exists.return_value = True
    
    # Fetch succeeds, Pull fails
    err = subprocess.CalledProcessError(1, ["git", "pull"], stderr=b"Merge conflict")
    mock_run.side_effect = [None, err]  # 1st call (fetch), 2nd call (pull)

    sync_one_repo(repo, storage_path="/tmp", backup_only=True, checkout=True, source_provider=source_provider)
    
    # Should log error from checkout (not sync error)
    mock_logger.error.assert_called()
    assert "Failed to update checkout" in mock_logger.error.call_args[0][0]
    
@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_sync_one_repo_dry_run(mock_logger, mock_exists, mock_makedirs, mock_run):
    repo = Repository(name='dry-repo', clone_url='url')
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = "url"
    destination_provider = MagicMock()
    destination_provider.get_remote_url.return_value = "dest_url"
    
    sync_one_repo(repo, storage_path="/tmp", dry_run=True, source_provider=source_provider, destination_provider=destination_provider)
    
    # Should not call git
    mock_run.assert_not_called()
    assert mock_logger.info.call_count >= 1
    assert "DRY-RUN" in mock_logger.info.call_args[0][0]

@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_sync_one_repo_full_flow_success(mock_logger, mock_exists, mock_makedirs, mock_run):
    repo = Repository(name='repo', clone_url='url')
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = "src_url"
    destination_provider = MagicMock()
    destination_provider.get_remote_url.return_value = "dest_url"
    
    # 1. Exists -> True (Fetch)
    # 2. Checkout dir Exists -> True (Pull)
    mock_exists.return_value = True
    
    sync_one_repo(repo, storage_path="/tmp", checkout=True, source_provider=source_provider, destination_provider=destination_provider)
    
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
