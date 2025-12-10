#!/usr/bin/env python3
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import from local modules
from config import parse_args
from logger import log
from github_provider import get_github_repos
from mirror import needs_sync, sync_one_repo

def main():
    args = parse_args()
    
    # Load secrets
    gh_token = os.environ.get("GITHUB_TOKEN")
    gl_token = os.environ.get("GITLAB_TOKEN")
    
    if not gh_token or not gl_token:
        print("CRITICAL: Missing Environment Variables.")
        print("Please set GITHUB_TOKEN and GITLAB_TOKEN.")
        sys.exit(1)

    log("Starting G2G-Sync...")
    if args.dry_run:
        log("!!! DRY RUN MODE ACTIVE !!!")

    while True:
        repos = get_github_repos(gh_token, args.verbose)
        log(f"Found {len(repos)} repositories on GitHub.", is_verbose_mode=args.verbose)

        sync_count = 0
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            future_to_repo = {}
            for repo in repos:
                # If watching, use the smart filter. If running once, sync all (or customize logic).
                if args.watch and not needs_sync(repo, args.window):
                    continue
                    
                future = executor.submit(sync_one_repo, repo, args, gh_token, gl_token)
                future_to_repo[future] = repo

            for future in as_completed(future_to_repo):
                repo = future_to_repo[future]
                try:
                    future.result()
                    sync_count += 1
                except Exception as exc:
                    log(f"[{repo['name']}] generated an exception: {exc}")

        if sync_count > 0:
            log(f"Sync cycle complete. Updated {sync_count} repositories.")
        elif args.verbose:
            log("No changes detected in this cycle.")

        if not args.watch:
            break
            
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
