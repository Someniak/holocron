import os
import ssl
import time
import hmac
import json
import shutil
import socket
import hashlib
import subprocess
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
import requests

from holocron.webhook import (
    verify_signature,
    build_repo_from_payload,
    start_webhook_server,
    build_ssl_context,
)
from holocron.__main__ import start_webhook_listener

SECRET = "s3cr3t"


def sign(secret, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- verify_signature ---

def test_verify_signature_valid():
    body = b'{"a": 1}'
    assert verify_signature(SECRET, body, sign(SECRET, body)) is True


def test_verify_signature_wrong_secret():
    body = b'{"a": 1}'
    assert verify_signature(SECRET, body, sign("other", body)) is False


def test_verify_signature_missing_or_malformed():
    body = b"x"
    assert verify_signature(SECRET, body, "") is False
    assert verify_signature(SECRET, body, "md5=abc") is False
    assert verify_signature("", body, sign(SECRET, body)) is False


# --- build_repo_from_payload ---

def test_build_repo_from_payload_basic():
    payload = {"repository": {"name": "myrepo", "clone_url": "https://github.com/u/myrepo.git", "size": 42, "full_name": "u/myrepo"}}
    repo = build_repo_from_payload(payload)
    assert repo.name == "myrepo"
    assert repo.clone_url == "https://github.com/u/myrepo.git"
    assert repo.size == 42
    assert repo.full_name == "u/myrepo"
    assert repo.pushed_at is not None


def test_build_repo_full_name_defaults_none_when_absent():
    payload = {"repository": {"name": "r", "clone_url": "https://x/r.git"}}
    repo = build_repo_from_payload(payload)
    assert repo.full_name is None


def test_build_repo_parses_unix_pushed_at():
    ts = 1700000000
    payload = {"repository": {"name": "r", "clone_url": "https://x/r.git", "pushed_at": ts}}
    repo = build_repo_from_payload(payload)
    expected = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    assert repo.pushed_at == expected


def test_build_repo_missing_fields_returns_none():
    assert build_repo_from_payload({}) is None
    assert build_repo_from_payload({"repository": {"name": "r"}}) is None
    assert build_repo_from_payload({"repository": {"clone_url": "u"}}) is None


# --- HTTP server integration ---

@pytest.fixture
def server():
    received = []
    srv = start_webhook_server(
        port=0,  # ephemeral
        secret=SECRET,
        on_push=received.append,
        path="/webhook",
        host="127.0.0.1",
    )
    port = srv.server_address[1]
    yield f"http://127.0.0.1:{port}", received
    srv.shutdown()
    srv.server_close()


def _push_body():
    return json.dumps({
        "repository": {"name": "hook-repo", "clone_url": "https://github.com/u/hook-repo.git"}
    }).encode()


def test_valid_push_returns_202_and_triggers(server):
    base, received = server
    body = _push_body()
    r = requests.post(
        f"{base}/webhook",
        data=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )
    assert r.status_code == 202
    assert len(received) == 1
    assert received[0].name == "hook-repo"


def _pull_request_body(action="opened"):
    return json.dumps({
        "action": action,
        "number": 7,
        "repository": {"name": "hook-repo", "clone_url": "https://github.com/u/hook-repo.git"},
    }).encode()


def test_pull_request_opened_returns_202_and_triggers(server):
    base, received = server
    body = _pull_request_body("opened")
    r = requests.post(
        f"{base}/webhook",
        data=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )
    assert r.status_code == 202
    assert len(received) == 1
    assert received[0].name == "hook-repo"


def test_pull_request_synchronize_triggers(server):
    base, received = server
    body = _pull_request_body("synchronize")
    r = requests.post(
        f"{base}/webhook",
        data=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )
    assert r.status_code == 202
    assert len(received) == 1


def test_pull_request_irrelevant_action_returns_204(server):
    """A pull_request action that doesn't change the head set is acked, not synced."""
    base, received = server
    body = _pull_request_body("labeled")
    r = requests.post(
        f"{base}/webhook",
        data=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )
    assert r.status_code == 204
    assert received == []


def test_invalid_signature_returns_404_not_401(server):
    """Unauthenticated requests look like a nonexistent endpoint (404), not a
    401 that would advertise an HMAC-protected webhook is here."""
    base, received = server
    body = _push_body()
    r = requests.post(
        f"{base}/webhook",
        data=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign("wrong", body)},
        timeout=5,
    )
    assert r.status_code == 404
    assert received == []


def test_ping_event_returns_204(server):
    base, received = server
    body = b"{}"
    r = requests.post(
        f"{base}/webhook",
        data=body,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )
    assert r.status_code == 204
    assert received == []


