import json

import numpy as np
import pandas as pd
import pytest

from wgs.collector.db import Database
from wgs.features import build
from wgs.features.growth import monthly_curves, star_growth
from wgs.features.metadata import commit_features, is_list_repo, license_group, metadata_features


def weeks(totals):
    return json.dumps([{"week": 1_700_000_000 + i * 604800, "total": t} for i, t in enumerate(totals)])


# ---------------------------------------------------------------- metadata
def test_license_group():
    assert license_group("MIT") == "MIT"
    assert license_group("BSD-2-Clause") == license_group("BSD-3-Clause") == "BSD"
    assert license_group("NOASSERTION") == "Custom"
    assert license_group("WTFPL") == "Other"
    assert license_group(None) == license_group(np.nan) == "None"


def test_commit_features():
    f = commit_features(weeks([0] * 40 + [3, 0, 5] + [0] * 9))
    assert f["commits_52w"] == 8 and f["active_weeks_52"] == 2
    assert f["weeks_since_last_commit"] == 9
    assert f["commits_per_month"] == pytest.approx(8 / 12)
    none = commit_features(weeks([0] * 52))
    assert none["commits_52w"] == 0 and np.isnan(none["weeks_since_last_commit"])
    assert np.isnan(commit_features(None)["commits_52w"])


def test_is_list_repo():
    assert is_list_repo("sindresorhus/awesome", None, [])
    assert is_list_repo("o/frontend-resources", None, [])
    assert is_list_repo("o/x", "A curated list of Rust crates", [])
    assert not is_list_repo("o/react-list", "A list component for React", [])
    assert not is_list_repo("o/fontawesome-module", "Font Awesome icons for Nuxt", [])


def test_metadata_features_activity_rules():
    df = pd.DataFrame(
        {
            "full_name": ["o/a", "o/b", "o/c", "o/d"],
            "observed_at": ["2026-10-09T00:00:00+00:00"] * 4,
            "created_at": ["2020-10-09T00:00:00Z"] * 4,
            "pushed_at": ["2026-10-01T00:00:00Z", "2024-01-01T00:00:00Z", "2026-10-01T00:00:00Z",
                          "2026-10-01T00:00:00Z"],
            "stars": [100, 50, 10, 5],
            "archived": [0, 0, 1, 0],
            "weeks_json": [weeks([1] * 52), weeks([0] * 52), weeks([1] * 52), None],
            "commit_stats_status": ["ok", "ok", "ok", "empty"],
            "topics": ['["cli"]', "[]", "[]", "[]"],
            "license": ["MIT", None, "NOASSERTION", "MIT"],
            "homepage": ["https://a.dev", "", None, None],
            "description": ["A tool", None, "x", "y"],
        }
    )
    m = metadata_features(df, inactive_days=365)
    assert m["age_days"].iloc[0] == pytest.approx(2191, abs=1)
    assert m["stars_per_day"].iloc[0] == pytest.approx(100 / 2191, rel=1e-3)
    assert m["inactive_reason"].tolist() == ["active", "no push in a year", "archived", "no commits in a year"]
    assert m["commits_52w"].tolist() == [52, 0, 52, 0]  # "empty" repo counts as zero commits
    assert m["n_topics"].tolist() == [1, 0, 0, 0]
    assert m["has_homepage"].tolist() == [True, False, False, False]
    assert m["license_group"].tolist() == ["MIT", "None", "Custom", "MIT"]


# ---------------------------------------------------------------- growth
REPOS = pd.DataFrame(
    {"id": [1, 2], "stars": [200, 100], "created_at": ["2020-01-01T10:00:00Z", "2020-01-01T10:00:00Z"]}
)


