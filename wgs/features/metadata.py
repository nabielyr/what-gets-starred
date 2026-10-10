"""Metadata features: age, activity, licence, topics and repo type."""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

# Licences common enough to keep as their own group; everything else becomes "Other".
LICENSE_GROUPS = {
    "MIT": "MIT",
    "Apache-2.0": "Apache-2.0",
    "GPL-3.0": "GPL-3.0",
    "GPL-2.0": "GPL-2.0",
    "AGPL-3.0": "AGPL-3.0",
    "LGPL-3.0": "LGPL",
    "LGPL-2.1": "LGPL",
    "BSD-3-Clause": "BSD",
    "BSD-2-Clause": "BSD",
    "MPL-2.0": "MPL-2.0",
    "Unlicense": "Public domain",
    "CC0-1.0": "Public domain",
    "0BSD": "Public domain",
}

LIST_REPO_PATTERN = re.compile(
    r"\b(awesome|curated|resources|cheat ?sheets?|roadmaps?|interviews?|free programming books)\b",
    re.IGNORECASE,
)
LIST_DESCRIPTION_PATTERN = re.compile(
    r"\b((?<!font )awesome|curated list|list of (awesome|useful|free)|collection of (resources|links|tools))\b",
    re.IGNORECASE,
)


def license_group(spdx: str | None) -> str:
    if spdx is None or (isinstance(spdx, float) and np.isnan(spdx)):
        return "None"
    if spdx == "NOASSERTION":
        return "Custom"  # a licence file GitHub could not identify
    return LICENSE_GROUPS.get(spdx, "Other")


def commit_features(weeks_json: str | None) -> dict:
    """Activity from the 52-week commit counts (oldest week first)."""
    if not isinstance(weeks_json, str):
        return {
            "commits_52w": np.nan,
            "commits_per_month": np.nan,
            "active_weeks_52": np.nan,
            "weeks_since_last_commit": np.nan,
        }
    totals = [w["total"] for w in json.loads(weeks_json)]
    active = [i for i, n in enumerate(totals) if n > 0]
    commits = sum(totals)
    return {
        "commits_52w": commits,
        "commits_per_month": commits / 12,
        "active_weeks_52": len(active),
        # NaN when there was no commit at all in the window (i.e. "more than 52 weeks ago").
        "weeks_since_last_commit": len(totals) - 1 - active[-1] if active else np.nan,
    }


def is_list_repo(name: str, description: str | None, topics: list[str]) -> bool:
    """Awesome lists, resource collections, roadmaps... repos that are mostly links."""
    repo = name.split("/")[-1].replace("-", " ").replace("_", " ")
    text = f"{repo} {' '.join(topics)}"
    if LIST_REPO_PATTERN.search(text):
        return True
    return isinstance(description, str) and bool(LIST_DESCRIPTION_PATTERN.search(description))


def metadata_features(df: pd.DataFrame, inactive_days: int) -> pd.DataFrame:
    """Expects one row per repo with raw columns from the collector (see build.py)."""
    out = pd.DataFrame(index=df.index)
    observed = pd.to_datetime(df["observed_at"], utc=True)
    created = pd.to_datetime(df["created_at"], utc=True)
    pushed = pd.to_datetime(df["pushed_at"], utc=True)

    out["created_year"] = created.dt.year
    out["age_days"] = (observed - created).dt.total_seconds() / 86400
    out["days_since_push"] = (observed - pushed).dt.total_seconds() / 86400
    out["stars_per_day"] = df["stars"] / out["age_days"].clip(lower=1)
    out["log_stars"] = np.log10(df["stars"].clip(lower=1))

    # A repo GitHub reports as empty has zero commits; "unavailable" stays unknown (NaN).
    weeks = df["weeks_json"].where(df.get("commit_stats_status", pd.Series(index=df.index)) != "empty", "[]")
    activity = pd.DataFrame([commit_features(w) for w in weeks], index=df.index)
    out = out.join(activity)

    topics = df["topics"].apply(lambda t: json.loads(t) if isinstance(t, str) else [])
    out["n_topics"] = topics.apply(len)
    out["license_group"] = df["license"].apply(license_group)
    out["has_license"] = out["license_group"] != "None"
    out["has_homepage"] = df["homepage"].fillna("").str.strip().ne("")
    out["description_words"] = df["description"].fillna("").str.split().apply(len)
    out["is_list_repo"] = [
        is_list_repo(n, d, t) for n, d, t in zip(df["full_name"], df["description"], topics)
    ]

    archived = df["archived"].fillna(0).astype(bool)
    stale_push = out["days_since_push"] > inactive_days
    no_commits = out["commits_52w"].eq(0)
    out["is_inactive"] = archived | stale_push | no_commits
    out["inactive_reason"] = np.select(
        [archived, stale_push, no_commits],
        ["archived", "no push in a year", "no commits in a year"],
        default="active",
    )
    return out
