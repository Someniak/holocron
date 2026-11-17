import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from .logger import logger, log_execution
from .utils import (
    checkout_dir_for,
    is_safe_clone_url,
    is_safe_full_name,
    is_safe_repo_name,
    mirror_dir,
    redact,
    repository_key,
)

# Per-repo locks. A repo can be scheduled for sync by both the poll loop and an
# incoming webhook at the same time; running two `git fetch`/`git push` against
# the same bare mirror dir concurrently corrupts it. We serialize per repo
# identity (owner/repo when known) so two same-named repos under different
# owners do not needlessly serialize against each other.
_repo_locks = {}
_repo_locks_guard = threading.Lock()


def _get_repo_lock(key):
    """Returns a process-wide lock unique to `key`, creating it on first use."""
    with _repo_locks_guard:
        lock = _repo_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _repo_locks[key] = lock
        return lock


def needs_sync(repo, window_minutes):
    """
    Checks if the repository has been pushed to within the last `window_minutes`.
    """
    if not repo.pushed_at:
        return False
        
    now = datetime.now(timezone.utc).replace(tzinfo=None) # naive UTC
    # pushed_at is already a datetime object from the provider (naive UTC usually)
    pushed_at = repo.pushed_at

    # Check if the difference is inside our window
    return (now - pushed_at) < timedelta(minutes=window_minutes)

@log_execution
def sync_one_repo(repo, storage_path, dry_run=False, backup_only=False, checkout=False, source_provider=None, destination_provider=None):
    # 0. Validate untrusted repository fields before they reach the filesystem
    # or `git`. `repo.name` is used as a path component and `repo.clone_url` is
    # handed to `git clone`; a crafted name (path traversal) or clone URL (git's
    # `ext::` transport => command execution) would otherwise be dangerous. This
    # is the single chokepoint every sync path (poll + webhook) funnels through.
    if not is_safe_repo_name(repo.name) or not is_safe_clone_url(repo.clone_url):
        logger.error(f"Refusing to sync repository: unsafe name or clone URL (name={repo.name!r}).")
        return

    if not is_safe_full_name(repo.full_name):
        logger.error(f"Refusing to sync repository: unsafe full name (full_name={repo.full_name!r}).")
        return

    repo_dir = mirror_dir(storage_path, repo)

    # 1. Construct Secure URLs. get_remote_url pins credentials to the
    # configured origin and raises if the repo's URL points elsewhere (e.g. a
    # forged payload trying to exfiltrate the token) -- refuse rather than sync.
    try:
        source_url = source_provider.get_remote_url(repo)

        destination_url = None
        if not backup_only and destination_provider:
            destination_url = destination_provider.get_remote_url(repo)
    except ValueError as exc:
        logger.error(f"Refusing to sync '{repo.name}': {exc}")
        return

    # 2. Dry Run Check
    if dry_run:
        # redact: the destination URL embeds the operator's live push token.
        target_msg = redact(destination_url) if not backup_only else "(Local Backup Only)"
        logger.info(f"[DRY-RUN] Would sync '{repo.name}' -> '{target_msg}'")
        return

    # 3. Create Storage Directory if needed
    os.makedirs(storage_path, exist_ok=True)

    # 4. Execute Sync Steps (serialized per repo to keep concurrent poll- and
    # webhook-triggered syncs from clobbering the same bare mirror).
    with _get_repo_lock(repository_key(repo)):
        try:
            _ensure_local_mirror(repo, repo_dir, source_url)

            if not backup_only:
                 destination_provider.prepare_push(repo)
                 _push_to_destination(repo, repo_dir, destination_url)
            else:
                logger.info(f"[{repo.name}] Successfully backed up locally.")

            if checkout:
                _update_sidecar_checkout(repo, repo_dir, storage_path)

        except subprocess.CalledProcessError as e:
            # The logger scrubs URL credentials from every record, including this
            # one, whose cmd/stderr can echo the token-embedded remote URL.
            logger.error(f"ERROR syncing {repo.name}: {e}\nOutput: {e.stderr}")

