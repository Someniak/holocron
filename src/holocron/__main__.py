#!/usr/bin/env python3
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from fnmatch import fnmatch
from typing import Optional

# Import from local modules
from .config import GITHUB_API_URL, GITLAB_API_URL, parse_args, validate_config
from .logger import log_execution, logger, setup_logger
from .mirror import needs_sync, sync_one_repo
from .providers.base import Provider, Repository
from .providers.bitbucket import BitbucketProvider
from .providers.github import GitHubProvider
from .providers.gitlab import GitLabProvider
from .utils import handle_credits, print_storage_estimate

# Regex to match tokens in URLs for masking
_TOKEN_PATTERN = re.compile(r"(oauth2:|x-token-auth:)[^@]+@")


def _mask_token(text: str) -> str:
    """Mask any tokens that might appear in error messages."""
    return _TOKEN_PATTERN.sub(r"\1***@", text)


def _filter_repos(
    repos: list[Repository],
    include: Optional[list[str]],
    exclude: Optional[list[str]],
) -> list[Repository]:
    """Filter repositories by include/exclude glob patterns."""
    filtered = repos

    if include:
        filtered = [r for r in filtered if any(fnmatch(r.name, pat) for pat in include)]
        excluded_count = len(repos) - len(filtered)
        if excluded_count > 0:
            logger.debug(f"Include filter: {excluded_count} repos excluded, {len(filtered)} matched.")

    if exclude:
        before = len(filtered)
        filtered = [r for r in filtered if not any(fnmatch(r.name, pat) for pat in exclude)]
        excluded_count = before - len(filtered)
        if excluded_count > 0:
            logger.debug(f"Exclude filter: {excluded_count} repos excluded.")

    return filtered


def _is_safe_repo_name(name: str) -> bool:
    """Validate that a repository name is safe for filesystem operations."""
    return ".." not in name and not name.startswith("/") and "\\" not in name and "\x00" not in name


def _cleanup_orphaned_mirrors(storage: str, repo_names: set[str]) -> int:
    """Remove local mirrors that no longer exist on the source."""
    removed = 0
    if not os.path.exists(storage):
        return removed

    for entry in os.listdir(storage):
        entry_path = os.path.join(storage, entry)
        if not os.path.isdir(entry_path):
            continue

        # Match both bare (.git suffix) and checkout dirs
        repo_name = entry
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]

        if repo_name not in repo_names:
            logger.info(f"[Cleanup] Removing orphaned mirror: {entry}")
            shutil.rmtree(entry_path)
            removed += 1

    return removed


@log_execution
def run_sync_cycle(
    config: dict,
    source_provider: Provider,
    destination_provider: Optional[Provider],
    synced_pushes: dict,
) -> dict:
    """Executes one full synchronization cycle. Returns stats dict."""
    # Unpack config
    concurrency = config["concurrency"]
    storage = config["storage"]
    watch = config["watch"]
    window = config["window"]
    backup_only = config["backup_only"]
    dry_run = config["dry_run"]
    checkout = config["checkout"]
    include_patterns = config.get("include")
    exclude_patterns = config.get("exclude")
    cleanup = config.get("cleanup", False)

    stats = {"synced": 0, "skipped": 0, "failed": 0, "filtered": 0, "cleaned": 0}

    repos = source_provider.fetch_repos()
    logger.debug(f"Found {len(repos)} repositories on source.")

    # Apply include/exclude filters
    original_count = len(repos)
    repos = _filter_repos(repos, include_patterns, exclude_patterns)
    stats["filtered"] = original_count - len(repos)

    print_storage_estimate(repos, checkout_mode=checkout)

    # Cleanup orphaned mirrors
    if cleanup and not dry_run:
        repo_names = {r.name for r in repos}
        stats["cleaned"] = _cleanup_orphaned_mirrors(storage, repo_names)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_repo = {}
        for repo in repos:
            repo_name = repo.name

            # Validate repo name for safety
            if not _is_safe_repo_name(repo_name):
                logger.warning(f"[{repo_name}] Skipping: unsafe repository name.")
                stats["skipped"] += 1
                continue

            pushed_at = repo.pushed_at
            repo_dir = os.path.join(storage, f"{repo_name}.git")

            # Smart filtering
            if watch:
                # 1. Skip if already synced this exact push
                if repo_name in synced_pushes and synced_pushes[repo_name] == pushed_at:
                    stats["skipped"] += 1
                    continue

                # 2. Check time window (SKIP if old AND local repo exists)
                if os.path.exists(repo_dir) and not needs_sync(repo, window):
                    stats["skipped"] += 1
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
                destination_provider=destination_provider,
            )
            future_to_repo[future] = repo

        for future in as_completed(future_to_repo):
            repo = future_to_repo[future]
            try:
                future.result()
                stats["synced"] += 1
                if repo.pushed_at:
                    synced_pushes[repo.name] = repo.pushed_at
            except Exception as exc:
                stats["failed"] += 1
                logger.error(f"[{repo.name}] generated an exception: {_mask_token(str(exc))}")

    return stats


