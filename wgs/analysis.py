"""Helpers shared by the analysis notebooks: loading, survey weights, statistics, chart style."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from wgs.config import ROOT

PROCESSED = ROOT / "data" / "processed"
BUCKET_ORDER = ["10-100", "100-1k", "1k-10k", "10k+"]
BUCKET_LABELS = {"10-100": "10–99", "100-1k": "100–999", "1k-10k": "1k–9.9k", "10k+": "10k+"}

# Validated palettes (see the dataviz notes in the README): an ordinal blue ramp for the
# ordered star buckets, and a fixed-order categorical palette for unordered groups.
BUCKET_COLORS = dict(zip(BUCKET_ORDER, ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]))
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
MUTED = "#b5b3ab"
DIVERGING = ("#2a78d6", "#f0efec", "#e34948")  # negative, neutral, positive
INK = {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#8a8880", "grid": "#e6e4df"}
SURFACE = "#fcfcfb"


@dataclass
class Data:
    repos: pd.DataFrame
    topics: pd.DataFrame
    monthly: pd.DataFrame
    population: pd.DataFrame
    benchmarks: dict
    report: dict


def load(processed: Path = PROCESSED) -> Data:
    repos = pd.read_parquet(processed / "repos.parquet")
    repos["bucket"] = pd.Categorical(repos["bucket"].astype(str), categories=BUCKET_ORDER, ordered=True)
    return Data(
        repos=repos,
        topics=pd.read_parquet(processed / "repo_topics.parquet"),
        monthly=pd.read_parquet(processed / "star_monthly.parquet"),
        population=pd.read_parquet(processed / "population.parquet"),
        benchmarks=json.loads((processed / "readme_benchmarks.json").read_text(encoding="utf-8")),
        report=json.loads((processed / "build_report.json").read_text(encoding="utf-8")),
    )


# ---------------------------------------------------------------- survey weights
def stratum_weights(repos: pd.DataFrame, population: pd.DataFrame) -> pd.Series:
    """Inverse-probability weights: how many GitHub repos each sampled repo stands for.

    The sample takes ~equal numbers from every stratum (bucket x creation year x language
    group), so popular repos and rare languages are heavily over-represented. Weighting each
    repo by population / sample size of its stratum recovers population-level estimates.
    """
    key = ["bucket", "created_year", "lang_group"]
    sample = repos.assign(bucket=repos["bucket"].astype(str)).groupby(key[:1] + key[1:]).size()
    pop = population.rename(columns={"year": "created_year"}).set_index(key)["population"]
    w = (pop / sample).rename("weight")
    idx = pd.MultiIndex.from_arrays(
        [repos["bucket"].astype(str), repos["created_year"], repos["lang_group"]], names=key
    )
    return pd.Series(w.reindex(idx).to_numpy(), index=repos.index, name="weight")


def weighted_share(mask: pd.Series, weights: pd.Series) -> float:
    m = mask.astype(float)
    ok = m.notna() & weights.notna()
    return float((m[ok] * weights[ok]).sum() / weights[ok].sum())


# ---------------------------------------------------------------- statistics
def spearman_ci(x, y, n_boot: int = 1000, seed: int = 42, ci: float = 0.95) -> tuple[float, float, float]:
    """Spearman's rho with a percentile bootstrap confidence interval."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    rho = stats.spearmanr(x, y).statistic
    rng = np.random.default_rng(seed)
    rx, ry = stats.rankdata(x), stats.rankdata(y)
    boots = []
    for _ in range(n_boot):
        i = rng.integers(0, len(x), len(x))
        boots.append(np.corrcoef(stats.rankdata(rx[i]), stats.rankdata(ry[i]))[0, 1])
    lo, hi = np.nanpercentile(boots, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    return float(rho), float(lo), float(hi)


def partial_spearman(x, y, controls) -> float:
    """Spearman correlation of x and y after removing the (rank-)linear effect of controls."""
    df = pd.DataFrame({"x": x, "y": y})
    controls = pd.DataFrame(controls).reset_index(drop=True)
    df = pd.concat([df.reset_index(drop=True), controls], axis=1).dropna()
    ranks = df.rank()
    z = np.column_stack([np.ones(len(ranks)), ranks[controls.columns].to_numpy()])

    def resid(col):
        beta, *_ = np.linalg.lstsq(z, ranks[col].to_numpy(), rcond=None)
        return ranks[col].to_numpy() - z @ beta

    return float(np.corrcoef(resid("x"), resid("y"))[0, 1])


def survival_share(days: pd.Series, horizon: int, observed_days: pd.Series) -> float:
    """Share of repos that reached a milestone within `horizon` days, among repos observed for
    at least `horizon` days. Avoids the bias that young repos can only show short times."""
    eligible = observed_days >= horizon
    return float((days[eligible] <= horizon).mean())


# ---------------------------------------------------------------- chart style
def set_style() -> None:
    import matplotlib as mpl

    mpl.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "figure.dpi": 110,
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.labelcolor": INK["secondary"],
            "axes.edgecolor": INK["grid"],
            "axes.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": INK["grid"],
            "grid.linewidth": 0.6,
            "grid.linestyle": "-",
            "xtick.color": INK["secondary"],
            "ytick.color": INK["secondary"],
            "xtick.major.size": 0,
            "ytick.major.size": 0,
            "text.color": INK["primary"],
            "legend.frameon": False,
            "lines.linewidth": 2,
        }
    )


def note(ax, text: str) -> None:
    """A muted source/caption line under a chart."""
    ax.annotate(text, xy=(0, -0.16), xycoords="axes fraction", fontsize=8, color=INK["muted"], va="top")
