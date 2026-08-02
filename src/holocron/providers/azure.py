import base64
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from ..logger import logger, log_execution
from .base import Provider, Repository

# Azure DevOps Services REST API version. 7.1 is the current GA version and is
# also served by recent Azure DevOps Server installs.
API_VERSION = "7.1"

# Everything outside the slug charset Holocron accepts (see utils.is_safe_repo_name).
# Azure DevOps allows spaces and other punctuation in repository names, so names
# have to be slugified before they can become a directory / destination path.
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

# Azure DevOps returns .NET "round-trip" timestamps with 7 fractional digits
# ("2014-06-30T17:58:34.1765687Z"); datetime.fromisoformat accepts at most 6.
_OVERLONG_FRACTION = re.compile(r"^(.*\.\d{6})\d+(.*)$")


def _slugify(name):
    """
    Reduces an Azure DevOps repository/project name to a safe path component.

    Returns "" when nothing usable is left, so callers can fall back to another
    identifier (e.g. the repository GUID).
    """
    if not isinstance(name, str):
        return ""
    slug = _UNSAFE_NAME_CHARS.sub("-", name).strip("-.")
    return "" if slug in (".", "..") else slug


def _parse_azure_date(value):
    """Parses an Azure DevOps timestamp into a naive UTC datetime (or None)."""
    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    match = _OVERLONG_FRACTION.match(text)
    if match:
        text = match.group(1) + match.group(2)

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _utcnow():
    """Naive UTC now, matching the convention mirror.needs_sync compares against."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _AzureDevOpsApi:
    """
    Shared Azure DevOps REST plumbing: auth, JSON handling and pagination.

    `org_url` is the full organisation URL -- `https://dev.azure.com/<org>` for
    the current cloud form, `https://<org>.visualstudio.com` for the legacy one.
    Authentication is a Personal Access Token sent as HTTP Basic (empty user,
    PAT as password), which is what Azure DevOps expects for both the REST API
    and `git` over HTTPS.
    """

    def __init__(self, org_url, token, project=None, api_version=API_VERSION):
        self.org_url = (org_url or "").rstrip("/")
        self.token = token
        # Optional single-project scope. For a source this narrows what is
        # mirrored; for a destination it is where repositories are created.
        self.project = project
        self.api_version = api_version

    # --- HTTP plumbing -----------------------------------------------------

    def _headers(self):
        # Azure DevOps PAT auth == Basic auth with an empty username.
        credentials = base64.b64encode(f":{self.token or ''}".encode()).decode()
        return {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
        }

    def _api_base(self):
        """Organisation-wide `_apis` root, narrowed to a project when configured."""
        if self.project:
            return f"{self.org_url}/{quote(self.project, safe='')}/_apis"
        return f"{self.org_url}/_apis"

    def _get_json(self, url, params, context_name):
        """
        GETs a JSON document from the Azure DevOps API.

        An unauthenticated request to dev.azure.com does not fail with 401: it
        answers 203 with an HTML sign-in page. Checking the content type turns
        that into an actionable error instead of a confusing JSON decode failure.
        """
        query = dict(params or {})
        query.setdefault("api-version", self.api_version)

        r = requests.get(url, headers=self._headers(), params=query, timeout=20)
        r.raise_for_status()

        content_type = (r.headers.get("Content-Type") or "").lower()
        if "json" not in content_type:
            raise RuntimeError(
                f"{context_name}: Azure DevOps returned '{content_type or 'an unknown content type'}' "
                f"instead of JSON (HTTP {r.status_code}). The PAT is most likely missing, "
                f"expired, or lacks the 'Code (Read)' scope."
            )
        return r

    def _get_all_items(self, url, context_name, params=None):
        """
        Fetches every item from a list endpoint, following continuation tokens.

        Most Git list endpoints return the full set in one response, but Azure
        DevOps pages large results via the `x-ms-continuationtoken` header.
        """
        items = []
        continuation = None
        page = 1

        logger.debug(f"Fetching {context_name}...")

        while True:
            try:
                query = dict(params or {})
                if continuation:
                    query["continuationToken"] = continuation

                logger.debug(f"Requesting page {page} from {url}")
                r = self._get_json(url, query, context_name)
                data = r.json()
            except Exception as e:
                # Do NOT swallow-and-break: returning pages 1..N-1 here would look
                # like a complete result and silently drop repos from the mirror.
                # Fail loud so the caller knows the fetch was incomplete.
                logger.error(f"ERROR fetching {context_name}: {e}")
                raise RuntimeError(
                    f"Incomplete fetch of {context_name}: failed at page {page}: {e}"
                ) from e

            batch = data.get("value", []) if isinstance(data, dict) else []
            items.extend(batch)
            logger.debug(f"Page {page} returned {len(batch)} items.")

            next_token = r.headers.get("x-ms-continuationtoken")
            # Guard against a server echoing the same token forever.
            if not next_token or next_token == continuation:
                break
            continuation = next_token
            page += 1

        return items


class AzureDevOpsProvider(_AzureDevOpsApi, Provider):
    """
    Azure DevOps Services (cloud) as a mirror *source*.

    Mirrors *from* Azure DevOps: it lists repositories and builds authenticated
    clone URLs, but never creates or pushes anything. See
    AzureDevOpsDestinationProvider for the other direction.
    """

    def __init__(self, org_url, token, project=None, api_version=API_VERSION,
                 activity_concurrency=8, repo_filter=None):
        super().__init__(org_url, token, project=project, api_version=api_version)
        # Optional RepoFilter. Applied here as well as centrally, because the
        # last-push lookup costs one request per repository -- in a 1000-repo
        # organisation, filtering first is the difference between 1001 requests
        # and a handful.
        self.repo_filter = repo_filter
        # Azure DevOps has no bulk "last activity" endpoint, so the last push
        # date costs one request per repository; they are issued in parallel.
        self.activity_concurrency = activity_concurrency

    @log_execution
    def fetch_repos(self) -> list[Repository]:
        """Fetches every Git repository in the organisation (or configured project)."""
        scope = f"Azure DevOps repositories in '{self.org_url}'"
        if self.project:
            scope += f" (project '{self.project}')"

        items = self._get_all_items(f"{self._api_base()}/git/repositories", scope)

        usable = []
        for item in items:
            name = item.get("name")
            clone_url = item.get("remoteUrl") or item.get("webUrl")

            if not name or not clone_url:
                logger.warning(
                    f"Skipping Azure DevOps repository with no name or remote URL (id={item.get('id')!r})."
                )
                continue
            if item.get("isDisabled"):
                logger.debug(f"Skipping disabled Azure DevOps repository '{name}'.")
                continue
            if not item.get("defaultBranch"):
                # An uninitialised repo has no refs at all; there is nothing to
                # mirror and `git clone --mirror` would just create an empty one.
                logger.debug(f"Skipping empty Azure DevOps repository '{name}' (no default branch).")
                continue

            usable.append(item)

        # Names are resolved over the *whole* list before filtering, so a
        # repository's mirror name never changes just because the filter did
        # (which would orphan its existing mirror directory).
        paired = [
            (self._to_repository(item, name), item)
            for item, name in zip(usable, self._resolve_names(usable))
        ]

        if self.repo_filter:
            paired = [pair for pair in paired
                      if self.repo_filter.matches(pair[0])]
            logger.debug(
                f"{len(paired)} of {len(usable)} Azure DevOps repositories selected; "
                f"skipping last-push lookups for the rest."
            )

        repos = [repo for repo, _ in paired]
        self._attach_last_activity(repos, [item for _, item in paired])
        return repos

    def _resolve_names(self, items):
        """
        Maps Azure DevOps repository names onto safe, unique mirror names.

        Two things make this necessary: names may contain characters that are
        not valid in a path component (spaces in particular), and they are only
        unique *within a project* -- two projects in the same organisation can
        both hold a "docs" repo. Colliding names are qualified with the project
        name; anything still ambiguous gets a numeric suffix.
        """
        slugs = [_slugify(item.get("name")) for item in items]

        # Only qualify with the project name when the colliding repositories
        # actually live in different projects; a collision inside one project
        # (e.g. "my repo" and "my-repo") is settled by the numeric-suffix pass.
        slug_projects = defaultdict(set)
        for item, slug in zip(items, slugs):
            if slug:
                slug_projects[slug].add((item.get("project") or {}).get("name") or "")

        resolved = []
        for item, slug in zip(items, slugs):
            project = (item.get("project") or {}).get("name") or ""

            if slug and len(slug_projects[slug]) > 1 and project:
                qualified = _slugify(f"{project}-{item.get('name')}")
                if qualified:
                    logger.warning(
                        f"Azure DevOps repository '{item.get('name')}' exists in more than one "
                        f"project; mirroring the '{project}' one as '{qualified}'."
                    )
                    slug = qualified
            if not slug:
                # Nothing usable in the name (e.g. it is entirely non-ASCII);
                # fall back to the repository GUID so the repo is still mirrored.
                slug = _slugify(item.get("id")) or "repository"
                logger.warning(
                    f"Azure DevOps repository name {item.get('name')!r} has no usable "
                    f"characters for a path; mirroring it as '{slug}'."
                )
            resolved.append(slug)

        # Final uniqueness pass: distinct source names can still slugify to the
        # same string ("my repo" and "my-repo"), and two projects can share both
        # the repo name and a slugified project name.
        seen = set()
        unique = []
        for item, slug in zip(items, resolved):
            candidate = slug
            suffix = 2
            while candidate in seen:
                candidate = f"{slug}-{suffix}"
                suffix += 1
            if candidate != slug:
                logger.warning(
                    f"Mirror name '{slug}' is already taken; mirroring Azure DevOps "
                    f"repository '{item.get('name')}' as '{candidate}'."
                )
            seen.add(candidate)
            unique.append(candidate)
        return unique

    def _to_repository(self, item: dict, name: str) -> Repository:
        """Helper to convert an Azure DevOps API dict to a Repository object."""
        project = (item.get("project") or {}).get("name") or ""
        source_name = item.get("name")

        # Azure DevOps reports the compressed size in bytes; Repository.size is KB.
        size_bytes = item.get("size") or 0
        try:
            size_kb = int(size_bytes) // 1024
        except (TypeError, ValueError):
            size_kb = 0

        return Repository(
            name=name,
            clone_url=item.get("remoteUrl") or item.get("webUrl"),
            size=size_kb,
            pushed_at=None,  # filled in by _attach_last_activity
            full_name=f"{project}/{source_name}" if project else source_name,
        )

    def _attach_last_activity(self, repos, items):
        """
        Populates `pushed_at` for each repository (one API call per repository).

        The repository list carries no timestamp, but `--window` filtering in
        watch mode depends on one, so the most recent push is fetched separately.
        """
        if not repos:
            return

        workers = max(1, min(self.activity_concurrency, len(repos)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="azure-activity") as executor:
            for repo, pushed_at in zip(repos, executor.map(self._fetch_last_push, items)):
                repo.pushed_at = pushed_at

    def _fetch_last_push(self, item):
        """
        Returns the date of the most recent push, as a naive UTC datetime.

        None means the repository provably has no pushes yet. When the lookup
        *fails* we return "now" instead: an unknown timestamp would otherwise
        fall outside every `--window` and the repo would be silently skipped for
        good in watch mode. Erring towards syncing costs a fetch, not a mirror.
        """
        name = item.get("name")
        repo_id = item.get("id")
        if not repo_id:
            return _utcnow()

        url = f"{self.org_url}/_apis/git/repositories/{quote(str(repo_id), safe='')}/pushes"
        try:
            # Pushes come back newest-first, so one item is enough.
            data = self._get_json(url, {"$top": 1}, f"last push for '{name}'").json()
        except Exception as e:
            logger.warning(
                f"[{name}] Could not determine the last push date: {e}. "
                f"Treating it as recently pushed so it is not skipped."
            )
            return _utcnow()

        pushes = data.get("value") or []
        if not pushes:
            return None

        parsed = _parse_azure_date(pushes[0].get("date"))
        if parsed is None:
            logger.warning(
                f"[{name}] Unrecognised push date {pushes[0].get('date')!r}. "
                f"Treating it as recently pushed so it is not skipped."
            )
            return _utcnow()
        return parsed

    def _allowed_clone_hosts(self):
        """Hosts the Azure DevOps PAT may be sent to (the configured org host)."""
        host = (urlsplit(self.org_url).hostname or "").lower()
        return {host} if host else set()

    def get_remote_url(self, repo: Repository) -> str:
        """
        Constructs the authenticated clone URL.

        The PAT is pinned to the configured organisation: a clone URL on another
        host, or outside the organisation's path, raises ValueError rather than
        leaking the token. Any userinfo Azure DevOps already put in the URL
        (`https://org@dev.azure.com/...`) is stripped first -- appending
        credentials to it would produce an unusable URL.
        """
        parts = urlsplit(repo.clone_url or "")
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"unsupported clone URL for {repo.name!r}")

        if parts.hostname.lower() not in self._allowed_clone_hosts():
            raise ValueError(
                f"clone host {parts.hostname!r} does not match the configured "
                f"Azure DevOps host; refusing to attach credentials"
            )

        org_path = urlsplit(self.org_url).path.rstrip("/")
        if org_path and not (parts.path == org_path or parts.path.startswith(f"{org_path}/")):
            raise ValueError(
                f"clone URL for {repo.name!r} is outside the configured organisation "
                f"path {org_path!r}; refusing to attach credentials"
            )

        host = parts.hostname
        if parts.port:
            host = f"{host}:{parts.port}"

        netloc = host
        if self.token:
            # Azure DevOps ignores the username and takes the PAT as password.
            netloc = f"oauth2:{quote(self.token, safe='')}@{host}"

        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class AzureDevOpsDestinationProvider(_AzureDevOpsApi, Provider):
    """
    Azure DevOps Services (cloud) as a mirror *destination*.

    Two things make this more than a URL builder:

    * Azure DevOps does not create a repository on first push the way GitLab
      does -- pushing to a path that does not exist fails. `prepare_push`
      therefore creates missing repositories through the REST API, which is why
      a `project` is required: repositories live inside one, and Holocron does
      not create projects.
    * The push URL is built from the *configured* organisation and project, not
      from `repo.clone_url` -- that URL belongs to the source.

    The PAT needs the `Code (Read, write, & manage)` scope; `Code (Read & write)`
    is enough only when every destination repository already exists.
    """

    def __init__(self, org_url, token, project=None, api_version=API_VERSION):
        if not (org_url or "").strip():
            raise ValueError("an Azure DevOps destination needs an organisation URL")
        if not (project or "").strip():
            raise ValueError(
                "an Azure DevOps destination needs a project (--azure-project / "
                "AZURE_DEVOPS_PROJECT): repositories are created inside a project"
            )
        super().__init__(org_url, token, project=project, api_version=api_version)

        # prepare_push runs for every repository on every cycle. Remember which
        # ones are known to exist so a steady-state watch loop costs no API
        # calls at all. Guarded: syncs run on a thread pool.
        self._existing = set()
        self._existing_guard = threading.Lock()
        self._project_id = None

    @log_execution
    def fetch_repos(self) -> list[Repository]:
        """
        Lists the repositories that already exist in the destination project.

        The sync engine only ever fetches from the source, so this is here to
        satisfy the Provider interface and to make the destination inspectable.
        Names are the raw Azure DevOps ones, not mirror names.
        """
        scope = f"Azure DevOps repositories in project '{self.project}'"
        repos = []
        for item in self._get_all_items(f"{self._api_base()}/git/repositories", scope):
            name = item.get("name")
            if not name:
                continue
            try:
                size_kb = int(item.get("size") or 0) // 1024
            except (TypeError, ValueError):
                size_kb = 0
            repos.append(Repository(
                name=name,
                clone_url=item.get("remoteUrl") or item.get("webUrl"),
                size=size_kb,
                full_name=f"{self.project}/{name}",
            ))
        return repos

    def get_remote_url(self, repo: Repository) -> str:
        """
        Constructs the authenticated push URL: `{org}/{project}/_git/{name}`.

        `repo.name` is the mirror name, already restricted to the safe slug
        charset by the sync engine before it gets here.
        """
        name = (repo.name or "").strip()
        if not name:
            raise ValueError("cannot push a repository with no name")

        parts = urlsplit(self.org_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(
                f"unsupported Azure DevOps organisation URL {self.org_url!r}"
            )

        host = parts.hostname
        if parts.port:
            host = f"{host}:{parts.port}"

        netloc = host
        if self.token:
            # Azure DevOps ignores the username and takes the PAT as password.
            netloc = f"oauth2:{quote(self.token, safe='')}@{host}"

        path = (
            f"{parts.path.rstrip('/')}"
            f"/{quote(self.project, safe='')}/_git/{quote(name, safe='')}"
        )
        return urlunsplit((parts.scheme, netloc, path, "", ""))

    def push_refspecs(self):
        """
        Pushes branches and tags only, pruning within those namespaces.

        `git push --mirror` is wrong for Azure DevOps in both directions: it
        would try to *write* refs the server reserves (a GitHub mirror carries
        `refs/pull/*`, which Azure DevOps rejects) and to *delete* the
        server-managed refs Azure keeps for its own pull requests, since they
        have no local counterpart. Restricting the push to `refs/heads/*` and
        `refs/tags/*` mirrors everything that is actually the source's, and
        leaves Azure's own refs alone.
        """
        return ["+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"]

    def prepare_push(self, repo: Repository):
        """Creates the destination repository if it does not exist yet."""
        name = repo.name
        with self._existing_guard:
            if name in self._existing:
                return

        self._ensure_repository(name)

        with self._existing_guard:
            self._existing.add(name)

    # --- repository provisioning -------------------------------------------

    def _ensure_repository(self, name):
        """
        Checks whether `name` exists in the project, creating it if it does not.

        Unlike the GitLab destination's best-effort branch-protection tweak, a
        failure here is fatal for this repository: without the repository the
        push cannot succeed, and an opaque `git push` error is a much worse way
        to find that out. The exception is reported per repository and the next
        cycle retries.
        """
        url = f"{self._api_base()}/git/repositories/{quote(name, safe='')}"
        try:
            self._get_json(url, None, f"Azure DevOps repository '{name}'")
            logger.debug(f"[{name}] Destination repository already exists.")
            return
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status != 404:
                raise RuntimeError(
                    f"[{name}] Could not look up the destination repository in project "
                    f"'{self.project}': HTTP {status}{_scope_hint(status)}"
                ) from e
        except requests.exceptions.RequestException as e:
            raise RuntimeError(
                f"[{name}] Could not reach Azure DevOps to look up the destination "
                f"repository: {e}"
            ) from e

        logger.info(f"[{name}] Creating Azure DevOps repository in project '{self.project}'...")
        self._create_repository(name)

    def _create_repository(self, name):
        """POSTs a new (empty) Git repository into the configured project."""
        payload = {"name": name, "project": {"id": self._resolve_project_id()}}
        headers = dict(self._headers())
        headers["Content-Type"] = "application/json"

        try:
            r = requests.post(
                f"{self._api_base()}/git/repositories",
                headers=headers,
                params={"api-version": self.api_version},
                json=payload,
                timeout=20,
            )
            if r.status_code == 409:
                # Another sync thread won the race, or the name differs only by
                # case. Either way the repository is there now.
                logger.debug(f"[{name}] Destination repository already existed.")
                return
            r.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            raise RuntimeError(
                f"[{name}] Could not create the Azure DevOps repository in project "
                f"'{self.project}': HTTP {status}{_scope_hint(status, manage=True)}"
            ) from e
        except requests.exceptions.RequestException as e:
            raise RuntimeError(
                f"[{name}] Could not reach Azure DevOps to create the destination "
                f"repository: {e}"
            ) from e

        logger.info(f"[{name}] Created Azure DevOps repository.")

    def _resolve_project_id(self):
        """Looks up (and caches) the destination project's GUID."""
        if self._project_id:
            return self._project_id

        url = f"{self.org_url}/_apis/projects/{quote(self.project, safe='')}"
        try:
            data = self._get_json(url, None, f"Azure DevOps project '{self.project}'").json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404:
                raise RuntimeError(
                    f"Azure DevOps project '{self.project}' does not exist in "
                    f"{self.org_url} (Holocron creates repositories, not projects)."
                ) from e
            raise RuntimeError(
                f"Could not look up Azure DevOps project '{self.project}': "
                f"HTTP {status}{_scope_hint(status)}"
            ) from e
        except requests.exceptions.RequestException as e:
            raise RuntimeError(
                f"Could not reach Azure DevOps to look up project '{self.project}': {e}"
            ) from e

        project_id = data.get("id")
        if not project_id:
            raise RuntimeError(
                f"Azure DevOps returned no id for project '{self.project}'."
            )

        self._project_id = project_id
        return project_id


def _scope_hint(status, manage=False):
    """Appends the usual cause for an auth failure: a PAT without the scope."""
    if status not in (401, 403):
        return ""
    scope = "Code (Read, write, & manage)" if manage else "Code (Read & write)"
    return f" (the PAT most likely lacks the '{scope}' scope)"
