import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Optional

from .logger import log_execution, logger
from .providers.base import Provider, Repository

# Pattern to mask tokens in subprocess error output
_TOKEN_PATTERN = re.compile(r"(oauth2:|x-token-auth:)[^@\s]+@")


def _mask_stderr(text: str) -> str:
    """Mask any tokens that might appear in git error output."""
    return _TOKEN_PATTERN.sub(r"\1***@", text)


def needs_sync(repo: Repository, window_minutes: int) -> bool:
    """
    Checks if the repository has been pushed to within the last `window_minutes`.
    """
    if not repo.pushed_at:
        return False

    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC
    pushed_at = repo.pushed_at

    return (now - pushed_at) < timedelta(minutes=window_minutes)


@log_execution
def sync_one_repo(
    repo: Repository,
    storage_path: str,
    dry_run: bool = False,
    backup_only: bool = False,
    checkout: bool = False,
    source_provider: Optional[Provider] = None,
    destination_provider: Optional[Provider] = None,
) -> None:
    repo_dir = os.path.join(storage_path, f"{repo.name}.git")

    # 1. Construct Secure URLs
    if source_provider is None:
        raise ValueError("Source provider is required")
    source_url = source_provider.get_remote_url(repo)

    destination_url: Optional[str] = None
    if not backup_only and destination_provider:
        destination_url = destination_provider.get_remote_url(repo)

    # 2. Dry Run Check
    if dry_run:
        target_msg = destination_url if not backup_only else "(Local Backup Only)"
        logger.info(f"[DRY-RUN] Would sync '{repo.name}' -> '{target_msg}'")
        return

    # 3. Create Storage Directory if needed
    os.makedirs(storage_path, exist_ok=True)

    # 4. Execute Sync Steps
    try:
        _ensure_local_mirror(repo, repo_dir, source_url)

        if not backup_only and destination_provider and destination_url:
            destination_provider.prepare_push(repo)
            _push_to_destination(repo, repo_dir, destination_url)
        else:
            logger.info(f"[{repo.name}] Successfully backed up locally.")

        if checkout:
            _update_sidecar_checkout(repo, repo_dir)

    except subprocess.CalledProcessError as e:
        stderr_text = _mask_stderr(str(e.stderr)) if e.stderr else str(e)
        logger.error(f"ERROR syncing {repo.name}: {e}\nOutput: {stderr_text}")


def _ensure_local_mirror(repo: Repository, repo_dir: str, source_url: str) -> None:
    """Clones or fetches the local bare mirror."""
    if not os.path.exists(repo_dir):
        logger.info(f"[{repo.name}] Cloning new mirror...")
        try:
            subprocess.run(
                ["git", "clone", "--mirror", "--quiet", source_url, repo_dir],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            err_msg = _mask_stderr(e.stderr.decode().strip()) if e.stderr else str(e)
            raise subprocess.CalledProcessError(e.returncode, e.cmd, output=e.output, stderr=err_msg) from e
    else:
        logger.debug(f"[{repo.name}] Fetching updates...")
        try:
            subprocess.run(
                ["git", "-C", repo_dir, "fetch", "--quiet", "-p", "origin"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            err_msg = _mask_stderr(e.stderr.decode().strip()) if e.stderr else str(e)
            raise subprocess.CalledProcessError(e.returncode, e.cmd, output=e.output, stderr=err_msg) from e


def _push_to_destination(repo: Repository, repo_dir: str, destination_url: str) -> None:
    """Pushes the local mirror to the destination."""
    subprocess.run(
        ["git", "-C", repo_dir, "remote", "set-url", "--push", "origin", destination_url],
        check=True,
        stderr=subprocess.DEVNULL,
    )

    try:
        subprocess.run(
            ["git", "-C", repo_dir, "push", "--mirror", "--quiet"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        logger.info(f"[{repo.name}] Successfully synced to destination.")
    except subprocess.CalledProcessError as e:
        err_msg = _mask_stderr(e.stderr.decode().strip()) if e.stderr else str(e)
        raise subprocess.CalledProcessError(e.returncode, e.cmd, output=e.output, stderr=err_msg) from e


def _update_sidecar_checkout(repo: Repository, repo_dir: str) -> None:
    """Updates or clones a separate non-bare checkout for inspection."""
    checkout_dir = repo_dir.replace(".git", "")

    if not os.path.exists(checkout_dir):
        logger.debug(f"[{repo.name}] Creating checkout...")
        try:
            subprocess.run(
                ["git", "clone", "--quiet", repo_dir, checkout_dir],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            err_msg = _mask_stderr(e.stderr.decode().strip()) if e.stderr else str(e)
            logger.error(f"[{repo.name}] Failed to create checkout: {err_msg}")
    else:
        logger.debug(f"[{repo.name}] Updating checkout...")
        try:
            subprocess.run(
                ["git", "-C", checkout_dir, "pull", "--quiet"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            err_msg = _mask_stderr(e.stderr.decode().strip()) if e.stderr else str(e)
            logger.error(f"[{repo.name}] Failed to update checkout: {err_msg}")
