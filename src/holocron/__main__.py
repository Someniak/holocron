#!/usr/bin/env python3
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import from local modules
# Import from local modules
from .config import parse_args, validate_config, __author__, __license__, GITLAB_API_URL, GITHUB_API_URL
from .logger import log
from .mirror import needs_sync, sync_one_repo
from .utils import handle_credits, print_storage_estimate
from .providers.gitlab import GitLabProvider
from .providers.github import GitHubProvider

def run_sync_cycle(args, source_provider, destination_provider, synced_pushes):
    """Executes one full synchronization cycle."""
    repos = source_provider.fetch_repos(args.verbose)
    log(f"Found {len(repos)} repositories on GitHub.", is_verbose_mode=args.verbose)
    
    print_storage_estimate(repos, args)

    sync_count = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        future_to_repo = {}
        for repo in repos:
            repo_name = repo.name
            pushed_at = repo.pushed_at
            repo_dir = os.path.join(args.storage, f"{repo_name}.git")

            # Smart filtering
            if args.watch:
                # 1. Skip if already synced this exact push
                if repo_name in synced_pushes and synced_pushes[repo_name] == pushed_at:
                    continue
                
                # 2. Check time window (SKIP if old AND local repo exists)
                if os.path.exists(repo_dir) and not needs_sync(repo, args.window):
                        continue
                
            future = executor.submit(sync_one_repo, repo, args, source_provider, destination_provider)
            future_to_repo[future] = repo

        for future in as_completed(future_to_repo):
            repo = future_to_repo[future]
            try:
                future.result()
                sync_count += 1
                if repo.pushed_at:
                    synced_pushes[repo.name] = repo.pushed_at
            except Exception as exc:
                log(f"[{repo.name}] generated an exception: {exc}")
    
    return sync_count

def main():
    args = parse_args()
    handle_credits(args)
    
    gh_token, gl_token = validate_config(args)
    
    # Initialize Providers
    source_provider = GitHubProvider(gh_token, GITHUB_API_URL)
    
    destination_provider = None
    if not args.backup_only:
        destination_provider = GitLabProvider(GITLAB_API_URL, gl_token)

    log("Initializing Holocron...")
    if args.dry_run:
        log("!!! DRY RUN MODE ACTIVE !!!")

    synced_pushes = {}

    while True:
        sync_count = run_sync_cycle(args, source_provider, destination_provider, synced_pushes)

        if sync_count > 0:
            log(f"Sync cycle complete. Updated {sync_count} repositories.")
        elif args.verbose:
            log("No changes detected in this cycle.")

        if not args.watch:
            break
            
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