def _mirror_origin(repo_dir):
    """
    The source URL currently recorded as `origin` in the mirror, or None if the
    mirror has no readable origin.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_dir, "remote", "get-url", "origin"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    stdout = getattr(result, "stdout", None)
    if not stdout:
        return None
    origin = stdout.decode(errors="replace").strip()
    return origin or None


def _same_origin(recorded, wanted):
    """
    True if two source URLs address the same upstream.

    Compared with credentials stripped, because the recorded origin and the
    freshly built URL carry tokens that differ by design.
    """
    return redact(recorded) == redact(wanted)


def _ensure_local_mirror(repo, repo_dir, source_url):
    """Clones or fetches the local bare mirror."""
    if not os.path.exists(repo_dir):
        logger.info(f"[{repo.name}] Cloning new mirror...")
        try:
            subprocess.run(["git", "clone", "--mirror", "--quiet", source_url, repo_dir], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode().strip() if e.stderr else str(e)
            raise subprocess.CalledProcessError(e.returncode, e.cmd, output=e.output, stderr=err_msg)
    else:
        # A mirror directory is only ever reused for the upstream it was cloned
        # from. Fetching `origin` blindly would push one project's content into
        # another's destination (silently, with a zero exit status) if two
        # repositories ever resolved to the same directory.
        recorded = _mirror_origin(repo_dir)
        if recorded is not None and not _same_origin(recorded, source_url):
            logger.warning(
                f"[{repo.name}] Mirror origin {redact(recorded)!r} does not match "
                f"the current source {redact(source_url)!r}; re-pointing the mirror."
            )
            subprocess.run(
                ["git", "-C", repo_dir, "remote", "set-url", "origin", source_url],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

        logger.debug(f"[{repo.name}] Fetching updates...")
        try:
            subprocess.run(["git", "-C", repo_dir, "fetch", "--quiet", "-p", "origin"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode().strip() if e.stderr else str(e)
            raise subprocess.CalledProcessError(e.returncode, e.cmd, output=e.output, stderr=err_msg)

def _push_to_destination(repo, repo_dir, destination_url):
    """Pushes the local mirror to the destination (GitLab)."""
    # Ensure push remote is set (optional but good practice)
    subprocess.run(["git", "-C", repo_dir, "remote", "set-url", "--push", "origin", destination_url], check=True, stderr=subprocess.DEVNULL)

    try:
        subprocess.run(["git", "-C", repo_dir, "push", "--mirror", "--quiet"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        logger.info(f"[{repo.name}] Successfully synced to GitLab.")
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode().strip() if e.stderr else str(e)
        raise subprocess.CalledProcessError(e.returncode, e.cmd, output=e.output, stderr=err_msg)

def _update_sidecar_checkout(repo, repo_dir, storage_path):
    """Updates or clones a separate non-bare checkout for inspection."""
    checkout_dir = checkout_dir_for(repo_dir)

    # The checkout must be a proper subdirectory of the storage root. A name that
    # degenerates (e.g. `.git`) would otherwise resolve the checkout onto the
    # storage root, or onto the mirror itself, and `git pull` would run in a
    # repository holocron never cloned.
    try:
        storage_root = os.path.abspath(storage_path)
        checkout_abs = os.path.abspath(checkout_dir)
        inside_root = os.path.commonpath([storage_root, checkout_abs]) == storage_root
    except ValueError:
        inside_root = False
        storage_root = checkout_abs = None
    if (
        not inside_root
        or checkout_abs in (storage_root, os.path.abspath(repo_dir))
    ):
        logger.error(redact(f"[{repo.name}] Refusing unsafe checkout path: '{checkout_dir}'"))
        return

    if not os.path.exists(checkout_dir):
        logger.debug(f"[{repo.name}] Creating checkout...")
        try:
            subprocess.run(["git", "clone", "--quiet", repo_dir, checkout_dir], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode().strip() if e.stderr else str(e)
            logger.error(redact(f"[{repo.name}] Failed to create checkout: {err_msg}"))
    else:
        logger.debug(f"[{repo.name}] Updating checkout...")
        try:
            subprocess.run(["git", "-C", checkout_dir, "pull", "--quiet"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode().strip() if e.stderr else str(e)
            logger.error(redact(f"[{repo.name}] Failed to update checkout: {err_msg}"))