def test_wrong_path_returns_404(server):
    base, received = server
    body = _push_body()
    r = requests.post(
        f"{base}/nope",
        data=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )
    assert r.status_code == 404
    assert received == []


# --- Anti-fingerprinting: unauthenticated probes learn nothing ---

def test_get_does_not_reveal_software(server):
    """GET on the webhook path must not announce holocron or return 200."""
    base, received = server
    r = requests.get(f"{base}/webhook", timeout=5)
    assert r.status_code == 404
    assert "holocron" not in r.text.lower()
    assert received == []


def test_no_server_banner_header(server):
    """No 'Server: BaseHTTP/x Python/y' banner that fingerprints the stack."""
    base, _ = server
    r = requests.get(f"{base}/webhook", timeout=5)
    server_hdr = r.headers.get("Server", "")
    assert "python" not in server_hdr.lower()
    assert "basehttp" not in server_hdr.lower()


def test_unsupported_method_returns_404_not_501(server):
    """Probe verbs get a bland 404, not the stdlib's 501 'Unsupported method' --
    including exotic/non-standard verbs, which the do_* catch-all also covers."""
    base, received = server
    for method in ("PUT", "DELETE", "OPTIONS", "PATCH", "HEAD", "TRACE", "BREW"):
        r = requests.request(method, f"{base}/webhook", timeout=5)
        assert r.status_code == 404, f"{method} returned {r.status_code}"
    assert received == []


def test_probe_responses_are_uniform(server):
    """A known path and a random path return identical status + body, so a
    scanner cannot distinguish the webhook path from any other."""
    base, _ = server
    r_known = requests.get(f"{base}/webhook", timeout=5)
    r_random = requests.get(f"{base}/some-random-path", timeout=5)
    assert r_known.status_code == r_random.status_code == 404
    assert r_known.text == r_random.text


# --- End-to-end: HTTP delivery drives the real sync engine (via __main__) ---

def _wait_for(predicate, timeout=5.0):
    """Polls until predicate() is truthy (sync runs async on a worker thread)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@patch("holocron.__main__.sync_one_repo")
def test_webhook_delivery_triggers_sync_one_repo(mock_sync):
    """A signed push POST should flow through start_webhook_listener into
    sync_one_repo with the payload's repo, and update synced_pushes on success."""
    config = {
        "concurrency": 2,
        "storage": "/tmp/holo-test",
        "dry_run": False,
        "backup_only": False,
        "checkout": False,
        "webhook_port": 0,  # ephemeral
        "webhook_path": "/webhook",
    }
    synced_pushes = {}
    source_provider = MagicMock()
    destination_provider = MagicMock()

    with patch.dict(os.environ, {"HOLOCRON_WEBHOOK_SECRET": SECRET}):
        server = start_webhook_listener(config, source_provider, destination_provider, synced_pushes)

    try:
        port = server.server_address[1]
        body = _push_body()
        r = requests.post(
            f"http://127.0.0.1:{port}/webhook",
            data=body,
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(SECRET, body)},
            timeout=5,
        )
        assert r.status_code == 202

        # Sync runs on a background worker; wait for it to be invoked.
        assert _wait_for(lambda: mock_sync.called), "sync_one_repo was never called"

        _, kwargs = mock_sync.call_args
        assert kwargs["repo"].name == "hook-repo"
        assert kwargs["source_provider"] is source_provider
        assert kwargs["destination_provider"] is destination_provider
        assert kwargs["backup_only"] is False

        # done-callback records the push once the (mocked, successful) sync returns.
        assert _wait_for(lambda: "hook-repo" in synced_pushes)
    finally:
        server.shutdown()
        server.server_close()


@patch("holocron.__main__.sync_one_repo")
def test_webhook_missing_secret_exits(mock_sync):
    """--webhook without HOLOCRON_WEBHOOK_SECRET must refuse to start."""
    config = {"concurrency": 1, "storage": "/tmp/x", "dry_run": False,
              "backup_only": False, "checkout": False,
              "webhook_port": 0, "webhook_path": "/webhook"}
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(SystemExit):
            start_webhook_listener(config, MagicMock(), MagicMock(), {})


# --- TLS / HTTPS support ---

def test_build_ssl_context_requires_both():
    """A lone cert or key is a config error, not a silent plaintext fallback."""
    with pytest.raises(ValueError):
        build_ssl_context("only-cert.pem", None)
    with pytest.raises(ValueError):
        build_ssl_context(None, "only-key.pem")


def test_build_ssl_context_missing_files():
    with pytest.raises(FileNotFoundError):
        build_ssl_context("/nope/cert.pem", "/nope/key.pem")


