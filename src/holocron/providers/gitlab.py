import requests
from datetime import datetime
from ..logger import logger, log_execution
from .base import Provider, Repository

class GitLabProvider(Provider):
    def __init__(self, api_url, token, namespace=None, provision_status_var=False):
        self.api_url = api_url
        self.token = token
        self.namespace = namespace
        # When True, prepare_push upserts a per-project GITHUB_REPO CI/CD
        # variable (the source's owner/repo) so GitLab runners can report CI
        # checks back to the matching GitHub commit.
        self.provision_status_var = provision_status_var

    @log_execution
    def fetch_repos(self) -> list[Repository]:
        """
        Fetches repositories from GitLab (User + Groups).
        """
        headers = {'Private-Token': self.token}
        all_repos = []
        seen_ids = set()

        # 1. Fetch User Repos (and member projects)
        user_repos = self._get_all_pages(
            f"{self.api_url}/projects",
            headers,
            "GitLab projects (membership=true)",
             query_params={
                "membership": "true",
                "simple": "true" 
             }
        )
        
        for item in user_repos:
             if item['id'] not in seen_ids:
                all_repos.append(self._to_repository(item))
                seen_ids.add(item['id'])
        
        # Note: GitLab's /projects?membership=true usually covers everything a user has access to, 
        # including group projects. If strict group separation is needed, we'd query /groups.
        
        return all_repos

    def prepare_push(self, repo: Repository):
        """
        Prepares the destination project for the mirror push.

        Ensures the default branch allows force pushes (required for mirroring),
        and, when provision_status_var is enabled, upserts the GITHUB_REPO CI/CD
        variable (reusing the project lookup done here).
        """
        if not self.token:
             return

        # 1. Get Project ID and Default Branch
        # We can't trust repo.name alone if we have a custom namespace, so we query by path
        # But wait, self.get_remote_url constructs the URL based on self.namespace + repo.name via path.
        # Let's verify how we can get the project info. 
        # The 'repo' object comes from the source usually.
        # We need to find the project on GitLab that matches the destination path.
        
        project_path = repo.name
        if self.namespace:
            project_path = f"{self.namespace}/{repo.name}"
            
        # URL encode path
        encoded_path = project_path.replace("/", "%2F")
        base_url = self.api_url.rstrip('/')
        if base_url.endswith('/api/v4'):
            base_url = base_url[:-7]
        base_url = base_url.rstrip('/')
        
        api_base = f"{base_url}/api/v4"
        headers = {'Private-Token': self.token}
        
        try:
            logger.debug(f"[{repo.name}] Checking branch protection for '{project_path}'...")
            
            # Fetch Project
            r = requests.get(f"{api_base}/projects/{encoded_path}", headers=headers, timeout=10)
            if r.status_code == 404:
                return # Project likely doesn't exist yet, so no protection to worry about
            r.raise_for_status()
            project_data = r.json()
            project_id = project_data['id']
            default_branch = project_data.get('default_branch', 'main')

            # Provision the GITHUB_REPO CI/CD variable so runners can report CI
            # checks back to GitHub (see _upsert_ci_variable). Best-effort: any
            # failure is logged and does not block the mirror push.
            if self.provision_status_var and repo.full_name:
                self._provision_github_repo_var(api_base, headers, project_id, repo.full_name)
            
            # 2. Check Protection Rules
            # GET /projects/:id/protected_branches/:name
            r_prot = requests.get(f"{api_base}/projects/{project_id}/protected_branches/{default_branch}", headers=headers, timeout=10)
            
            needs_update = False
            if r_prot.status_code == 200:
                # Branch is protected
                prot_data = r_prot.json()
                if not prot_data.get('allow_force_push', False):
                    needs_update = True
                    logger.info(f"[{repo.name}] Branch '{default_branch}' is protected. Enabling force push...")
            elif r_prot.status_code == 404:
                 # Not protected, so we are good (assuming default is not protected, or if it is, it might be implicitly handled by strict defaults but usually explicit rule exists)
                 pass
            
            # 3. Update Protection if needed
            if needs_update:
                # PATCH /projects/:id/protected_branches/:name
                # Note: GitLab API sometimes requires unprotect + protect, or PATCH depending on version.
                # PATCH is supported in newer GitLab. Let's try PATCH with allow_force_push=True
                
                # Check if PATCH is supported or if we need to blindly recreate.
                # simpler to just update.
                payload = {'allow_force_push': True}
                r_patch = requests.patch(f"{api_base}/projects/{project_id}/protected_branches/{default_branch}", headers=headers, json=payload, timeout=10)
                
                if r_patch.status_code == 405 or r_patch.status_code == 404:
                    # Fallback: Unprotect and Protect (Old way or if PATCH fails)
                    # Actually, if 404, it means it's not protected? But we just checked 200. 
                    # Let's assume standard PATCH works. If not, we might need a more complex fallback.
                    logger.warning(f"[{repo.name}] PATCH failed: {r_patch.status_code}. Output: {r_patch.text}")
                else:
                    r_patch.raise_for_status()
                    logger.info(f"[{repo.name}] Successfully enabled force push for '{default_branch}'.")

        except requests.exceptions.HTTPError as e:
            # Non-fatal: if we cannot relax protection the subsequent push will
            # fail and be reported separately. Surface the status so the common
            # cause (insufficient token scope) is diagnosable instead of hidden.
            status = e.response.status_code if e.response is not None else "?"
            hint = " (token likely missing Maintainer/Owner rights or 'api' scope)" if status in (401, 403) else ""
            logger.warning(f"[{repo.name}] Failed to update branch protection: HTTP {status}{hint}: {e}")
        except Exception as e:
            logger.warning(f"[{repo.name}] Failed to update branch protection (ignoring): {e}")

    def _provision_github_repo_var(self, api_base, headers, project_id, value):
        """
        Creates or updates the project-level GITHUB_REPO CI/CD variable (best-effort).

        Tries PUT (update) first; a 404 means the variable doesn't exist yet, so
        we POST (create). The variable is non-secret and unprotected so it is
        available to unprotected feature-branch pipelines. Any error is logged
        and swallowed so provisioning never blocks the mirror push.
        """
        key = "GITHUB_REPO"
        var_url = f"{api_base}/projects/{project_id}/variables/{key}"
        try:
            r_put = requests.put(var_url, headers=headers, json={"value": value}, timeout=10)
            if r_put.status_code == 404:
                r_post = requests.post(
                    f"{api_base}/projects/{project_id}/variables",
                    headers=headers,
                    json={"key": key, "value": value, "masked": False, "protected": False},
                    timeout=10,
                )
                r_post.raise_for_status()
                logger.info(f"[{value}] Created GitLab CI/CD variable {key}={value}.")
            else:
                r_put.raise_for_status()
                logger.debug(f"[{value}] Updated GitLab CI/CD variable {key}={value}.")
        except Exception as e:
            logger.warning(f"Failed to provision GitLab CI/CD variable {key} for project {project_id}: {e}")

    def get_remote_url(self, repo: Repository) -> str:
        """
        Constructs the authenticated URL for pushing to GitLab.
        """
        # Strip '/api/v4' from the user-provided API URL to get the base URL,
        # then inject the OAuth2 token.
        base_url = self.api_url.rstrip('/')
        if base_url.endswith('/api/v4'):
            base_url = base_url[:-7]
        base_url = base_url.rstrip('/')

        url = f"{base_url}/{repo.name}.git"

        if self.namespace:
            # Inject namespace (group/user) between base_url and repo_name
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
        if item.get('last_activity_at'):
            try:
                # 2024-01-01T00:00:00.000Z
                pushed_at = datetime.strptime(item['last_activity_at'].split('.')[0], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                # Try without microseconds if it fails
                try:
                    pushed_at = datetime.strptime(item['last_activity_at'], "%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    pass
                
        return Repository(
            name=item['path'], # Use path (slug) as name
            clone_url=item['http_url_to_repo'],
            size=0, # Simple objects might not have stats, default to 0
            pushed_at=pushed_at
        )

    def _get_all_pages(self, base_url, headers, context_name, query_params=None):
        """Helper to fetch all pages from a GitLab endpoint."""
        if query_params is None:
            query_params = {}

        items = []
        page = 1
        query_params['per_page'] = 100
        
        logger.debug(f"Fetching {context_name}...")
        
        while True:
            try:
                query_params['page'] = page
                
                logger.debug(f"Requesting page {page} from {base_url}...")

                r = requests.get(base_url, headers=headers, params=query_params, timeout=20)
                r.raise_for_status()
                
                data = r.json()
                if not data:
                    break
                
                count = len(data)
                items.extend(data)
                
                # Check for pagination headers usually, but length check is robust enough for simple cases
                if count < query_params['per_page']:
                     break

                page += 1
            except Exception as e:
                # Do NOT swallow-and-break: returning pages 1..N-1 here would look
                # like a complete result and silently drop repos from the mirror.
                # Fail loud so the caller knows the fetch was incomplete.
                logger.error(f"ERROR fetching {context_name}: {e}")
                raise RuntimeError(
                    f"Incomplete fetch of {context_name}: failed at page {page}: {e}"
                ) from e
        return items
