"""Build the analysis-ready dataset: python -m wgs.features.build

Reads the collector's SQLite database and writes to data/processed/:
  repos.parquet            one row per repo: metadata, activity, README and growth features
  repo_topics.parquet      (id, topic) pairs
  star_monthly.parquet     cumulative stars per repo per month (reliable histories only)
  population.parquet       census: number of repos per star bucket x year x language group
  readme_benchmarks.json   README feature percentiles, used by the dashboard's README checker
  build_report.json        validation summary

README text itself is never written out: it belongs to each repo's authors.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from wgs.config import DEFAULT_CONFIG_PATH, Config, load_config
from wgs.features.growth import monthly_curves, star_growth
from wgs.features.metadata import metadata_features
from wgs.features.readme import extract_readme_features

BUCKET_ORDER = ["10-100", "100-1k", "1k-10k", "10k+"]

REPO_QUERY = """
SELECT
    r.id,
    COALESCE(json_extract(d.raw_json, '$.full_name'), r.full_name) AS full_name,
    r.bucket,
    r.lang_group,
    COALESCE(json_extract(d.raw_json, '$.language'), r.language) AS language,
    r.created_at,
    d.pushed_at,
    d.fetched_at AS observed_at,
    r.stars AS stars_at_discovery,
    d.stars,
    d.forks,
    d.subscribers AS watchers,
    d.open_issues,
    json_extract(d.raw_json, '$.size') AS size_kb,
    d.license,
    d.topics,
    d.archived,
    json_extract(d.raw_json, '$.homepage') AS homepage,
    json_extract(d.raw_json, '$.description') AS description,
    m.status AS readme_status,
    m.path AS readme_path,
    m.content AS readme,
    a.status AS commit_stats_status,
    a.weeks_json
