import os
import subprocess
from datetime import datetime, timedelta, timezone
from logger import log
from config import GITLAB_API_URL

def needs_sync(repo, window_minutes):
    """
    Returns True if the repo was pushed to within the 'window_minutes'.
    """
    pushed_at_str = repo.get('pushed_at')
    if not pushed_at_str:
        return False
        
    # Parse ISO timestamp (Handles the 'Z' for UTC)
    pushed_at = datetime.fromisoformat(pushed_at_str.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    
    # Check if the difference is inside our window
    return (now - pushed_at) < timedelta(minutes=window_minutes)

def sync_one_repo(repo, args, github_token, gitlab_token):
    name = repo['name']
    repo_dir = os.path.join(args.storage, f"{name}.git")
    
    # 1. Construct Secure URLs (Injecting tokens)
    # Note: We use the OAuth2 syntax for GitLab
    gh_clone_url = repo['clone_url'].replace("https://", f"https://oauth2:{github_token}@")
    
    # We assume the GitLab group matches the GitHub username or is defined in the URL
    # For this script, we construct a generic target URL. 
    # In a real scenario, you might want to customize the group logic.
    gl_target_url = f"{GITLAB_API_URL.replace('/api/v4', '')}/{name}.git".replace("http://", f"http://oauth2:{gitlab_token}@")

    # 2. Dry Run Check
    if args.dry_run:
        log(f"[DRY-RUN] Would sync '{name}' -> '{gl_target_url}'")
        return

    # 3. Create Storage Directory if needed
    os.makedirs(args.storage, exist_ok=True)

    # 4. Git Operations
    try:
        if not os.path.exists(repo_dir):
            log(f"[{name}] Cloning new mirror...")
            subprocess.run(["git", "clone", "--mirror", gh_clone_url, repo_dir], check=True, stdout=subprocess.DEVNULL)
            # Set push remote
            subprocess.run(["git", "-C", repo_dir, "remote", "set-url", "--push", "origin", gl_target_url], check=True)
        else:
            log(f"[{name}] Fetching updates...", verbose_only=True, is_verbose_mode=args.verbose)
            subprocess.run(["git", "-C", repo_dir, "fetch", "-p", "origin"], check=True, stdout=subprocess.DEVNULL)

        # 5. Push to GitLab
        # We always push to ensure the mirror is exact
        subprocess.run(["git", "-C", repo_dir, "push", "--mirror"], check=True, stdout=subprocess.DEVNULL)
        log(f"[{name}] Successfully synced.")
        
    except subprocess.CalledProcessError as e:
        log(f"ERROR syncing {name}: {e}")
