import contextlib
from datetime import datetime
from typing import Optional

import requests

from ..logger import log_execution, logger
from ..retry import handle_rate_limit, retry_on_failure
from .base import Provider, Repository


class GitLabProvider(Provider):
    def __init__(self, api_url: str, token: str, namespace: Optional[str] = None):
        self.api_url = api_url
        self.token = token
        self.namespace = namespace

    @log_execution
    def fetch_repos(self) -> list[Repository]:
        """
        Fetches repositories from GitLab (User + Groups).
        """
        headers = {"Private-Token": self.token}
        all_repos: list[Repository] = []
        seen_ids: set[int] = set()

        user_repos = self._get_all_pages(
            f"{self.api_url}/projects",
            headers,
            "GitLab projects (membership=true)",
            query_params={
                "membership": "true",
                "simple": "true",
            },
        )

        for item in user_repos:
            if item["id"] not in seen_ids:
                all_repos.append(self._to_repository(item))
                seen_ids.add(item["id"])

        return all_repos

    def prepare_push(self, repo: Repository) -> None:
        """
        Ensures the default branch is configured to allow force pushes (required for mirroring).
        """
        if not self.token:
            return

        project_path = repo.name
        if self.namespace:
            project_path = f"{self.namespace}/{repo.name}"

        encoded_path = project_path.replace("/", "%2F")
        base_url = self.api_url.rstrip("/")
        if base_url.endswith("/api/v4"):
            base_url = base_url[:-7]
        base_url = base_url.rstrip("/")

        api_base = f"{base_url}/api/v4"
        headers = {"Private-Token": self.token}

        try:
            logger.debug(f"[{repo.name}] Checking branch protection for '{project_path}'...")

            r = requests.get(f"{api_base}/projects/{encoded_path}", headers=headers, timeout=10)
            if r.status_code == 404:
                return
            r.raise_for_status()
            project_data = r.json()
            project_id = project_data["id"]
            default_branch = project_data.get("default_branch", "main")

            r_prot = requests.get(
                f"{api_base}/projects/{project_id}/protected_branches/{default_branch}",
                headers=headers,
                timeout=10,
            )

            needs_update = False
            if r_prot.status_code == 200:
                prot_data = r_prot.json()
                if not prot_data.get("allow_force_push", False):
                    needs_update = True
                    logger.info(f"[{repo.name}] Branch '{default_branch}' is protected. Enabling force push...")
            elif r_prot.status_code == 404:
                pass

            if needs_update:
                payload = {"allow_force_push": True}
                r_patch = requests.patch(
                    f"{api_base}/projects/{project_id}/protected_branches/{default_branch}",
                    headers=headers,
                    json=payload,
                    timeout=10,
                )

                if r_patch.status_code in (405, 404):
                    logger.warning(f"[{repo.name}] PATCH failed: {r_patch.status_code}. Output: {r_patch.text}")
                else:
                    r_patch.raise_for_status()
                    logger.info(f"[{repo.name}] Successfully enabled force push for '{default_branch}'.")

        except requests.HTTPError as e:
            logger.warning(f"[{repo.name}] HTTP error updating branch protection: {e}")
        except requests.ConnectionError as e:
            logger.warning(f"[{repo.name}] Connection error updating branch protection: {e}")
        except requests.RequestException as e:
            logger.warning(f"[{repo.name}] Failed to update branch protection (ignoring): {e}")

    def get_remote_url(self, repo: Repository) -> str:
        """
        Constructs the authenticated URL for pushing to GitLab.
        """
        base_url = self.api_url.rstrip("/")
        if base_url.endswith("/api/v4"):
            base_url = base_url[:-7]
        base_url = base_url.rstrip("/")

        url = f"{base_url}/{repo.name}.git"

        if self.namespace:
            url = f"{base_url}/{self.namespace}/{repo.name}.git"

        if self.token:
            if url.startswith("https://"):
                return url.replace("https://", f"https://oauth2:{self.token}@", 1)
            elif url.startswith("http://"):
                return url.replace("http://", f"http://oauth2:{self.token}@", 1)

        return url

    def _to_repository(self, item: dict) -> Repository:
        """Helper to convert GitLab API dict to Repository object."""
        pushed_at = None
        if item.get("last_activity_at"):
            try:
                pushed_at = datetime.strptime(item["last_activity_at"].split(".")[0], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                with contextlib.suppress(ValueError):
                    pushed_at = datetime.strptime(item["last_activity_at"], "%Y-%m-%dT%H:%M:%SZ")

        return Repository(
            name=item["path"],
            clone_url=item["http_url_to_repo"],
            size=0,
            pushed_at=pushed_at,
        )

    @retry_on_failure(max_retries=3)
    def _get_all_pages(
        self, base_url: str, headers: dict, context_name: str, query_params: Optional[dict] = None
    ) -> list[dict]:
        """Helper to fetch all pages from a GitLab endpoint."""
        if query_params is None:
            query_params = {}

        items: list[dict] = []
        page = 1
        query_params["per_page"] = 100

        logger.debug(f"Fetching {context_name}...")

        while True:
            try:
                query_params["page"] = page

                logger.debug(f"Requesting page {page} from {base_url}...")

                r = requests.get(base_url, headers=headers, params=query_params, timeout=20)
                if handle_rate_limit(r):
                    continue  # Retry after rate limit wait
                r.raise_for_status()

                data = r.json()
                if not data:
                    break

                count = len(data)
                items.extend(data)

                if count < query_params["per_page"]:
                    break

                page += 1
            except requests.HTTPError as e:
                logger.error(f"HTTP error fetching {context_name}: {e}")
                break
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
