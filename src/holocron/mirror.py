import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from .logger import logger, log_execution
from .utils import redact

# Per-repo locks. A repo can be scheduled for sync by both the poll loop and an
# incoming webhook at the same time; running two `git fetch`/`git push` against
# the same bare mirror dir concurrently corrupts it. We serialize per repo name.
_repo_locks = {}
_repo_locks_guard = threading.Lock()


def _get_repo_lock(name):
    """Returns a process-wide lock unique to `name`, creating it on first use."""
    with _repo_locks_guard:
        lock = _repo_locks.get(name)
        if lock is None:
            lock = threading.Lock()
            _repo_locks[name] = lock
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
    repo_dir = os.path.join(storage_path, f"{repo.name}.git")
    
    # 1. Construct Secure URLs
    source_url = source_provider.get_remote_url(repo)
    
    destination_url = None
    if not backup_only and destination_provider:
        destination_url = destination_provider.get_remote_url(repo)

    # 2. Dry Run Check
    if dry_run:
        target_msg = destination_url if not backup_only else "(Local Backup Only)"
        logger.info(f"[DRY-RUN] Would sync '{repo.name}' -> '{target_msg}'")
        return

    # 3. Create Storage Directory if needed
    os.makedirs(storage_path, exist_ok=True)

    # 4. Execute Sync Steps (serialized per repo to keep concurrent poll- and
    # webhook-triggered syncs from clobbering the same bare mirror).
    with _get_repo_lock(repo.name):
        try:
            _ensure_local_mirror(repo, repo_dir, source_url)

            if not backup_only:
                 destination_provider.prepare_push(repo)
                 _push_to_destination(repo, repo_dir, destination_url)
            else:
                logger.info(f"[{repo.name}] Successfully backed up locally.")

            if checkout:
                _update_sidecar_checkout(repo, repo_dir)

        except subprocess.CalledProcessError as e:
            # redact: e (its cmd) and e.stderr can echo the token-embedded remote URL.
            logger.error(redact(f"ERROR syncing {repo.name}: {e}\nOutput: {e.stderr}"))

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

def _update_sidecar_checkout(repo, repo_dir):
    """Updates or clones a separate non-bare checkout for inspection."""
    checkout_dir = repo_dir.replace(".git", "")
    
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
