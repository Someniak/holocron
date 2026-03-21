from datetime import datetime
from typing import Optional

import requests

from ..config import GITHUB_API_URL
from ..logger import log_execution, logger
from ..retry import handle_rate_limit, retry_on_failure
from .base import Provider, Repository


class GitHubProvider(Provider):
    def __init__(self, token: str, api_url: str = GITHUB_API_URL):
        self.token = token
        self.api_url = api_url

    def get_remote_url(self, repo: Repository) -> str:
        """Constructs the authenticated clone URL."""
        return repo.clone_url.replace("https://", f"https://oauth2:{self.token}@")

    @log_execution
    def fetch_repos(self) -> list[Repository]:
        """Fetches all repositories from the user AND their organizations."""
        headers = {"Authorization": f"token {self.token}"}
        all_repos: list[Repository] = []
        seen_ids: set[int] = set()

        # 1. Fetch User Repos
        user_repos = self._get_all_pages(
            f"{self.api_url}/user/repos",
            headers,
            "user repositories (visibility=all, all affiliations)",
            query_params={
                "visibility": "all",
                "affiliation": "owner,collaborator,organization_member",
            },
        )

        for item in user_repos:
            if item["id"] not in seen_ids:
                all_repos.append(self._to_repository(item))
                seen_ids.add(item["id"])

        # 2. Fetch User Organizations
        orgs = self._get_all_pages(
            f"{self.api_url}/user/orgs",
            headers,
            "organizations",
        )

        # 3. Fetch Repos for each Org
        for org in orgs:
            org_name = org["login"]
            org_repos = self._get_all_pages(
                f"{self.api_url}/orgs/{org_name}/repos",
                headers,
                f"repositories for organization '{org_name}'",
                query_params={"type": "all"},
            )
            for item in org_repos:
                if item["id"] not in seen_ids:
                    all_repos.append(self._to_repository(item))
                    seen_ids.add(item["id"])

        return all_repos

    def _to_repository(self, item: dict) -> Repository:
        """Helper to convert GitHub API dict to Repository object."""
        pushed_at = None
        if item.get("pushed_at"):
            import contextlib

            with contextlib.suppress(ValueError):
                pushed_at = datetime.strptime(item["pushed_at"], "%Y-%m-%dT%H:%M:%SZ")

        return Repository(
            name=item["name"],
            clone_url=item["clone_url"],
            size=item.get("size", 0),
            pushed_at=pushed_at,
        )

    @retry_on_failure(max_retries=3)
    def _get_all_pages(
        self, base_url: str, headers: dict, context_name: str, query_params: Optional[dict] = None
    ) -> list[dict]:
        """Helper to fetch all pages from a GitHub endpoint."""
        if query_params is None:
            query_params = {}

        items: list[dict] = []
        page = 1
        query_params["per_page"] = 100

        logger.debug(f"Fetching {context_name}...")

        while True:
            try:
                query_params["page"] = page

                logger.debug(f"Requesting page {page} from {base_url}")

                r = requests.get(base_url, headers=headers, params=query_params, timeout=20)
                handle_rate_limit(r)

                if r.status_code == 429:
                    # Re-request after rate limit wait
                    r = requests.get(base_url, headers=headers, params=query_params, timeout=20)

                r.raise_for_status()

                data = r.json()
                if not data:
                    logger.debug(f"Page {page} empty. stopping.")
                    break

                count = len(data)
                logger.debug(f"Page {page} returned {count} items.")

                items.extend(data)

                if count < query_params["per_page"]:
                    break

                page += 1
            except requests.HTTPError as e:
                logger.error(f"HTTP error fetching {context_name}: {e}")
                break
            except requests.ConnectionError as e:
                logger.error(f"Connection error fetching {context_name}: {e}")
                raise  # Let retry decorator handle this
            except requests.Timeout as e:
                logger.error(f"Timeout fetching {context_name}: {e}")
                raise  # Let retry decorator handle this
            except requests.RequestException as e:
                logger.error(f"ERROR fetching {context_name}: {e}")
                break
        return items

    def prepare_push(self, repo: Repository) -> None:
        """
        Ensures the default branch is configured to allow force pushes.
        """
        if not self.token:
            return

        try:
            headers = {
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github.v3+json",
            }

            logger.debug(f"[{repo.name}] Checking branch protection...")

            r = requests.get(f"{self.api_url}/repos/{repo.name}", headers=headers, timeout=10)
            if r.status_code == 404:
                return
            r.raise_for_status()

            default_branch = r.json().get("default_branch", "main")

            # Check Protection
            prot_url = f"{self.api_url}/repos/{repo.name}/branches/{default_branch}/protection"
            r_prot = requests.get(prot_url, headers=headers, timeout=10)

            if r_prot.status_code == 404:
                return

            if r_prot.status_code == 200:
                prot_data = r_prot.json()
                allow_force = prot_data.get("allow_force_pushes", {}).get("enabled", False)

                if not allow_force:
                    logger.info(f"[{repo.name}] Branch '{default_branch}' is protected. Enabling force push...")

                    update_payload = self._build_protection_payload(prot_data)

                    logger.info(f"[{repo.name}] Updating branch protection to allow force push.")

                    r_put = requests.put(prot_url, headers=headers, json=update_payload, timeout=10)
                    r_put.raise_for_status()
                    logger.info(f"[{repo.name}] Successfully enabled force push.")

        except requests.HTTPError as e:
            logger.warning(f"[{repo.name}] HTTP error updating GitHub branch protection: {e}")
        except requests.ConnectionError as e:
            logger.warning(f"[{repo.name}] Connection error updating GitHub branch protection: {e}")
        except requests.RequestException as e:
            logger.warning(f"[{repo.name}] Failed to update GitHub branch protection: {e}")

    def _build_protection_payload(self, prot_data: dict) -> dict:
        """Build the PUT payload from a GET protection response, preserving existing settings."""
        update_payload = {
            "required_status_checks": prot_data.get("required_status_checks"),
            "enforce_admins": prot_data.get("enforce_admins", {}).get("enabled", False),
            "required_pull_request_reviews": prot_data.get("required_pull_request_reviews"),
            "restrictions": prot_data.get("restrictions"),
            "allow_force_pushes": True,
            "allow_deletions": prot_data.get("allow_deletions", {}).get("enabled", False),
        }

        # Clean up required_status_checks (GET response != PUT input)
        rsc = prot_data.get("required_status_checks")
        if rsc:
            update_payload["required_status_checks"] = {
                "strict": rsc.get("strict", False),
                "contexts": rsc.get("contexts", []),
                "checks": rsc.get("checks", []),
            }

        # Clean up required_pull_request_reviews
        rprr = prot_data.get("required_pull_request_reviews")
        if rprr:
            update_payload["required_pull_request_reviews"] = {
                "dismissal_restrictions": rprr.get("dismissal_restrictions"),
                "dismiss_stale_reviews": rprr.get("dismiss_stale_reviews", False),
                "require_code_owner_reviews": rprr.get("require_code_owner_reviews", False),
                "required_approving_review_count": rprr.get("required_approving_review_count", 1),
            }

            dr = rprr.get("dismissal_restrictions")
            if dr:
                users = [u["login"] for u in dr.get("users", [])]
                teams = [t["slug"] for t in dr.get("teams", [])]
                items = {}
                if users:
                    items["users"] = users
                if teams:
                    items["teams"] = teams
                update_payload["required_pull_request_reviews"]["dismissal_restrictions"] = items

        # Clean up restrictions
        res = prot_data.get("restrictions")
        if res:
            update_payload["restrictions"] = {
                "users": [u["login"] for u in res.get("users", [])],
                "teams": [t["slug"] for t in res.get("teams", [])],
                "apps": [a["slug"] for a in res.get("apps", [])],
            }

        return update_payload