def get_provider(
    name: str,
    token: Optional[str],
    api_url_github: str,
    api_url_gitlab: str,
    namespace: Optional[str] = None,
    bb_username: Optional[str] = None,
    bb_app_password: Optional[str] = None,
) -> Provider:
    """Factory to get the correct provider instance."""
    if name == "github":
        if not token:
            raise ValueError("GitHub token is required")
        return GitHubProvider(token, api_url_github)
    elif name == "gitlab":
        if not token:
            raise ValueError("GitLab token is required")
        return GitLabProvider(api_url_gitlab, token, namespace)
    elif name == "bitbucket":
        if not bb_username or not bb_app_password:
            raise ValueError("Bitbucket credentials are required")
        return BitbucketProvider(bb_username, bb_app_password)
    else:
        raise ValueError(f"Unknown provider: {name}")


def main() -> None:
    args = parse_args()
    handle_credits(args.credits)

    # Initialize Logger Global Configuration
    setup_logger(args.verbose)

    # Handle 'local' destination alias
    if args.destination == "local":
        args.backup_only = True

    gh_token: Optional[str]
    gl_token: Optional[str]
    bb_username: Optional[str]
    bb_app_password: Optional[str]
    gh_token, gl_token, bb_username, bb_app_password = validate_config(args.source, args.destination, args.backup_only)

    # Initialize Providers
    logger.debug(f"Source: {args.source}, Destination: {args.destination}")

    def _token_for(provider_name: str) -> Optional[str]:
        if provider_name == "github":
            return gh_token
        elif provider_name == "gitlab":
            return gl_token
        return None  # Bitbucket uses bb_username/bb_app_password instead

    source_provider = get_provider(
        args.source,
        _token_for(args.source),
        GITHUB_API_URL,
        GITLAB_API_URL,
        namespace=args.gitlab_namespace,
        bb_username=bb_username,
        bb_app_password=bb_app_password,
    )

    destination_provider = None
    if not args.backup_only:
        destination_provider = get_provider(
            args.destination,
            _token_for(args.destination),
            GITHUB_API_URL,
            GITLAB_API_URL,
            namespace=args.gitlab_namespace,
            bb_username=bb_username,
            bb_app_password=bb_app_password,
        )

    logger.info("Initializing Holocron...")
    if args.dry_run:
        logger.info("!!! DRY RUN MODE ACTIVE !!!")

    synced_pushes: dict = {}

    # Convert args to a dict for easier passing to cycle runner
    config = vars(args)

    while True:
        start_time = time.time()
        stats = run_sync_cycle(config, source_provider, destination_provider, synced_pushes)
        elapsed = time.time() - start_time

        # Print sync summary
        synced = stats["synced"]
        failed = stats["failed"]
        skipped = stats["skipped"]
        filtered = stats["filtered"]
        cleaned = stats["cleaned"]

        parts = []
        if synced > 0:
            parts.append(f"{synced} synced")
        if skipped > 0:
            parts.append(f"{skipped} skipped")
        if failed > 0:
            parts.append(f"{failed} failed")
        if filtered > 0:
            parts.append(f"{filtered} filtered out")
        if cleaned > 0:
            parts.append(f"{cleaned} orphans removed")

        if parts:
            summary = ", ".join(parts)
            logger.info(f"Sync cycle complete ({elapsed:.1f}s): {summary}.")
        else:
            logger.debug("No changes detected in this cycle.")

        if not args.watch:
            break

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
