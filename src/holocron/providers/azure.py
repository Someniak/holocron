import base64
import re
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


class AzureDevOpsProvider(Provider):
    """
    Azure DevOps Services (cloud) provider.

    Source-only for now: Holocron can mirror *from* Azure DevOps, but does not
    create or push to Azure DevOps repositories.

    `org_url` is the full organisation URL -- `https://dev.azure.com/<org>` for
    the current cloud form, `https://<org>.visualstudio.com` for the legacy one.
    Authentication is a Personal Access Token sent as HTTP Basic (empty user,
    PAT as password), which is what Azure DevOps expects for both the REST API
    and `git` over HTTPS.
    """

    def __init__(self, org_url, token, project=None, api_version=API_VERSION,
                 activity_concurrency=8):
        self.org_url = (org_url or "").rstrip("/")
        self.token = token
        # Optional single-project scope. Without it every repository in the
        # organisation is mirrored.
        self.project = project
        self.api_version = api_version
        # Azure DevOps has no bulk "last activity" endpoint, so the last push
        # date costs one request per repository; they are issued in parallel.
        self.activity_concurrency = activity_concurrency

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

    # --- Source interface --------------------------------------------------

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

        repos = [
            self._to_repository(item, name)
            for item, name in zip(usable, self._resolve_names(usable))
        ]
        self._attach_last_activity(repos, usable)
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