@pytest.fixture
def self_signed_cert(tmp_path):
    """Generates a throwaway self-signed cert/key with openssl (skips if absent)."""
    if not shutil.which("openssl"):
        pytest.skip("openssl not available")
    cert = tmp_path / "webhook.crt"
    key = tmp_path / "webhook.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return str(cert), str(key)


def test_build_ssl_context_valid_pair(self_signed_cert):
    cert, key = self_signed_cert
    ctx = build_ssl_context(cert, key)
    assert isinstance(ctx, ssl.SSLContext)


@pytest.mark.filterwarnings("ignore::urllib3.exceptions.InsecureRequestWarning")
def test_https_delivery_returns_202(self_signed_cert):
    """With cert+key, the listener serves HTTPS and accepts a signed push."""
    cert, key = self_signed_cert
    received = []
    srv = start_webhook_server(
        port=0, secret=SECRET, on_push=received.append,
        path="/webhook", host="127.0.0.1", cert_file=cert, key_file=key,
    )
    try:
        port = srv.server_address[1]
        body = _push_body()
        # verify=False: the cert is self-signed, so skip chain validation here.
        r = requests.post(
            f"https://127.0.0.1:{port}/webhook",
            data=body,
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(SECRET, body)},
            timeout=5,
            verify=False,
        )
        assert r.status_code == 202
        assert len(received) == 1
        assert received[0].name == "hook-repo"
    finally:
        srv.shutdown()
        srv.server_close()


# --- Malformed but correctly signed payloads must not drop the connection ---

def _post_signed(base, payload_bytes, event="push"):
    return requests.post(
        f"{base}/webhook",
        data=payload_bytes,
        headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sign(SECRET, payload_bytes)},
        timeout=5,
    )


@pytest.mark.parametrize("body", [b"[]", b'"str"', b"42", b"null", b"[1,2,3]"])
def test_signed_non_object_payload_gets_400(server, body):
    """A JSON array/string/number/null body is valid JSON but not a payload."""
    base, received = server
    r = _post_signed(base, body)
    assert r.status_code == 400
    assert received == []


@pytest.mark.parametrize("repository", ["scalar", 7, ["a"], None])
def test_signed_non_dict_repository_gets_400(server, repository):
    base, received = server
    body = json.dumps({"repository": repository}).encode()
    r = _post_signed(base, body)
    assert r.status_code == 400
    assert received == []


@pytest.mark.parametrize("action_holder", [[], "x", 5])
def test_pull_request_with_non_object_payload_gets_400(server, action_holder):
    base, received = server
    body = json.dumps(action_holder).encode()
    r = _post_signed(base, body, event="pull_request")
    assert r.status_code == 400
    assert received == []


def test_signed_out_of_range_pushed_at_does_not_crash(server):
    """An OverflowError from the timestamp conversion must not drop the socket."""
    base, received = server
    body = json.dumps({
        "repository": {
            "name": "hook-repo",
            "clone_url": "https://github.com/u/hook-repo.git",
            "pushed_at": 10 ** 30,
        }
    }).encode()
    r = _post_signed(base, body)
    assert r.status_code == 202
    assert len(received) == 1
    assert received[0].pushed_at is not None


def test_signed_non_utf8_body_gets_400(server):
    """UnicodeDecodeError is not a JSONDecodeError; it must still answer."""
    base, received = server
    body = b'{"repository": "\xff\xfe"}'
    r = _post_signed(base, body)
    assert r.status_code == 400
    assert received == []


def test_stale_delivery_is_not_synced(server):
    """A replayed, long-stale signed delivery must not queue a sync."""
    base, received = server
    body = json.dumps({
        "repository": {
            "name": "hook-repo",
            "clone_url": "https://github.com/u/hook-repo.git",
            "pushed_at": 1,  # 1970
        }
    }).encode()
    r = _post_signed(base, body)
    assert r.status_code == 204
    assert received == []


# --- Admission control on the read path ---

