import os
import subprocess
from datetime import datetime, timedelta, timezone
from .logger import logger, log_execution

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
def sync_one_repo(repo, args, source_provider, destination_provider=None):
    name = repo.name
    repo_dir = os.path.join(args.storage, f"{name}.git")
    
    # 1. Construct Secure URLs (Injecting tokens)
    # Use the provider to get the clone URL
    gh_clone_url = source_provider.get_remote_url(repo)
    
    # Use the provider to get the target URL if we are syncing to a destination
    if not args.backup_only and destination_provider:
        gl_target_url = destination_provider.get_remote_url(repo)
    else:
        gl_target_url = None

    # 2. Dry Run Check
    if args.dry_run:
        target_msg = gl_target_url if not args.backup_only else "(Local Backup Only)"
        logger.info(f"[DRY-RUN] Would sync '{name}' -> '{target_msg}'")
        return

    # 3. Create Storage Directory if needed
    os.makedirs(args.storage, exist_ok=True)

    # 4. Git Operations
    try:
        # Use --quiet to suppress progress bars which mess up parallel logs
        # We also redirect stderr to DEVNULL to be completely silent unless it fails (which check=True handles by raising)
        # Note: If git fails, we won't see the error message if we silence stderr perfectly. 
        # A better approach is capture_output=True and log stderr on error.
        
        if not os.path.exists(repo_dir):
            logger.info(f"[{name}] Cloning new mirror...")
            try:
                subprocess.run(["git", "clone", "--mirror", "--quiet", gh_clone_url, repo_dir], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                # Decode stderr for the log
                err_msg = e.stderr.decode().strip() if e.stderr else str(e)
                raise subprocess.CalledProcessError(e.returncode, e.cmd, output=e.output, stderr=err_msg)

        else:
            logger.debug(f"[{name}] Fetching updates...")
            try:
                subprocess.run(["git", "-C", repo_dir, "fetch", "--quiet", "-p", "origin"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                err_msg = e.stderr.decode().strip() if e.stderr else str(e)
                raise subprocess.CalledProcessError(e.returncode, e.cmd, output=e.output, stderr=err_msg)
        
        # If not backup only, ensure push remote is set (optional but good practice if it changes)
        if not args.backup_only:
             subprocess.run(["git", "-C", repo_dir, "remote", "set-url", "--push", "origin", gl_target_url], check=True, stderr=subprocess.DEVNULL)

        # 5. Push to GitLab
        # We always push to ensure the mirror is exact, UNLESS backup-only mode is on
        if not args.backup_only:
            try:
                subprocess.run(["git", "-C", repo_dir, "push", "--mirror", "--quiet"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                logger.info(f"[{name}] Successfully synced to GitLab.")
            except subprocess.CalledProcessError as e:
                err_msg = e.stderr.decode().strip() if e.stderr else str(e)
                raise subprocess.CalledProcessError(e.returncode, e.cmd, output=e.output, stderr=err_msg)
        else:
            logger.info(f"[{name}] Successfully backed up locally.")
        
        # 6. Optional Checkout (Sidecar)
        if args.checkout:
            # Derived directly from repo_dir (which ends in .git)
            checkout_dir = repo_dir.replace(".git", "")
            
            if not os.path.exists(checkout_dir):
                logger.debug(f"[{name}] Creating checkout...")
                try:
                    # Clone from the local mirror
                    subprocess.run(["git", "clone", "--quiet", repo_dir, checkout_dir], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                except subprocess.CalledProcessError as e:
                    err_msg = e.stderr.decode().strip() if e.stderr else str(e)
                    logger.error(f"[{name}] Failed to create checkout: {err_msg}")
            else:
                logger.debug(f"[{name}] Updating checkout...")
                try:
                    # We use pull to update the current branch. 
                    # If this fails (e.g. merge conflict, though unlikely for a backup), we catch it.
                    subprocess.run(["git", "-C", checkout_dir, "pull", "--quiet"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                except subprocess.CalledProcessError as e:
                    err_msg = e.stderr.decode().strip() if e.stderr else str(e)
                    logger.error(f"[{name}] Failed to update checkout: {err_msg}")
        
    except subprocess.CalledProcessError as e:
        logger.error(f"ERROR syncing {name}: {e}")
