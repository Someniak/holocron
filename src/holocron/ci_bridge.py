"""
Pull Request -> GitLab CI -> GitHub status bridge.

When a GitHub PR is opened/updated, the mirror already holds the PR head commit
(a `git clone --mirror` of a GitHub repo fetches `refs/pull/*`). This module
pushes that head to a real GitLab branch, opens a GitLab Merge Request against
the PR base branch (which fires the `merge_request_event` pipeline in
.gitlab-ci.yml), then polls the pipeline and writes the result back onto the PR
as a GitHub commit status.

Reporting is *outbound from here* on purpose: the self-hosted GitLab is
internal-only, so neither GitHub nor a GitHub Action can reach in — but holocron
reaches both sides and holds both tokens, so it is the natural bridge.

The head SHA is identical on GitHub and in the mirror, so the GitHub status
(keyed on the head SHA) and the GitLab pipeline (run on that SHA) line up.
"""
import os
import time
import subprocess

from .logger import logger, log_execution
from .mirror import _ensure_local_mirror, _get_repo_lock
from .utils import (
    redact,
    is_safe_repo_name,
    is_safe_repo_full_name,
    is_safe_clone_url,
    is_safe_git_ref,
    is_safe_sha,
)
from .providers.base import Repository

# GitLab pipeline statuses that mean "the pipeline is done".
_TERMINAL = {"success", "failed", "canceled", "skipped", "manual"}


def _map_status(gitlab_status):
    """Maps a terminal GitLab pipeline status onto a GitHub commit-status state."""
    if gitlab_status == "success":
        return "success"
    if gitlab_status == "failed":
        return "failure"
    # canceled / skipped / manual: neither a clean pass nor a test failure.
    return "error"


def _source_branch(prefix, number):
    """The GitLab branch we mirror the PR head onto. Built from the int number
    only, so it is injection-safe by construction."""
    return f"{prefix}{int(number)}"


