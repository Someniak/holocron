import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from holocron.mirror import needs_sync, sync_one_repo
from holocron.providers.base import Repository
from holocron.utils import redact, is_safe_repo_name, is_safe_clone_url


def test_redact_strips_oauth_token():
    text = "fatal: could not read from https://oauth2:SECRETTOKEN@github.com/u/r.git"
    out = redact(text)
    assert "SECRETTOKEN" not in out
    assert "https://***@github.com/u/r.git" in out

def test_redact_strips_userpass():
    assert redact("http://user:pass@gitlab.local/x.git") == "http://***@gitlab.local/x.git"

def test_redact_noop_without_credentials():
    assert redact("plain error, no url") == "plain error, no url"
    assert redact(None) is None

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
def test_sync_one_repo_redacts_token_in_error(mock_exists, mock_makedirs, mock_run):
    # A git failure whose cmd/stderr echo the token-embedded URL must be redacted
    # before it reaches the logs. Asserted on the emitted stream rather than on a
    # mocked logger, because the scrubbing is a logger filter: mocking the logger
    # would bypass the very control under test.
    import io
    import logging
    from holocron.logger import logger as real_logger

    repo = Repository(name='secret-repo', clone_url='https://github.com/u/r.git')
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = "https://oauth2:SECRETTOKEN@github.com/u/r.git"

    mock_exists.return_value = False  # forces clone

    err = subprocess.CalledProcessError(
        128,
        ["git", "clone", "--mirror", "https://oauth2:SECRETTOKEN@github.com/u/r.git"],
        stderr=b"fatal: could not read from https://oauth2:SECRETTOKEN@github.com/u/r.git",
    )
    mock_run.side_effect = err

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("{message}", style="{"))
    original = real_logger.handlers[:]
    real_logger.handlers = [handler]
    try:
        sync_one_repo(repo, storage_path="/tmp", source_provider=source_provider)
    finally:
        real_logger.handlers = original

    logged = stream.getvalue()
    assert "ERROR syncing secret-repo" in logged
    assert "SECRETTOKEN" not in logged
    assert "***@github.com" in logged
    
@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_sync_one_repo_checkout_failure(mock_logger, mock_exists, mock_makedirs, mock_run):
    # Test failure during checkout update
    repo = Repository(name='checkout-fail', clone_url='https://github.com/u/checkout-fail.git')
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = "url"
    
    # 1. repo_dir exists (True) -> Fetch
    # 2. checkout_dir exists (True) -> Pull
    mock_exists.return_value = True
    
    # Fetch succeeds, Pull fails. Keyed on the command rather than call order so
    # the mirror-origin check (an extra `git remote get-url`) does not shift it.
    err = subprocess.CalledProcessError(1, ["git", "pull"], stderr=b"Merge conflict")

    def _run(cmd, *args, **kwargs):
        if "pull" in cmd:
            raise err
        return MagicMock(stdout=b"url")

    mock_run.side_effect = _run

    sync_one_repo(repo, storage_path="/tmp", backup_only=True, checkout=True, source_provider=source_provider)
    
    # Should log error from checkout (not sync error)
    mock_logger.error.assert_called()
    assert "Failed to update checkout" in mock_logger.error.call_args[0][0]
    
@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_sync_one_repo_dry_run(mock_logger, mock_exists, mock_makedirs, mock_run):
    repo = Repository(name='dry-repo', clone_url='https://github.com/u/dry-repo.git')
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
    repo = Repository(name='repo', clone_url='https://github.com/u/repo.git')
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


# --- Untrusted repository-field validation ---

def test_is_safe_repo_name():
    for good in ("my-repo", ".github", "a.b_c", "Repo123", "x"):
        assert is_safe_repo_name(good), good
    for bad in ("", ".", "..", "../etc", "a/b", "a\\b", "a b", "a\x00b", None, 123):
        assert not is_safe_repo_name(bad), bad


def test_is_safe_clone_url():
    for good in ("https://github.com/o/r.git", "http://gitlab.local/o/r.git"):
        assert is_safe_clone_url(good), good
    for bad in (
        "ext::sh -c id",          # git ext:: transport => command execution
        "file:///etc/passwd",     # local file transport
        "ssh://git@host/r.git",   # ssh transport
        "git://host/r.git",       # unauthenticated git transport
        "-oProxyCommand=evil",    # parsed by git as an option
        "/local/path",            # no scheme
        "",
        None,
    ):
        assert not is_safe_clone_url(bad), bad


@patch("subprocess.run")
@patch("os.makedirs")
@patch("holocron.mirror.logger")
def test_sync_one_repo_refuses_ext_transport(mock_logger, mock_makedirs, mock_run):
    """A crafted clone_url (ext:: => RCE) must never reach git."""
    repo = Repository(name="evil", clone_url="ext::sh -c 'touch /tmp/pwned'")
    source_provider = MagicMock()

    sync_one_repo(repo, storage_path="/tmp/mirror", backup_only=True, source_provider=source_provider)

    mock_run.assert_not_called()
    source_provider.get_remote_url.assert_not_called()
    assert mock_logger.error.called


@patch("subprocess.run")
@patch("os.makedirs")
@patch("holocron.mirror.logger")
def test_sync_one_repo_refuses_path_traversal_name(mock_logger, mock_makedirs, mock_run):
    """A crafted repo name (path traversal) must never reach git/the filesystem."""
    repo = Repository(name="../../etc/evil", clone_url="https://github.com/o/r.git")
    source_provider = MagicMock()

    sync_one_repo(repo, storage_path="/tmp/mirror", backup_only=True, source_provider=source_provider)

    mock_run.assert_not_called()
    assert mock_logger.error.called


