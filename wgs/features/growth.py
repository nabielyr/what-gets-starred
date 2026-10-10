"""Star growth features from the GH Archive daily star counts.

The archive does not capture every star, so a repo's raw event count is compared with its
real star count (coverage = events / stars). To date the Nth star we look for the day the
cumulative events reached N x coverage, i.e. we assume missed events are spread evenly over
time. Histories with coverage outside a sane range (renames, transfers, name reuse) are
flagged as unreliable and should be left out of time-to-milestone analysis.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _clean_daily(daily: pd.DataFrame, repos: pd.DataFrame) -> pd.DataFrame:
    """Drop events dated before the repo was created (an older repo with the same name)."""
    created = pd.to_datetime(repos.set_index("id")["created_at"], utc=True).dt.tz_localize(None).dt.normalize()
    d = daily.copy()
    d["day"] = pd.to_datetime(d["day"])
    d = d[d["day"] >= d["repo_id"].map(created)]
    return d.sort_values(["repo_id", "day"])


def star_growth(
    daily: pd.DataFrame,
    repos: pd.DataFrame,
    milestones: tuple[int, ...],
    coverage_range: tuple[float, float],
    snapshot: pd.Timestamp,
) -> pd.DataFrame:
    """One row per repo that has a star history.

    daily: repo_id, day, stars (events that day). repos: id, stars, created_at.
    """
    d = _clean_daily(daily, repos)
    info = repos.set_index("id")
    created = pd.to_datetime(info["created_at"], utc=True).dt.tz_localize(None)
    snapshot = pd.Timestamp(snapshot).tz_localize(None) if pd.Timestamp(snapshot).tzinfo else pd.Timestamp(snapshot)
    lo, hi = coverage_range

    rows = []
    for repo_id, g in d.groupby("repo_id", sort=False):
        stars = int(info.at[repo_id, "stars"])
        events = int(g["stars"].sum())
        coverage = events / stars if stars else np.nan
        cum = g["stars"].cumsum().to_numpy()
        days = g["day"].to_numpy()
        row = {
            "id": repo_id,
            "archive_events": events,
            "coverage": coverage,
            "history_reliable": bool(lo <= coverage <= hi),
            "first_star_date": g["day"].iloc[0],
            "archive_stars_365d": int(g.loc[g["day"] > snapshot - pd.Timedelta(days=365), "stars"].sum()),
        }
        for n in milestones:
            reached = np.nan
            if stars >= n and coverage > 0:
                # coverage < 1: missed events; > 1: unstars. Either way the repo had about
                # n stars once events reached n x coverage.
                threshold = math.ceil(n * coverage)
                idx = np.searchsorted(cum, threshold)
                if idx < len(cum):
                    reached = pd.Timestamp(days[idx])
            row[f"date_{n}"] = reached
        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["launch_lag_days"] = (out["first_star_date"] - out["id"].map(created).dt.normalize()).dt.days
    for n in milestones:
        date = pd.to_datetime(out.pop(f"date_{n}"))
        out[f"days_to_{n}"] = (date - out["id"].map(created).dt.normalize()).dt.days
        out[f"days_to_{n}_from_first_star"] = (date - out["first_star_date"]).dt.days
    return out


def monthly_curves(daily: pd.DataFrame, repos: pd.DataFrame, growth: pd.DataFrame) -> pd.DataFrame:
    """Cumulative stars per repo per month, for growth charts.

    `stars_est` rescales archive events by the repo's coverage so curves end near the real
    star count; only repos with a reliable history are included.
    """
    reliable = growth.loc[growth["history_reliable"], ["id", "coverage"]].set_index("id")["coverage"]
    d = _clean_daily(daily, repos)
    d = d[d["repo_id"].isin(reliable.index)]
    d["month"] = d["day"].dt.to_period("M").dt.to_timestamp()
    m = d.groupby(["repo_id", "month"], as_index=False)["stars"].sum()
    m["events_cum"] = m.groupby("repo_id")["stars"].cumsum()
    m["stars_est"] = (m["events_cum"] / m["repo_id"].map(reliable)).round().astype(int)
    return m.drop(columns="stars").rename(columns={"repo_id": "id"})