def test_stalled_body_is_dropped_within_the_read_timeout():
    """An unauthenticated client cannot pin a handler thread indefinitely.

    Declares a large Content-Length, sends a few bytes, then stalls. The read
    must time out and the connection must be reclaimed.
    """
    from holocron import webhook as webhook_module

    received = []
    srv = start_webhook_server(
        port=0, secret=SECRET, on_push=received.append,
        path="/webhook", host="127.0.0.1",
    )
    port = srv.server_address[1]
    original = webhook_module._READ_TIMEOUT_SECONDS
    webhook_module._READ_TIMEOUT_SECONDS = 0.5
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.sendall(
            b"POST /webhook HTTP/1.1\r\nHost: h\r\n"
            b"Content-Length: 100000\r\n\r\nAAAAAAAAAA"
        )
        # The server must answer (or close) rather than hang forever.
        sock.settimeout(10)
        deadline = time.monotonic() + 10
        got_data = False
        while time.monotonic() < deadline and not got_data:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if chunk:
                got_data = True
        assert got_data, "listener never responded to a stalled body"
        sock.close()
        assert received == []
    finally:
        webhook_module._READ_TIMEOUT_SECONDS = original
        srv.shutdown()
        srv.server_close()


def test_slow_drip_cannot_extend_the_request_deadline(monkeypatch):
    """A client trickling bytes must not renew the per-recv timeout forever.

    The per-recv timeout is deliberately large here, so only the wall-clock
    request deadline can end the read.
    """
    import holocron.webhook as webhook_module

    monkeypatch.setattr(webhook_module, "_MAX_REQUEST_SECONDS", 1.0)
    monkeypatch.setattr(webhook_module, "_READ_TIMEOUT_SECONDS", 10.0)

    received = []
    srv = start_webhook_server(
        port=0, secret=SECRET, on_push=received.append,
        path="/webhook", host="127.0.0.1",
    )
    try:
        sock = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
        sock.sendall(
            b"POST /webhook HTTP/1.1\r\nHost: h\r\n"
            b"Content-Length: 100\r\n\r\n"
        )
        start = time.monotonic()
        got_data = False
        while time.monotonic() - start < 3:
            try:
                sock.sendall(b"A")
            except OSError:
                break
            try:
                if sock.recv(4096):
                    got_data = True
                    break
            except socket.timeout:
                pass
            time.sleep(0.1)
        elapsed = time.monotonic() - start
        if not got_data:
            try:
                sock.settimeout(1)
                got_data = bool(sock.recv(4096))
            except (socket.timeout, OSError):
                got_data = False
        assert got_data, "listener never terminated a slow-drip request"
        assert elapsed < 2.5, f"request deadline not enforced (took {elapsed:.1f}s)"
        sock.close()
        assert received == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_connection_limit_drops_excess_connections(monkeypatch):
    """The listener must not spawn an unbounded thread per connection."""
    import holocron.webhook as webhook_module

    monkeypatch.setattr(webhook_module, "_MAX_CONCURRENT_CONNECTIONS", 2)
    monkeypatch.setattr(webhook_module, "_MAX_REQUEST_SECONDS", 10.0)

    srv = start_webhook_server(
        port=0, secret=SECRET, on_push=lambda repo: None,
        path="/webhook", host="127.0.0.1",
    )
    held = []
    try:
        for _ in range(2):
            sock = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
            # A partial request keeps the handler alive and the slot held.
            sock.sendall(b"POST /webhook HTTP/1.1\r\nHost: h\r\n")
            held.append(sock)

        deadline = time.monotonic() + 2
        while srv._slots._value > 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert srv._slots._value == 0, "test did not fill the connection slots"

        excess = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5)
        excess.settimeout(2)
        try:
            excess.sendall(b"POST /webhook HTTP/1.1\r\nHost: h\r\n")
            data = excess.recv(1024)
        except (socket.timeout, ConnectionResetError):
            data = b""
        finally:
            excess.close()
        assert not data, "excess connection was not dropped"
    finally:
        for sock in held:
            sock.close()
        srv.shutdown()
        srv.server_close()


def test_short_body_is_rejected_not_hmaced(monkeypatch):
    """A truncated body must be refused rather than verified as if complete."""
    import holocron.webhook as webhook_module

    monkeypatch.setattr(webhook_module, "_READ_TIMEOUT_SECONDS", 0.5)
    received = []
    srv = start_webhook_server(
        port=0, secret=SECRET, on_push=received.append,
        path="/webhook", host="127.0.0.1",
    )
    port = srv.server_address[1]
    try:
        body = _push_body()
        truncated = body[:5]
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        # Claim the full length, deliver only part of it, then half-close so the
        # read returns short instead of blocking.
        sock.sendall(
            b"POST /webhook HTTP/1.1\r\nHost: h\r\n"
            b"X-GitHub-Event: push\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + truncated
        )
        sock.shutdown(socket.SHUT_WR)
        sock.settimeout(10)
        data = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        sock.close()
        assert b" 202 " not in data and b" 200 " not in data
        assert received == []
    finally:
        srv.shutdown()
        srv.server_close()


# --- Admission control on the submission path (unbounded queue / starvation) ---

