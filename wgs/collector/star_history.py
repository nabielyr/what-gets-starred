"""Star history stage: daily star counts per repo from GH Archive.

GitHub restricted its stargazer listing endpoints to repository admins and collaborators in
July 2026, so star timestamps come from GH Archive (https://www.gharchive.org/) instead. It is
a public record of GitHub events since 2011, where each star is a "WatchEvent". The archive is
queried through ClickHouse's free public playground, which hosts it as the `github_events`
table, so no download and no GitHub quota is needed.

Known limitations, measured later in feature engineering:
- the archive misses some events, so event counts are usually below the real star count;
- unstars are not recorded;
- events are filed under the repo name at the time, so history before a rename is lost
  unless the old name is known (we query both the discovered and the current name).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Callable

import requests
from tqdm import tqdm

from wgs.collector.db import Database
from wgs.config import Config, StarHistorySettings

log = logging.getLogger(__name__)


class ArchiveError(RuntimeError):
    """Raised when the archive keeps failing or rejects a query."""


class ArchiveClient:
    def __init__(
        self,
        settings: StarHistorySettings,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.session = session or requests.Session()
        self._sleep = sleep

    def query(self, sql: str) -> str:
        last_error = ""
        for attempt in range(self.settings.max_retries + 1):
            try:
                resp = self.session.post(
                    self.settings.url,
                    params={"user": self.settings.user},
                    data=sql.encode(),
                    timeout=self.settings.timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    return resp.text
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code < 500 and resp.status_code != 429:
                    raise ArchiveError(last_error)  # a bad query will not get better
            if attempt < self.settings.max_retries:
                wait = min(2 ** (attempt + 2), 120)
                log.warning("Archive query failed (%s), retry %d in %ds", last_error, attempt + 1, wait)
                self._sleep(wait)
        raise ArchiveError(f"Archive query failed after {self.settings.max_retries} retries: {last_error}")


def _quote(name: str) -> str:
    return "'" + name.replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_query(names: list[str], before: str | None = None) -> str:
    """Daily WatchEvent counts for the given repo names (case-insensitive)."""
    in_list = ", ".join(_quote(n) for n in sorted({n.lower() for n in names}))
    cutoff = f"AND created_at < toDateTime({_quote(before)}) " if before else ""
    return (
        "SELECT lower(repo_name) AS name, toDate(created_at) AS day, count() AS stars "
        "FROM github_events "
        f"WHERE event_type = 'WatchEvent' AND lower(repo_name) IN ({in_list}) {cutoff}"
        "GROUP BY name, day ORDER BY name, day FORMAT TSV"
    )


def parse_tsv(text: str) -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for line in text.splitlines():
        if not line.strip():
            continue
        name, day, stars = line.split("\t")
        out[name].append((day, int(stars)))
    return out


def _merge(series: list[list[tuple[str, int]]]) -> list[tuple[str, int]]:
    totals: dict[str, int] = defaultdict(int)
    for s in series:
        for day, n in s:
            totals[day] += n
    return sorted(totals.items())


def snapshot_cutoff(db: Database) -> str | None:
    """Only count events before the snapshot, so they line up with the star counts we stored."""
    started = db.get_meta("snapshot_started")
    return started[:19].replace("T", " ") if started else None


def run_star_history(db: Database, cfg: Config, archive: ArchiveClient | None = None) -> None:
    settings = cfg.star_history
    archive = archive or ArchiveClient(settings)
    todo = db.repos_needing_star_history(settings.buckets)
    cutoff = snapshot_cutoff(db)
    log.info("Star history: %d repos to fetch from GH Archive (events before %s)", len(todo), cutoff)

    batches = [todo[i : i + settings.batch_size] for i in range(0, len(todo), settings.batch_size)]
    for batch in tqdm(batches, desc="star history", unit="batch"):
        names_by_repo = {
            r["id"]: {r["full_name"].lower(), r["current_name"].lower()} for r in batch
        }
        all_names = [n for names in names_by_repo.values() for n in names]
        rows = parse_tsv(archive.query(build_query(all_names, cutoff)))
        for repo_id, names in names_by_repo.items():
            db.save_star_history(repo_id, _merge([rows.get(n, []) for n in names]))

    summary = db.star_history_summary()
    log.info("Star history done: %s", ", ".join(f"{k} {v}" for k, v in sorted(summary.items())))
