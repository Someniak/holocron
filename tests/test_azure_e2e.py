"""
End-to-end check for the Azure DevOps source: a stub Azure DevOps API plus a
real git repository served over dumb HTTP, mirrored by the real sync engine.

Everything is local (loopback socket, temp dirs) -- no credentials, no network
-- but unlike the unit tests nothing is mocked below `fetch_repos`: `git clone
--mirror` really runs against the URL the provider builds, which is what proves
the credential injection, userinfo stripping and name slugification produce a
URL and a path git actually accepts.
"""
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import pytest

from holocron.mirror import sync_one_repo
from holocron.providers.azure import AzureDevOpsProvider

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

REPO_ID = "abc-123"
SOURCE_NAME = "Widgets Repo"  # the space must be slugified away
GIT_PATH_PREFIX = "/acme/Proj/_git/widgets"


def _git(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


@pytest.fixture
def source_repo(tmp_path):
    """A bare repo with one branch and one tag, prepared for dumb-HTTP serving."""
    work = tmp_path / "work"
    bare = tmp_path / "widgets.git"

    work.mkdir()
    _git("git", "init", "-q", "-b", "main", str(work))
    (work / "README.md").write_text("hello from azure devops\n")
    _git("git", "-C", str(work), "add", ".")
    _git("git", "-C", str(work), "-c", "user.email=t@e.st", "-c", "user.name=T",
         "commit", "-qm", "initial")
    _git("git", "-C", str(work), "tag", "v1.0.0")
    _git("git", "clone", "-q", "--bare", str(work), str(bare))
    # The dumb HTTP protocol reads static info/refs + objects/info/packs.
    _git("git", "-C", str(bare), "update-server-info")
    return bare


@pytest.fixture
def azure_stub(source_repo):
    """Serves the Azure DevOps REST endpoints and the bare repo on a loopback port."""
    seen_auth = []

    # The bare repo is complete (and `update-server-info` has run) before the
    # server starts, so its contents can be read once into an in-memory map.
    repo_files = {}
    for directory, _, filenames in os.walk(source_repo):
        for filename in filenames:
            full = os.path.join(directory, filename)
            key = os.path.relpath(full, source_repo).replace(os.sep, "/")
            repo_files[key] = open(full, "rb").read()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            seen_auth.append(self.headers.get("Authorization"))

            if path == "/acme/_apis/git/repositories":
                return self._json({"count": 1, "value": [{
                    "id": REPO_ID,
                    "name": SOURCE_NAME,
                    "project": {"name": "Proj"},
                    "defaultBranch": "refs/heads/main",
                    "size": 40960,  # bytes
                    # Azure DevOps clone URLs may carry the org as userinfo.
                    "remoteUrl": f"http://acme@{self.server.host_port}{GIT_PATH_PREFIX}",
                }]})

            if path == f"/acme/_apis/git/repositories/{REPO_ID}/pushes":
                return self._json({"count": 1, "value": [
                    {"date": "2099-01-01T00:00:00.1234567Z"},
                ]})

            if path.startswith(f"{GIT_PATH_PREFIX}/"):
                # Looked up in a map built from the repo before serving started,
                # so the request path never reaches the filesystem: an unknown
                # (or traversing) path simply misses and 404s.
                body = repo_files.get(path[len(GIT_PATH_PREFIX) + 1:])
                if body is not None:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)

            self.send_error(404)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.host_port = f"127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{server.host_port}/acme", seen_auth
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_azure_source_mirrors_to_local_disk(azure_stub, tmp_path):
    org_url, seen_auth = azure_stub
    provider = AzureDevOpsProvider(org_url=org_url, token="fake-pat")

    repos = provider.fetch_repos()
    assert len(repos) == 1
    repo = repos[0]

    # Name slugified, size converted from bytes to KB, push date parsed.
    assert repo.name == "Widgets-Repo"
    assert repo.size == 40
    assert repo.pushed_at.year == 2099
    assert repo.full_name == "Proj/Widgets Repo"

    # The PAT travelled as Basic auth on every API call.
    assert seen_auth and all(a and a.startswith("Basic ") for a in seen_auth)

    storage = tmp_path / "mirror-data"
    sync_one_repo(repo, storage_path=str(storage), backup_only=True, source_provider=provider)

    mirror = storage / "Widgets-Repo.git"
    assert mirror.is_dir(), f"expected a mirror at {mirror}, found {list(storage.iterdir())}"

    refs = subprocess.run(["git", "-C", str(mirror), "show-ref"],
                          capture_output=True, text=True, check=True).stdout
    # A true mirror: branches *and* tags, not just the default branch.
    assert "refs/heads/main" in refs
    assert "refs/tags/v1.0.0" in refs
