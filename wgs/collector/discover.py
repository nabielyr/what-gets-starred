"""Discovery stage: find repositories with the Search API and record population counts."""

from __future__ import annotations

import logging
import random
from datetime import date

from tqdm import tqdm

from wgs.collector.client import GitHubClient
from wgs.collector.db import Database
from wgs.collector.sampling import (
    PER_PAGE,
    SEARCH_MAX_RESULTS,
    Stratum,
    allocate_quotas,
    build_strata,
    page_count,
    random_window,
    redistribute,
    stable_rng,
)
from wgs.config import Config

log = logging.getLogger(__name__)


def search_page(client: GitHubClient, db: Database, query: str, page: int) -> dict:
    """One page of search results, served from the cache when already fetched."""
    cached = db.get_search_page(query, page)
    if cached is not None:
        return cached
    resp = client.get(
        "/search/repositories",
        params={"q": query, "per_page": PER_PAGE, "page": page},
        resource="search",
    )
    if resp.status_code == 422:
        # Invalid query or a page beyond the 1,000-result window: treat as empty.
        log.warning("Search rejected query %r page %d: %s", query, page, resp.text[:200])
        payload = {"total_count": 0, "incomplete_results": False, "items": []}
    else:
        resp.raise_for_status()
        payload = resp.json()
    db.save_search_page(query, page, payload)
    return payload


def init_strata(db: Database, cfg: Config) -> list[Stratum]:
    """Create the strata rows and (re)allocate quotas when the target changes."""
    strata = build_strata(cfg.sampling)
    for s in strata:
        db.upsert_stratum(s.key, s.bucket.name, s.year, s.group.name)
    db.conn.commit()

    target = cfg.sampling.target_per_bucket
    if db.get_meta("target_per_bucket") != str(target):
        for bucket in cfg.sampling.buckets:
            keys = [s.key for s in strata if s.bucket == bucket]
            for key, quota in allocate_quotas(keys, target, cfg.seed, bucket.name).items():
                db.conn.execute(
                    "UPDATE strata SET quota = ?, "
                    "status = CASE WHEN status = 'exhausted' THEN status ELSE 'pending' END "
                    "WHERE key = ?",
                    (quota, key),
                )
        db.conn.commit()
        db.set_meta("target_per_bucket", str(target))
        log.info("Allocated %d repos per bucket over %d strata", target, len(strata))
    return strata


def run_census(client: GitHubClient, db: Database, cfg: Config) -> None:
    """Record the total number of repos in every stratum (one search request each)."""
    strata = init_strata(db, cfg)
    todo = [s for s in strata if db.strata("key = ?", (s.key,))[0]["total_count"] is None]
    log.info("Census: %d of %d strata still need a population count", len(todo), len(strata))
    for s in tqdm(todo, desc="census", unit="stratum"):
        payload = search_page(client, db, s.query(), 1)
        db.update_stratum(s.key, total_count=payload["total_count"])


def _draw_window(
    client: GitHubClient, db: Database, stratum: Stratum, total: int, rng: random.Random
) -> list[dict]:
    """Search results from a random page of a random date window inside the stratum."""
    start, end = date(stratum.year, 1, 1), date(stratum.year, 12, 31)
    if total > SEARCH_MAX_RESULTS:
        # Too many results to reach them all: narrow to a random month, then a random day.
        start, end = random_window(stratum.year, rng, "month")
        total = search_page(client, db, stratum.query(start, end), 1)["total_count"]
        if total > SEARCH_MAX_RESULTS:
            day = date(stratum.year, start.month, rng.randint(1, end.day))
            start = end = day
            total = search_page(client, db, stratum.query(start, end), 1)["total_count"]
    if total == 0:
        return []
    page = rng.randint(1, page_count(total))
    return search_page(client, db, stratum.query(start, end), page)["items"]


def sample_stratum(client: GitHubClient, db: Database, cfg: Config, stratum: Stratum) -> int:
    """Draw repos until the stratum's quota is met. Returns the number of new repos."""
    row = db.strata("key = ?", (stratum.key,))[0]
    quota, attempts, total = row["quota"], row["attempts"], row["total_count"]
    if total is None:
        total = search_page(client, db, stratum.query(), 1)["total_count"]
        db.update_stratum(stratum.key, total_count=total)

    sampled = db.sampled_count(stratum.key)
    max_attempts = attempts + 4 * max(quota - sampled, 0) + 5
    added = 0

    while sampled < quota:
        if sampled >= total or attempts >= max_attempts:
            break
        rng = stable_rng(cfg.seed, stratum.key, attempts)
        items = _draw_window(client, db, stratum, total, rng)
        attempts += 1
        fresh = [it for it in items if not db.has_repo(it["id"])]
        take = min(len(fresh), cfg.sampling.max_per_window, quota - sampled)
        for item in rng.sample(fresh, take):
            if db.insert_repo(item, stratum.key, stratum.bucket.name, stratum.group.name):
                sampled += 1
                added += 1
        db.update_stratum(stratum.key, attempts=attempts)

    status = "done" if sampled >= quota else "exhausted"
    db.update_stratum(stratum.key, status=status)
    return added


def run_discover(client: GitHubClient, db: Database, cfg: Config) -> None:
    strata = init_strata(db, cfg)
    by_key = {s.key: s for s in strata}
    target = cfg.sampling.target_per_bucket

    for round_ in range(cfg.sampling.redistribute_rounds + 1):
        todo = [
            by_key[r["key"]]
            for r in db.strata("status = 'pending' AND quota > 0")
            if r["key"] in by_key
        ]
        if todo:
            log.info("Discovery round %d: %d strata to sample", round_ + 1, len(todo))
            for s in tqdm(todo, desc=f"discover r{round_ + 1}", unit="stratum"):
                sample_stratum(client, db, cfg, s)

        # Move quota that exhausted strata could not use to strata that still have repos left.
        moved = False
        for bucket in cfg.sampling.buckets:
            deficit = target - db.bucket_count(bucket.name)
            if deficit <= 0:
                continue
            capacity = {}
            for r in db.strata("bucket = ? AND status != 'exhausted'", (bucket.name,)):
                sampled = db.sampled_count(r["key"])
                if r["total_count"] is None:
                    capacity[r["key"]] = cfg.sampling.max_per_window  # unknown, probe a little
                else:
                    capacity[r["key"]] = max(min(r["total_count"], SEARCH_MAX_RESULTS) - sampled, 0)
            extra = redistribute(deficit, capacity, cfg.seed, f"{bucket.name}|{round_}")
            for key, n in extra.items():
                db.conn.execute(
                    "UPDATE strata SET quota = quota + ?, status = 'pending' WHERE key = ?", (n, key)
                )
                moved = True
            db.conn.commit()
            if extra:
                log.info("Bucket %s short by %d, redistributed to %d strata", bucket.name, deficit, len(extra))
        if not moved:
            break

    for bucket in cfg.sampling.buckets:
        log.info("Bucket %-7s: %d / %d repos", bucket.name, db.bucket_count(bucket.name), target)