def _push_ref(repo_dir, repo, refspec, gitlab_provider):
    """Force-pushes a single refspec from the bare mirror to GitLab."""
    push_url = gitlab_provider.get_remote_url(repo)
    try:
        subprocess.run(
            ["git", "-C", repo_dir, "push", push_url, refspec],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as e:
        # e.stderr / e.cmd echo the token-embedded push URL; redact before raising.
        err = e.stderr.decode().strip() if e.stderr else str(e)
        raise subprocess.CalledProcessError(e.returncode, "git push", stderr=redact(err))


def _push_pr_head(repo_dir, repo, number, source_branch, gitlab_provider):
    """Force-pushes refs/pull/<N>/head from the bare mirror to a GitLab branch."""
    _push_ref(repo_dir, repo, f"+refs/pull/{int(number)}/head:refs/heads/{source_branch}",
              gitlab_provider)


def _push_branch(repo_dir, repo, branch, gitlab_provider):
    """Force-pushes a branch head from the bare mirror to the same GitLab branch."""
    _push_ref(repo_dir, repo, f"+refs/heads/{branch}:refs/heads/{branch}", gitlab_provider)


def _await_pipeline_result(gitlab_provider, project_id, mr_iid, head_sha,
                           interval, timeout):
    """
    Waits for a pipeline to finish and reports how it ended.

    Returns (github_state, web_url, description). Phase 1 waits for the pipeline
    to appear (creation lags the push/MR); phase 2 polls it to a terminal state.
    On not-found or timeout, returns an ("error", url, msg) tuple so the caller
    can close out the gate instead of leaving it pending forever.

    When mr_iid is None (push-driven branch pipeline) the lookup is by SHA only.
    """
    deadline = time.monotonic() + timeout

    # Phase 1: locate the pipeline. For an MR, prefer its own pipelines (robust to
    # merged-results pipelines whose SHA is a synthetic merge commit); otherwise
    # (and as a fallback) look it up by commit SHA.
    pipeline = None
    while pipeline is None:
        if mr_iid is not None:
            pipeline = gitlab_provider.get_latest_mr_pipeline(project_id, mr_iid)
        if pipeline is None:
            pipeline = gitlab_provider.get_pipeline_for_sha(project_id, head_sha)
        if pipeline is None:
            if time.monotonic() >= deadline:
                return "error", None, "no GitLab pipeline was created for this PR"
            time.sleep(interval)

    pipeline_id = pipeline.get("id")
    web_url = pipeline.get("web_url")

    # Phase 2: poll to terminal.
    while True:
        status = pipeline.get("status")
        if status in _TERMINAL:
            web_url = pipeline.get("web_url", web_url)
            return _map_status(status), web_url, f"GitLab pipeline {status}"
        if time.monotonic() >= deadline:
            return "error", web_url, "GitLab pipeline did not finish in time"
        time.sleep(interval)
        pipeline = gitlab_provider.get_pipeline_status(project_id, pipeline_id)


@log_execution
def handle_pull_request(pr, storage_path, source_provider, gitlab_provider,
                        github_provider, config):
    """
    Drives one PR event end to end: push -> MR -> pending status -> poll -> final
    status. Every failure path lands on a terminal commit status so the PR gate
    never hangs on "pending".
    """
    context = config.get("ci_status_context", "holocron/gitlab-ci")
    prefix = config.get("ci_branch_prefix", "holocron/pr-")
    interval = config.get("ci_poll_interval", 10)
    timeout = config.get("ci_poll_timeout", 1800)
    allow_forks = config.get("ci_allow_forks", False)
    dry_run = config.get("dry_run", False)

    # 0. Validate every untrusted field before it reaches git, the filesystem, or
    # an API URL. Single chokepoint, mirroring sync_one_repo.
    if (not is_safe_repo_name(pr.repo_name)
            or not is_safe_repo_full_name(pr.repo_full_name)
            or not is_safe_clone_url(pr.clone_url)
            or not is_safe_git_ref(pr.base_ref)
            or not is_safe_sha(pr.head_sha)
            or pr.number <= 0):
        logger.error(f"Refusing PR #{pr.number}: unsafe field (repo={pr.repo_name!r}).")
        return

    source_branch = _source_branch(prefix, pr.number)
    repo = Repository(name=pr.repo_name, clone_url=pr.clone_url)

    def _status(state, description, target_url=None):
        if dry_run:
            logger.info(f"[DRY-RUN] Would set '{context}'={state} on {pr.head_sha[:8]} "
                        f"({description}).")
            return
        github_provider.set_commit_status(
            pr.repo_full_name, pr.head_sha, state, context,
            target_url=target_url, description=description,
        )

    # Fork gate: refs/pull/N/head exists for forks too, but running untrusted
    # code on internal runners with CI secrets is the real risk — default-deny.
    if pr.is_fork and not allow_forks:
        logger.warning(f"PR #{pr.number} head is a fork; not running CI "
                       f"(set --ci-allow-forks to override).")
        _status("error", "fork PRs are not validated by holocron CI")
        return

    # Closed PR: tear down the MR/branch; no pipeline to poll. A merged PR's base
    # changes flow through the normal mirror sync separately.
    if pr.action == "closed":
        if dry_run:
            logger.info(f"[DRY-RUN] Would close GitLab MR/branch for '{source_branch}'.")
            return
        try:
            project_id = gitlab_provider.get_project_id(repo)
            if project_id is not None:
                gitlab_provider.close_merge_request(project_id, source_branch)
                gitlab_provider.delete_branch(project_id, source_branch)
                logger.info(f"[{pr.repo_name}] Closed GitLab MR/branch for PR #{pr.number}.")
        except Exception as exc:
            logger.warning(redact(f"[{pr.repo_name}] Failed to close MR for PR "
                                  f"#{pr.number}: {exc}"))
        return

    # opened / synchronize / reopened
    repo_dir = os.path.join(storage_path, f"{repo.name}.git")

    if dry_run:
        logger.info(f"[DRY-RUN] Would push refs/pull/{pr.number}/head -> {source_branch}, "
                    f"open MR -> {pr.base_ref}, and report pipeline status to "
                    f"{pr.repo_full_name}.")
        return

    try:
        # Serialize with the mirror sync of the same repo — both touch the bare dir.
        with _get_repo_lock(repo.name):
            source_url = source_provider.get_remote_url(repo)
            _ensure_local_mirror(repo, repo_dir, source_url)

            project_id = gitlab_provider.get_project_id(repo)
            if project_id is None:
                _status("error", "GitLab project not mirrored yet")
                return

            gitlab_provider.prepare_push(repo)
            _push_pr_head(repo_dir, repo, pr.number, source_branch, gitlab_provider)
            mr = gitlab_provider.create_or_update_merge_request(
                project_id, source_branch, pr.base_ref,
                title=f"[holocron] PR #{pr.number}: {pr.head_ref}",
            )
        mr_iid = mr.get("iid")
        mr_url = mr.get("web_url")
        _status("pending", "GitLab CI running", target_url=mr_url)
    except Exception as exc:
        logger.error(redact(f"[{pr.repo_name}] PR #{pr.number} CI trigger failed: {exc}"))
        _status("error", "could not trigger GitLab CI")
        return

    # Poll for the result outside the repo lock: it can take a long time and does
    # not touch the bare mirror.
    try:
        state, web_url, description = _await_pipeline_result(
            gitlab_provider, project_id, mr_iid, pr.head_sha, interval, timeout,
        )
        _status(state, description, target_url=web_url or mr_url)
        logger.info(f"[{pr.repo_name}] PR #{pr.number} pipeline -> {state}.")
    except Exception as exc:
        logger.error(redact(f"[{pr.repo_name}] PR #{pr.number} pipeline poll failed: {exc}"))
        _status("error", "error while polling GitLab pipeline")


@log_execution
def handle_push_ci(push, storage_path, source_provider, gitlab_provider,
                   github_provider, config):
    """
    Push-driven CI: on a branch push, push the branch to GitLab (which starts a
    branch pipeline), then report that pipeline's result on the pushed commit as a
    GitHub commit status.

    No PR events are needed — the status hangs off the commit SHA, so a PR opened
    on the branch later shows the check automatically. Only branches pushed to your
    own repo reach here, so fork code never runs.
    """
    context = config.get("ci_status_context", "holocron/gitlab-ci")
    interval = config.get("ci_poll_interval", 10)
    timeout = config.get("ci_poll_timeout", 1800)
    dry_run = config.get("dry_run", False)

    # 0. Validate untrusted fields before they reach git / an API URL.
    if (not is_safe_repo_name(push.repo_name)
            or not is_safe_repo_full_name(push.repo_full_name)
            or not is_safe_clone_url(push.clone_url)
            or not is_safe_git_ref(push.branch)
            or not is_safe_sha(push.after)):
        logger.error(f"Refusing push CI: unsafe field (repo={push.repo_name!r}, "
                     f"branch={push.branch!r}).")
        return

    repo = Repository(name=push.repo_name, clone_url=push.clone_url)

    def _status(state, description, target_url=None):
        if dry_run:
            logger.info(f"[DRY-RUN] Would set '{context}'={state} on {push.after[:8]} "
                        f"({description}).")
            return
        github_provider.set_commit_status(
            push.repo_full_name, push.after, state, context,
            target_url=target_url, description=description,
        )

    repo_dir = os.path.join(storage_path, f"{repo.name}.git")

    if dry_run:
        logger.info(f"[DRY-RUN] Would push {push.branch} to GitLab and report the branch "
                    f"pipeline status for {push.after[:8]} to {push.repo_full_name}.")
        return

    try:
        with _get_repo_lock(repo.name):
            source_url = source_provider.get_remote_url(repo)
            _ensure_local_mirror(repo, repo_dir, source_url)

            project_id = gitlab_provider.get_project_id(repo)
            if project_id is None:
                _status("error", "GitLab project not mirrored yet")
                return

            gitlab_provider.prepare_push(repo)
            _push_branch(repo_dir, repo, push.branch, gitlab_provider)
        _status("pending", "GitLab CI running")
    except Exception as exc:
        logger.error(redact(f"[{push.repo_name}] push CI trigger failed on "
                            f"{push.branch}: {exc}"))
        _status("error", "could not trigger GitLab CI")
        return

    try:
        # Branch pipeline: no MR, so look the pipeline up by the pushed SHA.
        state, web_url, description = _await_pipeline_result(
            gitlab_provider, project_id, None, push.after, interval, timeout,
        )
        _status(state, description, target_url=web_url)
        logger.info(f"[{push.repo_name}] push CI on {push.branch} -> {state}.")
    except Exception as exc:
        logger.error(redact(f"[{push.repo_name}] push CI poll failed on "
                            f"{push.branch}: {exc}"))
        _status("error", "error while polling GitLab pipeline")