def test_star_growth_corrects_for_coverage_and_name_reuse():
    daily = pd.DataFrame(
        {
            "repo_id": [1, 1, 1, 1, 2],
            "day": ["2019-06-01", "2020-01-05", "2020-01-10", "2020-02-01", "2020-03-01"],
            "stars": [999, 30, 30, 40, 10],  # first event predates the repo: a reused name
        }
    )
    g = star_growth(daily, REPOS, (100, 1000), (0.5, 1.5), pd.Timestamp("2020-12-31")).set_index("id")
    assert g.at[1, "archive_events"] == 100 and g.at[1, "coverage"] == pytest.approx(0.5)
    assert bool(g.at[1, "history_reliable"])
    # 100 stars x coverage 0.5 = 50 events, reached on 2020-01-10 (cumulative 60)
    assert g.at[1, "days_to_100"] == 9
    assert g.at[1, "days_to_100_from_first_star"] == 5
    assert np.isnan(g.at[1, "days_to_1000"])  # never had 1,000 stars
    assert g.at[1, "launch_lag_days"] == 4
    assert not bool(g.at[2, "history_reliable"])  # 10 events for 100 stars
    assert g.at[1, "archive_stars_365d"] == 100


def test_monthly_curves_rescale_by_coverage():
    daily = pd.DataFrame({"repo_id": [1, 1, 1], "day": ["2020-01-05", "2020-01-20", "2020-03-01"],
                          "stars": [50, 30, 20]})
    g = star_growth(daily, REPOS, (100,), (0.5, 1.5), pd.Timestamp("2020-12-31"))
    m = monthly_curves(daily, REPOS, g)
    assert m["events_cum"].tolist() == [80, 100]
    assert m["stars_est"].tolist() == [160, 200]  # coverage 0.5 doubles the archive counts


# ---------------------------------------------------------------- build pipeline
def test_build_end_to_end(tmp_path):
    db = Database(tmp_path / "github.db")
    db.set_meta("snapshot_started", "2026-10-08T16:00:00+00:00")
    for i, (bucket, stars) in enumerate([("10-100", 20), ("1k-10k", 2000)], start=1):
        item = {"id": i, "full_name": f"o/r{i}", "stargazers_count": stars, "language": "Go",
                "created_at": "2022-01-01T00:00:00Z", "topics": ["cli", "go"]}
        db.insert_repo(item, f"{bucket}|2022|Go", bucket, "Go")
        db.save_details(i, "ok", 200, {"full_name": f"o/r{i}", "stargazers_count": stars, "forks_count": 1,
                                       "subscribers_count": 2, "topics": ["cli", "go"], "archived": False,
                                       "pushed_at": "2026-09-01T00:00:00Z", "license": {"spdx_id": "MIT"},
                                       "size": 10, "language": "Go", "description": "A tool"})
        db.save_commit_activity(i, "ok", 200, [{"week": 1_700_000_000, "total": 3}] * 52)
    db.save_readme(1, "ok", 200, "README.md", 10, "# r1\n\n## Installation\n\npip install r1\n")
    db.save_readme(2, "missing", 404)
    db.save_star_history(2, [("2022-01-02", 1500), ("2022-06-01", 500)])
    db.close()

    out = tmp_path / "processed"
    assert build.main(["--db", str(tmp_path / "github.db"), "--out", str(out)]) == 0

    repos = pd.read_parquet(out / "repos.parquet").set_index("id")
    assert "readme" not in repos.columns  # README text is never published
    assert repos.at[1, "has_install"] and repos.at[1, "readme_headings"] == 2
    assert repos.at[2, "readme_format"] == "none"
    assert repos.at[2, "coverage"] == pytest.approx(1.0) and repos.at[2, "days_to_1000"] == 1
    assert pd.read_parquet(out / "repo_topics.parquet")["topic"].tolist() == ["cli", "go", "cli", "go"]
    report = json.loads((out / "build_report.json").read_text())
    assert report["ok"] and report["rows"] == 2
    bench = json.loads((out / "readme_benchmarks.json").read_text())
    assert bench["groups"]["all"]["n"] == 1  # repo 2 has no README
