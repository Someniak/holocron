import requests
from datetime import datetime
from ..logger import logger, log_execution
from .base import Provider, Repository

class GitLabProvider(Provider):
    def __init__(self, api_url, token, namespace=None):
        self.api_url = api_url
        self.token = token
        self.namespace = namespace

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
        Ensures the default branch is configured to allow force pushes (required for mirroring).
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

    # --- CI bridge helpers -------------------------------------------------
    # These drive the PR->CI->status flow: resolve the project, open/close an MR
    # for the mirrored PR branch (which fires the merge_request_event pipeline),
    # and read pipeline status so the caller can report it back to GitHub.

    def _api_base(self):
        """Normalizes self.api_url to a '.../api/v4' base regardless of input."""
        base_url = self.api_url.rstrip('/')
        if base_url.endswith('/api/v4'):
            base_url = base_url[:-7]
        base_url = base_url.rstrip('/')
        return f"{base_url}/api/v4"

    def _project_path(self, repo):
        """The GitLab project path ('namespace/name' or just 'name')."""
        if self.namespace:
            return f"{self.namespace}/{repo.name}"
        return repo.name

    def get_project_id(self, repo):
        """
        Returns the numeric GitLab project id for `repo`, or None if the project
        does not exist yet (404). Other HTTP errors propagate so the caller can
        surface them.
        """
        api_base = self._api_base()
        headers = {'Private-Token': self.token}
        encoded_path = self._project_path(repo).replace("/", "%2F")
        r = requests.get(f"{api_base}/projects/{encoded_path}", headers=headers, timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get('id')

    def find_open_merge_request(self, project_id, source_branch):
        """Returns the open MR whose source branch is `source_branch`, or None."""
        api_base = self._api_base()
        headers = {'Private-Token': self.token}
        r = requests.get(
            f"{api_base}/projects/{project_id}/merge_requests",
            headers=headers,
            params={'source_branch': source_branch, 'state': 'opened'},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None

    def create_or_update_merge_request(self, project_id, source_branch,
                                       target_branch, title, description=None):
        """
        Ensures an open MR exists from `source_branch` to `target_branch`.

        Idempotent: if one is already open for that source branch it is reused
        (dedup by source branch), so a PR `synchronize` — which only force-pushes
        the branch and lets GitLab spawn a fresh MR pipeline — never creates a
        duplicate MR. Returns the MR dict (has `iid` and `web_url`).
        """
        existing = self.find_open_merge_request(project_id, source_branch)
        if existing:
            return existing
        api_base = self._api_base()
        headers = {'Private-Token': self.token}
        payload = {
            'source_branch': source_branch,
            'target_branch': target_branch,
            'title': title,
            'remove_source_branch': False,
        }
        if description:
            payload['description'] = description
        r = requests.post(f"{api_base}/projects/{project_id}/merge_requests",
                          headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        return r.json()

    def close_merge_request(self, project_id, source_branch):
        """Closes the open MR for `source_branch` (no-op if none is open)."""
        existing = self.find_open_merge_request(project_id, source_branch)
        if not existing:
            return None
        api_base = self._api_base()
        headers = {'Private-Token': self.token}
        r = requests.put(
            f"{api_base}/projects/{project_id}/merge_requests/{existing['iid']}",
            headers=headers, json={'state_event': 'close'}, timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def get_latest_mr_pipeline(self, project_id, mr_iid):
        """
        Returns the newest pipeline attached to MR `mr_iid`, or None.

        Preferred over get_pipeline_for_sha because a merged-results pipeline runs
        on a synthetic merge commit whose SHA differs from the PR head SHA.
        """
        api_base = self._api_base()
        headers = {'Private-Token': self.token}
        r = requests.get(
            f"{api_base}/projects/{project_id}/merge_requests/{mr_iid}/pipelines",
            headers=headers, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None

    def get_pipeline_for_sha(self, project_id, sha):
        """Fallback pipeline lookup by commit SHA (newest first), or None."""
        api_base = self._api_base()
        headers = {'Private-Token': self.token}
        r = requests.get(
            f"{api_base}/projects/{project_id}/pipelines",
            headers=headers,
            params={'sha': sha, 'order_by': 'id', 'sort': 'desc'},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None

    def get_pipeline_status(self, project_id, pipeline_id):
        """Returns the pipeline dict (fields incl. `status` and `web_url`)."""
        api_base = self._api_base()
        headers = {'Private-Token': self.token}
        r = requests.get(
            f"{api_base}/projects/{project_id}/pipelines/{pipeline_id}",
            headers=headers, timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def delete_branch(self, project_id, branch):
        """Best-effort delete of a branch (204 success / 404 already-gone both OK)."""
        api_base = self._api_base()
        headers = {'Private-Token': self.token}
        encoded = branch.replace("/", "%2F")
        r = requests.delete(
            f"{api_base}/projects/{project_id}/repository/branches/{encoded}",
            headers=headers, timeout=10,
        )
        if r.status_code not in (204, 404):
            r.raise_for_status()

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
