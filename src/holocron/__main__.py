#!/usr/bin/env python3
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


# Import from local modules
from .config import parse_args, validate_config, __author__, __license__, GITLAB_API_URL, GITHUB_API_URL
from .logger import setup_logger, logger, log_execution
from .mirror import needs_sync, sync_one_repo
from .utils import handle_credits, print_storage_estimate
from .providers.gitlab import GitLabProvider
from .providers.github import GitHubProvider

@log_execution
def run_sync_cycle(config: dict, source_provider, destination_provider, synced_pushes):
    """Executes one full synchronization cycle."""
    # Unpack config
    concurrency = config['concurrency']
    storage = config['storage']
    watch = config['watch']
    window = config['window']
    backup_only = config['backup_only']
    dry_run = config['dry_run']
    checkout = config['checkout']

    repos = source_provider.fetch_repos()
    logger.debug(f"Found {len(repos)} repositories on GitHub.")
    
    print_storage_estimate(repos, checkout_mode=checkout)

    sync_count = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_repo = {}
        for repo in repos:
            repo_name = repo.name
            pushed_at = repo.pushed_at
            repo_dir = os.path.join(storage, f"{repo_name}.git")

            # Smart filtering
            if watch:
                # 1. Skip if already synced this exact push
                if repo_name in synced_pushes and synced_pushes[repo_name] == pushed_at:
                    continue
                
                # 2. Check time window (SKIP if old AND local repo exists)
                if os.path.exists(repo_dir) and not needs_sync(repo, window):
                        continue
            
            # Pass explicit params to sync_one_repo
            future = executor.submit(
                sync_one_repo, 
                repo=repo, 
                storage_path=storage, 
                dry_run=dry_run, 
                backup_only=backup_only,
                checkout=checkout,
                source_provider=source_provider, 
                destination_provider=destination_provider
            )
            future_to_repo[future] = repo

        for future in as_completed(future_to_repo):
            repo = future_to_repo[future]
            try:
                future.result()
                sync_count += 1
                if repo.pushed_at:
                    synced_pushes[repo.name] = repo.pushed_at
            except Exception as exc:
                logger.error(f"[{repo.name}] generated an exception: {exc}")
    
    return sync_count


def get_provider(name, token, api_url_github, api_url_gitlab, namespace=None):
    """Factory to get the correct provider instance."""
    if name == "github":
        return GitHubProvider(token, api_url_github)
    elif name == "gitlab":
        return GitLabProvider(api_url_gitlab, token, namespace)
    else:
        raise ValueError(f"Unknown provider: {name}")

def main():
    args = parse_args()
    handle_credits(args.credits)
    
    # Initialize Logger Global Configuration
    setup_logger(args.verbose)

    # Handle 'local' destination alias
    if args.destination == "local":
        args.backup_only = True
    
    gh_token, gl_token = validate_config(args.source, args.destination, args.backup_only)
    
    # helper for tokens
    def get_token_for(p_name):
        return gh_token if p_name == "github" else gl_token
        
    # Initialize Providers
    logger.debug(f"Source: {args.source}, Destination: {args.destination}")

    source_provider = get_provider(
        args.source, 
        get_token_for(args.source), 
        GITHUB_API_URL, 
        GITLAB_API_URL,
        namespace=args.gitlab_namespace
    )
    
    destination_provider = None
    if not args.backup_only:
        destination_provider = get_provider(
            args.destination,
            get_token_for(args.destination),
            GITHUB_API_URL,
            GITLAB_API_URL,
            namespace=args.gitlab_namespace
        )

    logger.info("Initializing Holocron...")
    if args.dry_run:
        logger.info("!!! DRY RUN MODE ACTIVE !!!")

    synced_pushes = {}
    
    # Convert args to a dict (or Config object) for easier passing to cycle runner
    # We could also pass args directly but we want to decouple run_sync_cycle from argparse
    config = vars(args)

    while True:
        sync_count = run_sync_cycle(config, source_provider, destination_provider, synced_pushes)

        if sync_count > 0:
            logger.info(f"Sync cycle complete. Updated {sync_count} repositories.")
        else:
            logger.debug("No changes detected in this cycle.")

        if not args.watch:
            break
            
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
