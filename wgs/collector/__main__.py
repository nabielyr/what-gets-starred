"""Command line entry point: python -m wgs.collector <command>.

Commands
  run           census (full runs only) -> discover -> enrich -> star-history
  census        population count for every stratum
  discover      sample repositories per stratum
  enrich        repo details, README and commit stats for discovered repos
  star-history  daily star counts from GH Archive for repos with 100+ stars
  status        progress report and remaining API quota

Use --limit N for a small test run; it writes to data/raw/sample.db unless --db is given.
Every command can be interrupted with Ctrl+C and resumed by running it again.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from tqdm.contrib.logging import logging_redirect_tqdm

from wgs.collector.client import GitHubClient
from wgs.collector.db import Database, utcnow
from wgs.collector.discover import run_census, run_discover
from wgs.collector.enrich import run_enrich
from wgs.collector.star_history import run_star_history
from wgs.config import DEFAULT_CONFIG_PATH, ROOT, Config, get_github_token, load_config

log = logging.getLogger("wgs.collector")

SAMPLE_DB = ROOT / "data" / "raw" / "sample.db"


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def resolve_config(args: argparse.Namespace) -> Config:
    cfg = load_config(args.config)
    if args.limit:
        cfg = cfg.with_target(math.ceil(args.limit / len(cfg.sampling.buckets)))
        cfg = cfg.with_db(SAMPLE_DB)
    if args.db:
        cfg = cfg.with_db(Path(args.db).resolve())
    return cfg


def print_status(db: Database, client: GitHubClient | None, cfg: Config) -> None:
    print(f"Database: {cfg.db_path}")
    print(f"Snapshot started: {db.get_meta('snapshot_started') or '-'}")
    target = db.get_meta("target_per_bucket") or "-"
    print(f"\nRepos per bucket (target {target}):")
    total = 0
    for row in db.bucket_summary():
        total += row["n"]
        print(f"  {row['bucket']:<8} {row['n']:>6}   stars {row['min_stars']}..{row['max_stars']}")
    print(f"  {'total':<8} {total:>6}")

    strata = db.strata()
    by_status: dict[str, int] = {}
    for r in strata:
        if r["quota"] > 0:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    with_census = sum(r["total_count"] is not None for r in strata)
    summary = ", ".join(f"{k} {v}" for k, v in sorted(by_status.items()))
    print(f"\nStrata: {len(strata)}, {sum(by_status.values())} with a quota ({summary})")
    print(f"Census counts recorded: {with_census}/{len(strata)}")

    print("\nEnrichment stages:")
    for table, counts in db.stage_summary().items():
        done = sum(counts.values())
        detail = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "-"
        pct = f"{100 * done / total:.0f}%" if total else "-"
        print(f"  {table:<16} {done:>6}/{total:<6} {pct:>4}   ({detail})")

    history = db.star_history_summary()
    eligible = len(db.repos_needing_star_history(cfg.star_history.buckets)) + sum(history.values())
    detail = ", ".join(f"{k} {v}" for k, v in sorted(history.items())) or "-"
    print(f"  {'star_history':<16} {sum(history.values()):>6}/{eligible:<6}        ({detail})")

    if client is not None:
        try:
            res = client.rate_limit_status()
            for name in ("core", "search"):
                r = res[name]
                reset = datetime.fromtimestamp(r["reset"], timezone.utc).astimezone()
                print(f"\nAPI quota {name:<6}: {r['remaining']}/{r['limit']} (resets {reset:%H:%M:%S})", end="")
            print()
        except Exception as exc:  # status must never crash on a network hiccup
            print(f"\nAPI quota: unavailable ({exc})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m wgs.collector", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "command", choices=["run", "census", "discover", "enrich", "star-history", "status"]
    )
    parser.add_argument("--limit", type=int, help="small test run with about N repos (uses data/raw/sample.db)")
    parser.add_argument("--db", help="SQLite database path (overrides config.yaml)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="path to config.yaml")
    parser.add_argument("--census", action="store_true", help="also run the census with --limit")
    args = parser.parse_args(argv)

    cfg = resolve_config(args)
    setup_logging(cfg.log_path)
    db = Database(cfg.db_path)

    if args.command == "status":
        client = GitHubClient(get_github_token(), cfg.github)
        print_status(db, client, cfg)
        return 0

    client = GitHubClient(get_github_token(), cfg.github)
    if db.get_meta("snapshot_started") is None:
        db.set_meta("snapshot_started", utcnow())
    log.info("Using database %s", cfg.db_path)

    try:
        with logging_redirect_tqdm():
            if args.command in ("run", "census") and (args.command == "census" or not args.limit or args.census):
                run_census(client, db, cfg)
            if args.command in ("run", "discover"):
                run_discover(client, db, cfg)
            if args.command in ("run", "enrich"):
                run_enrich(client, db, cfg)
            if args.command in ("run", "star-history"):
                run_star_history(db, cfg)
    except KeyboardInterrupt:
        log.warning("Interrupted. Progress is saved; run the same command again to resume.")
        return 130
    finally:
        log.info("API requests made this session: %d", client.request_count)
        db.set_meta("last_run_finished", utcnow())
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