def _listener_config(**overrides):
    config = {
        "concurrency": 1,
        "storage": "/tmp/holo-test",
        "dry_run": False,
        "backup_only": False,
        "checkout": False,
        "webhook_port": 0,
        "webhook_path": "/webhook",
    }
    config.update(overrides)
    return config


def _deliver(port, name="hook-repo", pushed_at=None):
    repo = {
        "name": name,
        "clone_url": f"https://github.com/u/{name}.git",
        "full_name": f"u/{name}",
    }
    if pushed_at is not None:
        repo["pushed_at"] = pushed_at
    body = json.dumps({"repository": repo}).encode()
    return requests.post(
        f"http://127.0.0.1:{port}/webhook",
        data=body,
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(SECRET, body)},
        timeout=5,
    )


@patch("holocron.__main__.sync_one_repo")
def test_repeated_deliveries_for_one_repo_are_coalesced(mock_sync):
    """A burst for one repository must not fill the queue and starve others."""
    import threading

    release = threading.Event()
    started = threading.Event()

    def _blocking_sync(**kwargs):
        started.set()
        release.wait(10)

    mock_sync.side_effect = _blocking_sync

    with patch.dict(os.environ, {"HOLOCRON_WEBHOOK_SECRET": SECRET}):
        server = start_webhook_listener(_listener_config(), MagicMock(), MagicMock(), {})
    try:
        port = server.server_address[1]
        assert _deliver(port).status_code == 202
        assert started.wait(5), "first sync never started"

        # While one sync for this repo is in flight, further deliveries for the
        # same repo are acknowledged but must not queue more work.
        for _ in range(20):
            assert _deliver(port).status_code == 202

        time.sleep(0.2)
        assert mock_sync.call_count == 1

        # A different repository is still served, i.e. it was not starved.
        mock_sync.side_effect = None
        release.set()
        time.sleep(0.2)
        assert _deliver(port, name="other-repo").status_code == 202
        assert _wait_for(lambda: mock_sync.call_count == 2)
    finally:
        release.set()
        server.shutdown()
        server.server_close()


@patch("holocron.__main__.sync_one_repo")
def test_sequential_deliveries_all_sync(mock_sync):
    """Coalescing must not permanently suppress later pushes to one repo."""
    with patch.dict(os.environ, {"HOLOCRON_WEBHOOK_SECRET": SECRET}):
        server = start_webhook_listener(_listener_config(), MagicMock(), MagicMock(), {})
    try:
        port = server.server_address[1]
        now = int(time.time())
        for i in range(3):
            # Fresh timestamps: the listener drops deliveries outside its window.
            assert _deliver(port, pushed_at=now + i).status_code == 202
            assert _wait_for(lambda n=i + 1: mock_sync.call_count == n), mock_sync.call_count
    finally:
        server.shutdown()
        server.server_close()


@patch("holocron.__main__.sync_one_repo")
def test_pending_queue_is_bounded_and_overflow_is_dropped(mock_sync):
    """The pending set is capped; overflow is dropped, never queued unbounded."""
    import threading
    import holocron.__main__ as main_module

    release = threading.Event()

    def _blocking_sync(**kwargs):
        release.wait(10)

    mock_sync.side_effect = _blocking_sync

    with patch.dict(os.environ, {"HOLOCRON_WEBHOOK_SECRET": SECRET}), \
         patch.object(main_module, "_WEBHOOK_MAX_PENDING", 3):
        server = start_webhook_listener(
            _listener_config(concurrency=1), MagicMock(), MagicMock(), {}
        )
    try:
        port = server.server_address[1]
        for i in range(10):
            # Every delivery is still accepted by the listener (no 503, which
            # would make the forge retry and reintroduce the flood) ...
            assert _deliver(port, name=f"repo-{i}").status_code == 202
        time.sleep(0.3)
        # ... but only the cap plus the running job ever reached the pool.
        assert mock_sync.call_count <= 4, mock_sync.call_count
    finally:
        release.set()
        server.shutdown()
        server.server_close()


@patch("holocron.__main__.sync_one_repo")
def test_unsigned_delivery_still_creates_no_work(mock_sync):
    with patch.dict(os.environ, {"HOLOCRON_WEBHOOK_SECRET": SECRET}):
        server = start_webhook_listener(_listener_config(), MagicMock(), MagicMock(), {})
    try:
        port = server.server_address[1]
        body = _push_body()
        r = requests.post(
            f"http://127.0.0.1:{port}/webhook",
            data=body,
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign("wrong", body)},
            timeout=5,
        )
        assert r.status_code == 404
        time.sleep(0.2)
        mock_sync.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
