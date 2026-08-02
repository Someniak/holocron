"""
End-to-end check for the Azure DevOps *destination*: a stub Azure DevOps API in
front of a real git smart-HTTP server, driven by the real sync engine.

Everything is local (loopback socket, temp dirs) -- no credentials, no network
-- but nothing is mocked below the providers. `git clone --mirror` really runs
against the source URL, `prepare_push` really creates the destination
repository through the REST endpoint, and `git push --mirror` really pushes into
it over HTTP. That is what proves the push URL, the repository provisioning and
their ordering actually work together, rather than each being right in isolation.
"""
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from holocron.mirror import sync_one_repo
from holocron.providers.azure import AzureDevOpsDestinationProvider, AzureDevOpsProvider

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

ORG = "acme"
SOURCE_PROJECT = "SrcProj"
DEST_PROJECT = "DestProj"
REPO_ID = "abc-123"
SOURCE_NAME = "Widgets Repo"   # the space must be slugified away...
MIRROR_NAME = "Widgets-Repo"   # ...into this, which is what Azure DevOps gets asked for
SOURCE_GIT_PATH = f"/{ORG}/{SOURCE_PROJECT}/_git/widgets"


def _git(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


@pytest.fixture
def git_root(tmp_path):
    """
    A GIT_PROJECT_ROOT holding the source repository at its served path.

    Repositories are laid out exactly as their URL path, so `git http-backend`
    maps PATH_INFO onto them directly -- the destination repositories the API
    stub creates land next to this one.
    """
    root = tmp_path / "git-root"
    work = tmp_path / "work"

    work.mkdir()
    _git("git", "init", "-q", "-b", "main", str(work))
    (work / "README.md").write_text("hello from azure devops\n")
    _git("git", "-C", str(work), "add", ".")
    _git("git", "-C", str(work), "-c", "user.email=t@e.st", "-c", "user.name=T",
         "commit", "-qm", "initial")
    _git("git", "-C", str(work), "tag", "v1.0.0")
    _git("git", "-C", str(work), "branch", "feature")

    source = root / SOURCE_GIT_PATH.lstrip("/")
    source.parent.mkdir(parents=True)
    _git("git", "clone", "-q", "--bare", str(work), str(source))
    # A GitHub-sourced mirror carries the PR head refs; Azure DevOps reserves
    # that namespace, so they must not be pushed.
    _git("git", "-C", str(source), "update-ref", "refs/pull/1/head", "refs/heads/main")
    return root


@pytest.fixture
def azure_stub(git_root):
    """Serves the Azure DevOps REST endpoints plus git smart HTTP on a loopback port."""
    created = []

    def dest_repo_path(name):
        return git_root / ORG / DEST_PROJECT / "_git" / name

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _json(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _empty(self, status):
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = urlparse(self.path).path

            # --- source side ---
            if path == f"/{ORG}/_apis/git/repositories":
                return self._json({"count": 1, "value": [{
                    "id": REPO_ID,
                    "name": SOURCE_NAME,
                    "project": {"name": SOURCE_PROJECT},
                    "defaultBranch": "refs/heads/main",
                    "size": 40960,
                    "remoteUrl": f"http://{self.server.host_port}{SOURCE_GIT_PATH}",
                }]})

            if path == f"/{ORG}/_apis/git/repositories/{REPO_ID}/pushes":
                return self._json({"count": 1, "value": [
                    {"date": "2099-01-01T00:00:00.1234567Z"},
                ]})

            # --- destination side ---
            if path == f"/{ORG}/_apis/projects/{DEST_PROJECT}":
                return self._json({"id": "project-guid", "name": DEST_PROJECT})

            prefix = f"/{ORG}/{DEST_PROJECT}/_apis/git/repositories/"
            if path.startswith(prefix):
                name = path[len(prefix):]
                # Only ever compared against names this stub created, so the
                # request path never reaches the filesystem.
                if name in created:
                    return self._json({"id": f"repo-{name}", "name": name})
                return self._empty(404)

            return self._git_http()

        def do_POST(self):
            path = urlparse(self.path).path

            if path == f"/{ORG}/{DEST_PROJECT}/_apis/git/repositories":
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                name = payload["name"]
                assert payload["project"]["id"] == "project-guid"

                if name in created:
                    return self._empty(409)

                target = dest_repo_path(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                _git("git", "init", "-q", "--bare", str(target))
                # http-backend refuses anonymous pushes unless told otherwise.
                _git("git", "-C", str(target), "config", "http.receivepack", "true")
                created.append(name)
                return self._json({"id": f"repo-{name}", "name": name}, status=201)

            return self._git_http()

        def _git_http(self):
            """Hands the request to `git http-backend` (the CGI smart-HTTP server)."""
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""

            env = {
                "PATH": os.environ.get("PATH", ""),
                "GIT_PROJECT_ROOT": str(git_root),
                "GIT_HTTP_EXPORT_ALL": "1",
                "REQUEST_METHOD": self.command,
                "PATH_INFO": parsed.path,
                "QUERY_STRING": parsed.query,
                "REMOTE_ADDR": self.client_address[0],
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(length),
                "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
            }
            result = subprocess.run(["git", "http-backend"], input=body,
                                    capture_output=True, env=env)
            if result.returncode != 0:
                self.send_error(500, "git http-backend failed")
                return

            head, _, payload = result.stdout.partition(b"\r\n\r\n")
            status = 200
            headers = []
            for line in head.decode("latin-1").splitlines():
                key, _, value = line.partition(":")
                if key.lower() == "status":
                    status = int(value.strip().split()[0])
                else:
                    headers.append((key, value.strip()))

            self.send_response(status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.host_port = f"127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{server.host_port}/{ORG}", created, dest_repo_path
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_azure_to_azure_mirror_creates_and_pushes(azure_stub, tmp_path):
    """Source -> local mirror -> a destination repository that did not exist yet."""
    org_url, created, dest_repo_path = azure_stub

    source = AzureDevOpsProvider(org_url=org_url, token="src-pat")
    destination = AzureDevOpsDestinationProvider(org_url=org_url, token="dst-pat",
                                                 project=DEST_PROJECT)

    repos = source.fetch_repos()
    assert [r.name for r in repos] == [MIRROR_NAME]
    repo = repos[0]

    # The push URL is built from the destination's own org/project, not from the
    # source clone URL the API reported.
    assert destination.get_remote_url(repo) == (
        f"http://oauth2:dst-pat@{urlparse(org_url).netloc}"
        f"/{ORG}/{DEST_PROJECT}/_git/{MIRROR_NAME}"
    )

    storage = tmp_path / "mirror-data"
    sync_one_repo(repo, storage_path=str(storage),
                  source_provider=source, destination_provider=destination)

    # The repository was provisioned through the REST API under the mirror name...
    assert created == [MIRROR_NAME]

    # ...and the push really landed: branches *and* tags, in the bare repo on disk.
    refs = subprocess.run(["git", "-C", str(dest_repo_path(MIRROR_NAME)), "show-ref"],
                          capture_output=True, text=True, check=True).stdout
    assert "refs/heads/main" in refs
    assert "refs/tags/v1.0.0" in refs


def test_second_cycle_reuses_the_existing_repository(azure_stub, tmp_path):
    """A repeat sync must not try to create the repository again."""
    org_url, created, dest_repo_path = azure_stub

    source = AzureDevOpsProvider(org_url=org_url, token="src-pat")
    destination = AzureDevOpsDestinationProvider(org_url=org_url, token="dst-pat",
                                                 project=DEST_PROJECT)
    repo = source.fetch_repos()[0]
    storage = tmp_path / "mirror-data"

    for _ in range(2):
        sync_one_repo(repo, storage_path=str(storage),
                      source_provider=source, destination_provider=destination)

    assert created == [MIRROR_NAME]

    log = subprocess.run(["git", "-C", str(dest_repo_path(MIRROR_NAME)),
                          "log", "--oneline", "main"],
                         capture_output=True, text=True, check=True).stdout
    assert "initial" in log


def _dest_refs(path):
    return subprocess.run(["git", "-C", str(path), "show-ref"],
                          capture_output=True, text=True, check=True).stdout


def test_push_leaves_azure_owned_refs_alone(azure_stub, git_root, tmp_path):
    """
    `git push --mirror` would be wrong here in both directions.

    It would try to write the source's `refs/pull/*` (which Azure DevOps
    reserves) and to delete Azure's own server-managed refs, which have no local
    counterpart. Only branches and tags may move.
    """
    org_url, _, dest_repo_path = azure_stub

    source = AzureDevOpsProvider(org_url=org_url, token="src-pat")
    destination = AzureDevOpsDestinationProvider(org_url=org_url, token="dst-pat",
                                                 project=DEST_PROJECT)
    repo = source.fetch_repos()[0]
    storage = tmp_path / "mirror-data"

    sync_one_repo(repo, storage_path=str(storage),
                  source_provider=source, destination_provider=destination)

    dest = dest_repo_path(MIRROR_NAME)
    refs = _dest_refs(dest)
    assert "refs/heads/main" in refs
    assert "refs/heads/feature" in refs
    assert "refs/tags/v1.0.0" in refs
    # The source's PR refs stayed on the source side.
    assert "refs/pull/" not in refs

    # Azure DevOps keeps refs of its own under the reserved namespace; stand one
    # up, then delete a source branch so the next sync has pruning to do.
    _git("git", "-C", str(dest), "update-ref", "refs/pull/42/merge", "refs/heads/main")
    _git("git", "-C", str(git_root / SOURCE_GIT_PATH.lstrip("/")),
         "branch", "-q", "-D", "feature")

    sync_one_repo(repo, storage_path=str(storage),
                  source_provider=source, destination_provider=destination)

    refs = _dest_refs(dest)
    assert "refs/heads/feature" not in refs, "a deleted source branch must be pruned"
    assert "refs/pull/42/merge" in refs, "the destination's own ref must survive"
    assert "refs/heads/main" in refs
