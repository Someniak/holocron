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
import hmac
import json
import hashlib
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .logger import logger
from .providers.base import Repository

# GitHub caps webhook payloads at 25 MB. Refuse anything larger so a bad/hostile
# Content-Length can't make us buffer unbounded data.
_MAX_BODY_BYTES = 25 * 1024 * 1024


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


def _make_handler(secret, on_push, path):
    """Builds a request handler class closed over the server's config."""

    class WebhookHandler(BaseHTTPRequestHandler):
        # Silence BaseHTTPRequestHandler's default stderr access logging; we log
        # through our own logger instead.
        def log_message(self, fmt, *args):
            logger.debug("[webhook] " + (fmt % args))

        def _reply(self, code, message):
            body = message.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path.split("?", 1)[0] != path:
                self._reply(404, "not found")
                return

            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._reply(400, "invalid Content-Length")
                return
            if length <= 0 or length > _MAX_BODY_BYTES:
                self._reply(400, "invalid body size")
                return

            body = self.rfile.read(length)

            # Authenticate every delivery before parsing anything untrusted.
            signature = self.headers.get("X-Hub-Signature-256", "")
            if not verify_signature(secret, body, signature):
                logger.warning("[webhook] Rejected delivery: invalid or missing signature.")
                self._reply(401, "invalid signature")
                return

            event = self.headers.get("X-GitHub-Event", "")
            if event == "ping":
                self._reply(200, "pong")
                return
            if event != "push":
                # Acknowledge other events so GitHub marks the delivery OK.
                self._reply(204, "")
                return

            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self._reply(400, "invalid JSON")
                return

            repo = build_repo_from_payload(payload)
            if repo is None:
                logger.warning("[webhook] Push event missing repository name/clone_url; ignoring.")
                self._reply(400, "missing repository fields")
                return

            logger.info(f"[webhook] Received push for '{repo.name}'; scheduling sync.")
            on_push(repo)
            self._reply(202, "accepted")

        def do_GET(self):
            # Cheap liveness/health endpoint.
            if self.path.split("?", 1)[0] == path:
                self._reply(200, "holocron webhook listener")
            else:
                self._reply(404, "not found")

    return WebhookHandler


def start_webhook_server(port, secret, on_push, path="/webhook", host="0.0.0.0"):
    """
    Starts the webhook HTTP server on a background daemon thread.

    on_push(repo) is invoked for each valid push event and should return quickly
    (e.g. submit to a thread pool); it runs on the server's request thread.

    Returns the ThreadingHTTPServer so the caller can shut it down.
    """
    handler_cls = _make_handler(secret, on_push, path)
    server = ThreadingHTTPServer((host, port), handler_cls)

    thread = threading.Thread(
        target=server.serve_forever,
        name="holocron-webhook",
        daemon=True,
    )
    thread.start()
    logger.info(f"[webhook] Listening on http://{host}:{port}{path} for GitHub push events.")
    return server