# --- Mirror identity: distinct repositories must not share a mirror ---

def test_mirror_dir_is_distinct_for_same_slug_under_different_owners():
    from holocron.utils import mirror_dir

    a = mirror_dir("/storage", Repository(name="shared", clone_url="u", full_name="ownerA/shared"))
    b = mirror_dir("/storage", Repository(name="shared", clone_url="u", full_name="ownerB/shared"))
    assert a != b
    assert a == "/storage/ownerA/shared.git"
    assert b == "/storage/ownerB/shared.git"


def test_mirror_dir_falls_back_to_bare_slug():
    from holocron.utils import mirror_dir

    assert mirror_dir("/storage", Repository(name="solo", clone_url="u")) == "/storage/solo.git"


@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_sync_one_repo_refuses_unsafe_full_name(mock_logger, mock_exists, mock_makedirs, mock_run):
    """`full_name` also becomes a path component, so it is validated too."""
    repo = Repository(name="r", clone_url="https://github.com/o/r.git", full_name="../escape")
    source_provider = MagicMock()

    sync_one_repo(repo, storage_path="/tmp/mirror", backup_only=True, source_provider=source_provider)

    mock_run.assert_not_called()
    assert mock_logger.error.called


@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_ensure_local_mirror_repoints_mismatched_origin(mock_logger, mock_exists, mock_makedirs, mock_run):
    """A mirror whose recorded origin is another repository is re-pointed.

    Fetching the recorded origin blindly is what let one project's content reach
    another project's destination, silently and with a zero exit status.
    """
    repo = Repository(name="r", clone_url="https://github.com/o/r.git", full_name="o/r")
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = (
        "https://oauth2:TOKEN@github.com/o/r.git"
    )

    # Mirror exists; `git remote get-url origin` reports a *different* upstream.
    mock_exists.return_value = True
    mock_run.return_value = MagicMock(
        stdout=b"https://oauth2:OTHER@github.com/other-owner/r.git\n"
    )

    sync_one_repo(repo, storage_path="/tmp/mirror", backup_only=True, source_provider=source_provider)

    cmds = [call[0][0] for call in mock_run.call_args_list]
    set_url = [c for c in cmds if "set-url" in c and "origin" in c]
    assert set_url, cmds
    assert "https://oauth2:TOKEN@github.com/o/r.git" in set_url[0]
    assert any("fetch" in c for c in cmds)
    # The mismatch is reported, not silent -- and without leaking either token.
    warnings = " ".join(str(c) for c in mock_logger.warning.call_args_list)
    assert "does not match" in warnings
    assert "TOKEN" not in warnings and "OTHER" not in warnings


@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_ensure_local_mirror_keeps_matching_origin(mock_logger, mock_exists, mock_makedirs, mock_run):
    repo = Repository(name="r", clone_url="https://github.com/o/r.git", full_name="o/r")
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = "https://oauth2:TOKEN@github.com/o/r.git"

    mock_exists.return_value = True
    mock_run.return_value = MagicMock(stdout=b"https://oauth2:TOKEN@github.com/o/r.git\n")

    sync_one_repo(repo, storage_path="/tmp/mirror", backup_only=True, source_provider=source_provider)

    cmds = [call[0][0] for call in mock_run.call_args_list]
    assert not [c for c in cmds if "set-url" in c], cmds
    assert any("fetch" in c for c in cmds)


# --- Checkout paths: one-to-one with mirrors ---

def test_checkout_dir_strips_only_the_trailing_suffix():
    from holocron.utils import checkout_dir_for

    # Distinct valid names must not collapse onto one working tree.
    assert checkout_dir_for("/s/x.git") == "/s/x"
    assert checkout_dir_for("/s/x.git.git") == "/s/x.git"
    assert checkout_dir_for("/s/x.gitex.git") == "/s/x.gitex"
    # A path with no trailing suffix is left alone rather than rewritten.
    assert checkout_dir_for("/s/x.gitex") == "/s/x.gitex"


def test_repo_named_dot_git_is_rejected():
    """`.git` would make the checkout resolve onto the storage root itself."""
    assert is_safe_repo_name(".git") is False
    assert is_safe_repo_name(".GIT") is False
    assert is_safe_repo_name(".github") is True


@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_checkout_never_resolves_to_storage_root(mock_logger, mock_exists, mock_makedirs, mock_run):
    repo = Repository(name=".git", clone_url="https://github.com/o/r.git")
    source_provider = MagicMock()

    sync_one_repo(repo, storage_path="/tmp/mirror", backup_only=True, checkout=True,
                  source_provider=source_provider)

    mock_run.assert_not_called()
    assert mock_logger.error.called


# --- Dry-run must not log the destination credential ---

@patch("subprocess.run")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("holocron.mirror.logger")
def test_dry_run_does_not_log_destination_token(mock_logger, mock_exists, mock_makedirs, mock_run):
    repo = Repository(name="dry-repo", clone_url="https://github.com/u/dry-repo.git")
    source_provider = MagicMock()
    source_provider.get_remote_url.return_value = "https://oauth2:SOURCESECRET@github.com/u/dry-repo.git"
    destination_provider = MagicMock()
    destination_provider.get_remote_url.return_value = (
        "https://oauth2:DESTTOKEN@gitlab.acme-corp.example/acme/dry-repo.git"
    )

    sync_one_repo(repo, storage_path="/tmp", dry_run=True,
                  source_provider=source_provider, destination_provider=destination_provider)

    mock_run.assert_not_called()
    logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
    assert "DESTTOKEN" not in logged
    assert "***@gitlab.acme-corp.example" in logged
