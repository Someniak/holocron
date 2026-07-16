#!/usr/bin/env python3
import os
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


# Import from local modules
from .config import parse_args, validate_config, GITLAB_API_URL, GITHUB_API_URL
from .logger import setup_logger, logger, log_execution
from .mirror import needs_sync, sync_one_repo
from .utils import handle_credits, print_storage_estimate
from .providers.gitlab import GitLabProvider
from .providers.github import GitHubProvider
from .webhook import start_webhook_server

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

    try:
        repos = source_provider.fetch_repos()
    except Exception as exc:
        # A failed/incomplete fetch must not produce a partial mirror. Skip this
        # cycle entirely; watch mode will retry next interval, one-shot mode exits.
        logger.error(f"Skipping sync cycle: failed to fetch repositories: {exc}")
        return 0

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


def start_webhook_listener(config, source_provider, destination_provider, synced_pushes):
    """
    Starts the webhook HTTP listener and returns the server.

    Push events are synced through the same sync_one_repo engine as the poll
    loop, on a dedicated thread pool so a burst of deliveries doesn't block the
    HTTP handler. Requires HOLOCRON_WEBHOOK_SECRET to be set.
    """
    secret = os.environ.get("HOLOCRON_WEBHOOK_SECRET")
    if not secret:
        logger.error("CRITICAL: --webhook requires HOLOCRON_WEBHOOK_SECRET to be set. Not starting listener.")
        sys.exit(1)

    # Persistent pool for webhook-triggered syncs (the poll loop uses its own
    # per-cycle pool). Per-repo locking in sync_one_repo keeps the two in step.
    executor = ThreadPoolExecutor(max_workers=config['concurrency'], thread_name_prefix="webhook-sync")

    def on_push(repo):
        future = executor.submit(
            sync_one_repo,
            repo=repo,
            storage_path=config['storage'],
            dry_run=config['dry_run'],
            backup_only=config['backup_only'],
            checkout=config['checkout'],
            source_provider=source_provider,
            destination_provider=destination_provider,
        )

        def _done(fut):
            try:
                fut.result()
                if repo.pushed_at:
                    synced_pushes[repo.name] = repo.pushed_at
                logger.info(f"[{repo.name}] Webhook-triggered sync complete.")
            except Exception as exc:
                logger.error(f"[{repo.name}] Webhook-triggered sync failed: {exc}")

        future.add_done_callback(_done)

    try:
        return start_webhook_server(
            port=config['webhook_port'],
            secret=secret,
            on_push=on_push,
            path=config['webhook_path'],
            cert_file=config.get('webhook_cert'),
            key_file=config.get('webhook_key'),
        )
    except (ValueError, FileNotFoundError, ssl.SSLError) as exc:
        logger.error(f"CRITICAL: cannot start webhook TLS listener: {exc}")
        sys.exit(1)


def get_provider(name, token, api_url_github, api_url_gitlab, namespace=None,
                 provision_status_var=False):
    """Factory to get the correct provider instance."""
    if name == "github":
        return GitHubProvider(token, api_url_github)
    elif name == "gitlab":
        return GitLabProvider(api_url_gitlab, token, namespace,
                              provision_status_var=provision_status_var)
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
    
    # Only provision the GITHUB_REPO CI variable when mirroring GitHub -> GitLab:
    # it needs the GitHub `full_name` (populated by the GitHub source) and a
    # GitLab destination to write the variable to.
    # getattr (not args.github_status) tolerates the hand-built argparse.Namespace
    # objects the tests pass in, matching the existing getattr(args, "webhook", ...) usage.
    wants_status = getattr(args, "github_status", False)
    provision_status_var = (
        wants_status
        and args.source == "github"
        and args.destination == "gitlab"
    )
    if wants_status and not provision_status_var:
        logger.warning(
            "--github-status is set but is a no-op unless source=github and "
            "destination=gitlab; skipping GITHUB_REPO provisioning."
        )

    destination_provider = None
    if not args.backup_only:
        destination_provider = get_provider(
            args.destination,
            get_token_for(args.destination),
            GITHUB_API_URL,
            GITLAB_API_URL,
            namespace=args.gitlab_namespace,
            provision_status_var=provision_status_var,
        )

    logger.info("Initializing Holocron...")
    if args.dry_run:
        logger.info("!!! DRY RUN MODE ACTIVE !!!")

    synced_pushes = {}

    # Convert args to a dict (or Config object) for easier passing to cycle runner
    # We could also pass args directly but we want to decouple run_sync_cycle from argparse
    config = vars(args)

    # Start the webhook listener (if enabled) before the poll loop so push events
    # are handled even while an initial full cycle is running.
    if getattr(args, "webhook", False):
        start_webhook_listener(config, source_provider, destination_provider, synced_pushes)

    while True:
        sync_count = run_sync_cycle(config, source_provider, destination_provider, synced_pushes)

        if sync_count > 0:
            logger.info(f"Sync cycle complete. Updated {sync_count} repositories.")
        else:
            logger.debug("No changes detected in this cycle.")

        if not args.watch:
            break

        time.sleep(args.interval)

    # Webhook-only mode (no --watch): keep the process alive to serve deliveries
    # after the initial one-shot sync cycle.
    if getattr(args, "webhook", False) and not args.watch:
        logger.info("Initial sync done. Holding open for webhook deliveries (Ctrl-C to exit).")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Shutting down webhook listener.")

if __name__ == "__main__":
    main()
