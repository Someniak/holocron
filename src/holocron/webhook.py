"""
Webhook listener for push-triggered syncs.

GitHub can POST a `push` event to Holocron whenever a repository changes. Instead
of waiting for the next poll cycle, we verify the delivery, build a Repository
from the payload, and hand it straight to the existing sync engine on a worker
thread. The HTTP request returns immediately (202) so we never hit GitHub's
~10s delivery timeout.

Uses only the standard library (http.server + hmac) to avoid adding a web
framework dependency.
"""
import os
import ssl
import hmac
import json
import hashlib
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .logger import logger
from .providers.base import Repository, PullRequestEvent

# PR actions worth acting on. Others (labeled, assigned, edited, ...) are
# acknowledged but ignored so they don't re-run CI.
_ACTIONABLE_PR_ACTIONS = ("opened", "synchronize", "reopened", "closed")

# GitHub caps webhook payloads at 25 MB. Refuse anything larger so a bad/hostile
# Content-Length can't make us buffer unbounded data.
_MAX_BODY_BYTES = 25 * 1024 * 1024

# Generic 404 body returned for anything that isn't a valid signed delivery.
# Deliberately bland and server-agnostic: it names no software and matches the
# shape of a stock "nothing here" page so a scanner learns nothing.
_NOT_FOUND_BODY = b"<html><head><title>404 Not Found</title></head><body><h1>404 Not Found</h1></body></html>"

def verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """
    Validates GitHub's `X-Hub-Signature-256` header against the raw request body.

    The header looks like `sha256=<hexdigest>`. Comparison is constant-time.
    Returns False on any missing/malformed input rather than raising.
    """
    if not secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    received = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


def build_repo_from_payload(payload: dict):
    """
    Builds a Repository from a GitHub push-event payload.

    Uses the bare repo `name` (matching the polling path, which keys mirror
    directories and GitLab paths on the short name). Returns None if the payload
    lacks the fields we need to sync.
    """
    repo = payload.get("repository") or {}
    name = repo.get("name")
    clone_url = repo.get("clone_url")
    if not name or not clone_url:
        return None

    # `pushed_at` in a push event is a unix timestamp (int or numeric string).
    # It's only used to update the synced_pushes bookkeeping; fall back to "now"
    # so the poll loop treats this push as freshly seen either way.
    pushed_at = None
    raw = repo.get("pushed_at")
    try:
        if raw is not None:
            pushed_at = datetime.fromtimestamp(int(raw), tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError, OSError):
        pushed_at = None
    if pushed_at is None:
        pushed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    return Repository(
        name=name,
        clone_url=clone_url,
        size=repo.get("size", 0),
        pushed_at=pushed_at,
    )


def build_pr_event_from_payload(payload: dict):
    """
    Builds a PullRequestEvent from a GitHub `pull_request` webhook payload.

    Uses the bare repo `name` (matching the sync path, which keys mirror dirs and
    GitLab paths on the short name) and the `full_name` for the status write-back.
    Returns None if the payload lacks a field the CI bridge needs.
    """
    action = payload.get("action")
    number = payload.get("number")
    repo = payload.get("repository") or {}
    pr = payload.get("pull_request") or {}
    head = pr.get("head") or {}
    base = pr.get("base") or {}

    repo_full_name = repo.get("full_name")
    repo_name = repo.get("name")
    clone_url = repo.get("clone_url")
    head_sha = head.get("sha")
    head_ref = head.get("ref")
    base_ref = base.get("ref")

    if not (action and number is not None and repo_full_name and repo_name
            and clone_url and head_sha and head_ref and base_ref):
        return None

    # The head repo is null when a fork has been deleted; treat that (and any
    # head whose repo differs from the base repo) as a fork we don't trust.
    head_repo = head.get("repo") or {}
    is_fork = head_repo.get("full_name") != repo_full_name

    try:
        number = int(number)
    except (TypeError, ValueError):
        return None

    return PullRequestEvent(
        action=action,
        number=number,
        repo_full_name=repo_full_name,
        repo_name=repo_name,
        clone_url=clone_url,
        head_sha=head_sha,
        head_ref=head_ref,
        base_ref=base_ref,
        is_fork=is_fork,
        merged=bool(pr.get("merged", False)),
    )