FROM repos r
JOIN repo_details d ON d.repo_id = r.id AND d.status = 'ok'
LEFT JOIN readmes m ON m.repo_id = r.id
LEFT JOIN commit_activity a ON a.repo_id = r.id
ORDER BY r.id
"""

README_NUMERIC = [
    "readme_words",
    "readme_headings",
    "readme_max_depth",
    "readme_images",
    "readme_gifs",
    "readme_badges",
    "readme_links",
    "readme_code_blocks",
    "readme_tables",
]
README_FLAGS = [
    "has_install",
    "has_usage",
    "has_demo_section",
    "has_demo_link",
    "has_contributing",
    "has_license_section",
]


def load_raw(db_path: Path) -> dict[str, pd.DataFrame]:
    with sqlite3.connect(db_path) as conn:
        return {
            "repos": pd.read_sql(REPO_QUERY, conn),
            "daily": pd.read_sql("SELECT repo_id, day, stars FROM star_daily", conn),
            "history": pd.read_sql("SELECT repo_id AS id, status AS history_status FROM star_history", conn),
            "strata": pd.read_sql(
                "SELECT bucket, year, lang_group, total_count FROM strata ORDER BY bucket, year, lang_group", conn
            ),
            "meta": pd.read_sql("SELECT key, value FROM meta", conn),
        }


def readme_features(raw: pd.DataFrame) -> pd.DataFrame:
    rows = [
        extract_readme_features(text if status == "ok" else None, path)
        for text, path, status in zip(raw["readme"], raw["readme_path"], raw["readme_status"])
    ]
    return pd.DataFrame(rows, index=raw.index)


def build_repos(raw: dict[str, pd.DataFrame], cfg: Config, snapshot: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    r = raw["repos"]
    base_cols = [
        "id", "full_name", "bucket", "lang_group", "language", "created_at", "pushed_at", "observed_at",
        "stars_at_discovery", "stars", "forks", "watchers", "open_issues", "size_kb", "license",
        "archived", "homepage", "description", "commit_stats_status",
    ]
    repos = r[base_cols].copy()
    repos["language"] = repos["language"].fillna("None")
    repos["archived"] = repos["archived"].fillna(0).astype(bool)
    repos["topics"] = r["topics"].apply(lambda t: json.loads(t) if isinstance(t, str) else [])
    repos = repos.join(metadata_features(r, cfg.features.inactive_days))
    repos = repos.join(readme_features(r))

    growth = star_growth(
        raw["daily"], r[["id", "stars", "created_at"]], cfg.features.milestones,
        cfg.features.coverage_range, snapshot,
    )
    repos = repos.merge(raw["history"], on="id", how="left").merge(growth, on="id", how="left")
    repos["history_reliable"] = repos["history_reliable"].astype("boolean").fillna(False).astype(bool)
    for col in ("created_at", "pushed_at", "observed_at"):
        repos[col] = pd.to_datetime(repos[col], utc=True)
    repos["bucket"] = pd.Categorical(repos["bucket"], categories=BUCKET_ORDER, ordered=True)

    monthly = monthly_curves(raw["daily"], r[["id", "stars", "created_at"]], growth)
    return repos, monthly


def readme_benchmarks(repos: pd.DataFrame, popular_buckets: tuple[str, ...], snapshot: str) -> dict:
    """Percentiles of README features per group. List repos and repos without a README are left
    out: a curated list of 2,000 links says nothing about how to write a project README."""
    eligible = repos[(repos["readme_format"] != "none") & ~repos["is_list_repo"]]
    groups = {"popular": eligible[eligible["bucket"].isin(popular_buckets)], "all": eligible}
    groups.update({str(b): eligible[eligible["bucket"] == b] for b in BUCKET_ORDER})

    def describe(g: pd.DataFrame) -> dict:
        return {
            "n": int(len(g)),
            "numeric": {
                col: {
                    **{f"p{q}": float(g[col].quantile(q / 100)) for q in (10, 25, 50, 75, 90)},
                    "mean": float(g[col].mean()),
                }
                for col in README_NUMERIC
            },
            "flags": {col: float(g[col].mean()) for col in README_FLAGS},
        }

    return {
        "snapshot": snapshot,
        "popular_definition": f"buckets {', '.join(popular_buckets)} (1,000+ stars)",
        "excluded": "repos without a README and list/awesome repos",
        "groups": {name: describe(g) for name, g in groups.items()},
    }


def validation_report(repos: pd.DataFrame, monthly: pd.DataFrame, population: pd.DataFrame) -> dict:
    bucket_ranges = {"10-100": (10, 99), "100-1k": (100, 999), "1k-10k": (1000, 9999), "10k+": (10000, np.inf)}
    drifted = sum(
        not (lo <= s <= hi) for s, b in zip(repos["stars"], repos["bucket"]) for lo, hi in [bucket_ranges[b]]
    )
    nulls = repos.isna().mean()
    report = {
        "rows": int(len(repos)),
        "rows_per_bucket": {str(k): int(v) for k, v in repos["bucket"].value_counts().sort_index().items()},
        "duplicate_ids": int(repos["id"].duplicated().sum()),
        "negative_age": int((repos["age_days"] < 0).sum()),
        "negative_days_since_push": int((repos["days_since_push"] < -1).sum()),
        "stars_drifted_out_of_bucket": int(drifted),
        "readme_missing": int((repos["readme_format"] == "none").sum()),
        "commit_stats_missing": int(repos["commits_52w"].isna().sum()),
        "list_repos": int(repos["is_list_repo"].sum()),
        "inactive_share": round(float(repos["is_inactive"].mean()), 3),
        "star_history": {
            "repos": int(repos["coverage"].notna().sum()),
            "reliable": int(repos["history_reliable"].sum()),
            "median_coverage": round(float(repos["coverage"].median()), 3),
            "monthly_rows": int(len(monthly)),
        },
        "population_strata": int(len(population)),
        "columns_with_nulls": {k: round(float(v), 3) for k, v in nulls[nulls > 0].sort_values(ascending=False).items()},
    }
    problems = [k for k in ("duplicate_ids", "negative_age", "negative_days_since_push") if report[k]]
    report["ok"] = not problems
    report["problems"] = problems
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m wgs.features.build", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="SQLite database (default: paths.db in config.yaml)")
    parser.add_argument("--out", help="output directory (default: features.processed_dir)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    db_path = Path(args.db) if args.db else cfg.db_path
    out = Path(args.out) if args.out else cfg.features.processed_dir
    out.mkdir(parents=True, exist_ok=True)

    print(f"Reading {db_path}")
    raw = load_raw(db_path)
    meta = dict(zip(raw["meta"]["key"], raw["meta"]["value"]))
    snapshot = pd.Timestamp(meta.get("snapshot_started") or datetime.now(timezone.utc).isoformat())

    print(f"Building features for {len(raw['repos'])} repos...")
    repos, monthly = build_repos(raw, cfg, snapshot)
    topics = repos[["id", "topics"]].explode("topics").dropna().rename(columns={"topics": "topic"})
    population = raw["strata"].rename(columns={"total_count": "population"})

    repos.to_parquet(out / "repos.parquet", index=False)
    topics.to_parquet(out / "repo_topics.parquet", index=False)
    monthly.to_parquet(out / "star_monthly.parquet", index=False)
    population.to_parquet(out / "population.parquet", index=False)

    snapshot_str = snapshot.date().isoformat()
    benchmarks = readme_benchmarks(repos, cfg.features.popular_buckets, snapshot_str)
    (out / "readme_benchmarks.json").write_text(json.dumps(benchmarks, indent=2), encoding="utf-8")

    report = {"snapshot": snapshot_str, **validation_report(repos, monthly, population)}
    (out / "build_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps({k: v for k, v in report.items() if k != "columns_with_nulls"}, indent=2))
    for path in sorted(out.glob("*.*")):
        if path.suffix in (".parquet", ".json"):
            print(f"  {path.name:<24} {path.stat().st_size / 1024:>8.0f} KB")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
