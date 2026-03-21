import contextlib
from datetime import datetime
from typing import Optional

import requests

from ..logger import log_execution, logger
from ..retry import handle_rate_limit, retry_on_failure
from .base import Provider, Repository


class BitbucketProvider(Provider):
    def __init__(self, username: str, app_password: str, api_url: str = "https://api.bitbucket.org/2.0"):
        self.username = username
        self.app_password = app_password
        self.api_url = api_url.rstrip("/")
        self.auth = (username, app_password)

    def get_remote_url(self, repo: Repository) -> str:
        """Constructs the authenticated clone URL for Bitbucket."""
        return repo.clone_url.replace(
            "https://",
            f"https://{self.username}:{self.app_password}@",
        )

    @log_execution
    def fetch_repos(self) -> list[Repository]:
        """Fetches all repositories the user has access to from Bitbucket Cloud."""
        all_repos: list[Repository] = []
        seen_uuids: set[str] = set()

        # Fetch user's own repositories
        user_repos = self._get_all_pages(
            f"{self.api_url}/repositories/{self.username}",
            "user repositories",
        )
        for item in user_repos:
            uuid = item.get("uuid", "")
            if uuid not in seen_uuids:
                all_repos.append(self._to_repository(item))
                seen_uuids.add(uuid)

        # Fetch repositories from all workspaces the user is a member of
        workspaces = self._get_all_pages(
            f"{self.api_url}/workspaces",
            "workspaces",
        )
        for ws in workspaces:
            ws_slug = ws.get("slug", "")
            if ws_slug == self.username:
                continue  # Already fetched
            ws_repos = self._get_all_pages(
                f"{self.api_url}/repositories/{ws_slug}",
                f"repositories for workspace '{ws_slug}'",
            )
            for item in ws_repos:
                uuid = item.get("uuid", "")
                if uuid not in seen_uuids:
                    all_repos.append(self._to_repository(item))
                    seen_uuids.add(uuid)

        return all_repos

    def _to_repository(self, item: dict) -> Repository:
        """Converts a Bitbucket API response item to a Repository object."""
        pushed_at = None
        updated_on = item.get("updated_on")
        if updated_on:
            with contextlib.suppress(ValueError):
                pushed_at = datetime.strptime(updated_on.split(".")[0], "%Y-%m-%dT%H:%M:%S")

        # Find the HTTPS clone URL
        clone_url = ""
        for link in item.get("links", {}).get("clone", []):
            if link.get("name") == "https":
                clone_url = link["href"]
                break

        return Repository(
            name=item.get("slug", item.get("name", "")),
            clone_url=clone_url,
            size=item.get("size", 0) // 1024 if item.get("size") else 0,
            pushed_at=pushed_at,
        )

    @retry_on_failure(max_retries=3)
    def _get_all_pages(self, url: str, context_name: str) -> list[dict]:
        """Fetches all pages from a Bitbucket endpoint using cursor-based pagination."""
        items: list[dict] = []
        next_url: Optional[str] = url

        logger.debug(f"Fetching {context_name}...")

        while next_url:
            try:
                params = {"pagelen": 100} if next_url == url else {}
                r = requests.get(
                    next_url,
                    auth=self.auth,
                    params=params,
                    timeout=20,
                )
                handle_rate_limit(r)

                if r.status_code == 429:
                    # Re-request after waiting
                    r = requests.get(next_url, auth=self.auth, params=params, timeout=20)

                r.raise_for_status()
                data = r.json()

                values = data.get("values", [])
                items.extend(values)

                logger.debug(f"Fetched {len(values)} items from {context_name}.")

                next_url = data.get("next")
            except requests.ConnectionError as e:
                logger.error(f"Connection error fetching {context_name}: {e}")
                raise
            except requests.Timeout as e:
                logger.error(f"Timeout fetching {context_name}: {e}")
                raise
            except requests.RequestException as e:
                logger.error(f"ERROR fetching {context_name}: {e}")
                break

        return items

    def prepare_push(self, repo: Repository) -> None:
        """Bitbucket Cloud does not have branch protection that blocks mirror pushes in the same way."""
        pass