def _make_handler(secret, on_push, path, on_pull_request=None):
    """Builds a request handler class closed over the server's config."""

    class WebhookHandler(BaseHTTPRequestHandler):
        # Suppress the identifying "Server: BaseHTTP/x Python/y" banner. This is
        # the sole reader of server_version/sys_version, and it also covers the
        # stdlib send_error() path (e.g. a malformed request line) that _reply's
        # send_response_only() otherwise bypasses.
        def version_string(self):
            return ""

        # Silence BaseHTTPRequestHandler's default stderr access logging; we log
        # through our own logger instead.
        def log_message(self, fmt, *args):
            logger.debug("[webhook] " + (fmt % args))

        def _reply(self, code, body=b"", content_type=None):
            # send_response_only (not send_response) => no Server/Date banner and
            # no default access log line; we control every emitted header.
            self.send_response_only(code)
            if content_type:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            # Never write a body for HEAD, and none is needed for 204.
            if body and self.command != "HEAD":
                self.wfile.write(body)

        def _not_here(self):
            """Uniform, unidentifiable 404 — the same response an empty server
            gives for any unknown path. Used for every request that isn't a
            valid, correctly-signed webhook delivery, so a probe cannot tell the
            listener (or its path) apart from nothing at all."""
            self._reply(404, _NOT_FOUND_BODY, content_type="text/html; charset=utf-8")

        def do_POST(self):
            # Everything up to and including signature verification is a "does
            # this look like an authenticated delivery?" gate. Any failure here
            # is indistinguishable on the wire from a nonexistent endpoint: the
            # real reason is logged server-side, the client just sees a 404.
            if self.path.split("?", 1)[0] != path:
                self._not_here()
                return

            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._not_here()
                return
            if length <= 0 or length > _MAX_BODY_BYTES:
                self._not_here()
                return

            body = self.rfile.read(length)

            # Authenticate every delivery before parsing anything untrusted.
            signature = self.headers.get("X-Hub-Signature-256", "")
            if not verify_signature(secret, body, signature):
                logger.warning("[webhook] Rejected unauthenticated request (returning 404).")
                self._not_here()
                return

            # From here on the caller proved knowledge of the secret, so it is a
            # genuine GitHub delivery: meaningful status codes are safe to return.
            event = self.headers.get("X-GitHub-Event", "")
            if event == "ping":
                self._reply(204)
                return
            if event not in ("push", "pull_request"):
                # Acknowledge other events so GitHub marks the delivery OK.
                self._reply(204)
                return
            if event == "pull_request" and on_pull_request is None:
                # CI bridge disabled: acknowledge and ignore, exactly as before.
                self._reply(204)
                return

            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                logger.warning(f"[webhook] Authenticated {event} had invalid JSON.")
                self._reply(400, b"invalid payload", content_type="text/plain; charset=utf-8")
                return

            if event == "pull_request":
                pr = build_pr_event_from_payload(payload)
                if pr is None:
                    logger.warning("[webhook] pull_request event missing required fields; ignoring.")
                    self._reply(400, b"invalid payload", content_type="text/plain; charset=utf-8")
                    return
                if pr.action not in _ACTIONABLE_PR_ACTIONS:
                    # e.g. labeled/edited/assigned: nothing to run, but ack it.
                    self._reply(204)
                    return
                logger.info(f"[webhook] Received pull_request '{pr.action}' #{pr.number} "
                            f"for '{pr.repo_full_name}'; scheduling CI bridge.")
                on_pull_request(pr)
                self._reply(202)
                return

            repo = build_repo_from_payload(payload)
            if repo is None:
                logger.warning("[webhook] Push event missing repository name/clone_url; ignoring.")
                self._reply(400, b"invalid payload", content_type="text/plain; charset=utf-8")
                return

            logger.info(f"[webhook] Received push for '{repo.name}'; scheduling sync.")
            on_push(repo)
            self._reply(202)

        # Any method other than POST — GET/HEAD or an exotic verb — routes to the
        # same bland 404. Catching the whole do_* namespace (rather than a
        # per-verb whitelist) means unusual verbs can't fall through to the
        # stdlib's 501 "Unsupported method", which would both fingerprint the
        # server and leak the Server/Date headers _not_here() suppresses.
        def __getattr__(self, name):
            if name.startswith("do_"):
                return self._not_here
            raise AttributeError(name)

    return WebhookHandler


def build_ssl_context(cert_file, key_file):
    """
    Builds a server-side TLS context from a cert/key pair.

    Both files must be provided together and exist; a lone cert or key is a
    configuration error (raises ValueError / FileNotFoundError) rather than a
    silent fall-back to plaintext.
    """
    if bool(cert_file) != bool(key_file):
        raise ValueError(
            "webhook TLS needs BOTH a certificate and a key "
            "(--webhook-cert and --webhook-key); only one was given."
        )
    for label, path in (("certificate", cert_file), ("key", key_file)):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"webhook TLS {label} not found: {path}")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return context


def start_webhook_server(port, secret, on_push, path="/webhook", host="0.0.0.0",
                         cert_file=None, key_file=None, on_pull_request=None):
    """
    Starts the webhook HTTP(S) server on a background daemon thread.

    If cert_file and key_file are both given, the listener serves HTTPS using a
    self-signed or provided certificate; otherwise it serves plain HTTP.

    on_push(repo) is invoked for each valid push event; on_pull_request(pr), when
    provided, is invoked for each actionable pull_request event. Both should return
    quickly (e.g. submit to a thread pool); they run on the server's request
    thread. When on_pull_request is None, pull_request deliveries are acknowledged
    and ignored.

    Returns the ThreadingHTTPServer so the caller can shut it down.
    """
    handler_cls = _make_handler(secret, on_push, path, on_pull_request=on_pull_request)
    server = ThreadingHTTPServer((host, port), handler_cls)

    scheme = "http"
    if cert_file or key_file:
        context = build_ssl_context(cert_file, key_file)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    thread = threading.Thread(
        target=server.serve_forever,
        name="holocron-webhook",
        daemon=True,
    )
    thread.start()
    logger.info(f"[webhook] Listening on {scheme}://{host}:{port}{path} for GitHub push events.")
    return server
