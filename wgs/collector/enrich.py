"""Enrichment stage: repo details, README text and weekly commit counts for each repo."""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime

from tqdm import tqdm

from wgs.collector.client import GitHubAPIError, GitHubClient
from wgs.collector.db import Database
from wgs.config import Config

log = logging.getLogger(__name__)


def _blocked_or_error(status_code: int | None) -> str:
    return "blocked" if status_code in (403, 451) else "error"


def _get(client: GitHubClient, path: str):
    """GET that turns "failed after all retries" into None, so one bad repo never stops the run."""
    try:
        return client.get(path)
    except GitHubAPIError as exc:
        log.warning("Giving up on %s for now: %s", path, exc)
        return None


def fetch_details(client: GitHubClient, db: Database, repo_id: int, full_name: str) -> str:
    resp = _get(client, f"/repos/{full_name}")
    code = resp.status_code if resp is not None else None
    if code == 200:
        status, data = "ok", resp.json()
    elif code == 404:
        status, data = "not_found", None
    else:
        status, data = _blocked_or_error(code), None
    db.save_details(repo_id, status, code, data)
    return status


def fetch_readme(client: GitHubClient, db: Database, repo_id: int, full_name: str) -> str:
    resp = _get(client, f"/repos/{full_name}/readme")
    code = resp.status_code if resp is not None else None
    if code == 200:
        data = resp.json()
        raw = base64.b64decode(data.get("content") or "") if data.get("encoding") == "base64" else b""
        content = raw.decode("utf-8", errors="replace")
        db.save_readme(repo_id, "ok", 200, data.get("path"), data.get("size"), content)
        return "ok"
    status = "missing" if code == 404 else _blocked_or_error(code)
    db.save_readme(repo_id, status, code)
    return status


def fetch_commit_activity(
    client: GitHubClient, db: Database, cfg: Config, repo_id: int, full_name: str
) -> str:
    """Last 52 weeks of commit counts.

    GitHub computes repository statistics lazily: the first call often returns
    202 Accepted while a background job runs, so we record "pending" and poll again later.
    """
    resp = _get(client, f"/repos/{full_name}/stats/commit_activity")
    last_try = db.commit_activity_attempts(repo_id) + 1 >= cfg.enrich.stats_max_attempts
    weeks = None
    code = resp.status_code if resp is not None else None
    if code == 200:
        weeks = resp.json() or []
        status = "ok" if weeks else "empty"
    elif code == 202:
        status = "unavailable" if last_try else "pending"
    elif code == 204:
        status = "empty"  # repository has no commits
    elif code == 404:
        status = "not_found"
    elif code == 422:
        status = "unavailable"  # statistics not available for this repository
    else:
        status = _blocked_or_error(code)
        if status == "error" and last_try:
            status = "unavailable"
    db.save_commit_activity(repo_id, status, code, weeks if status == "ok" else None)
    return status


def run_enrich(client: GitHubClient, db: Database, cfg: Config, limit: int | None = None) -> None:
    needs = {
        table: {r["id"]: r["full_name"] for r in db.repos_needing(table)}
        for table in ("repo_details", "readmes", "commit_activity")
    }
    names: dict[int, str] = {}
    for todo in needs.values():
        names.update(todo)
    repo_ids = sorted(names)[:limit] if limit else sorted(names)
    log.info(
        "Enrich: %d repos need work (details %d, readme %d, commit stats %d)",
        len(repo_ids),
        len(needs["repo_details"]),
        len(needs["readmes"]),
        len(needs["commit_activity"]),
    )

    for repo_id in tqdm(repo_ids, desc="enrich", unit="repo"):
        name = names[repo_id]
        if repo_id in needs["repo_details"]:
            if fetch_details(client, db, repo_id, name) == "not_found":
                # Deleted or made private since discovery: nothing else to fetch.
                db.save_readme(repo_id, "missing", 404)
                db.save_commit_activity(repo_id, "not_found", 404)
                continue
        if repo_id in needs["readmes"]:
            fetch_readme(client, db, repo_id, name)
        if repo_id in needs["commit_activity"]:
            fetch_commit_activity(client, db, cfg, repo_id, name)

    poll_pending_stats(client, db, cfg)


def poll_pending_stats(client: GitHubClient, db: Database, cfg: Config) -> None:
    """Re-request commit stats that came back 202 until they are ready or we give up."""
    wait = cfg.enrich.stats_retry_wait
    while pending := db.pending_commit_activity():
        log.info("Commit stats: %d repos still computing on GitHub's side, polling again", len(pending))
        for row in tqdm(pending, desc="commit stats retry", unit="repo"):
            age = time.time() - datetime.fromisoformat(row["fetched_at"]).timestamp()
            if age < wait:
                time.sleep(wait - age)
            fetch_commit_activity(client, db, cfg, row["id"], row["full_name"])
