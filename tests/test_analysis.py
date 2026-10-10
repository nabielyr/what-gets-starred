import numpy as np
import pandas as pd
import pytest

from wgs import analysis as A


def test_stratum_weights_recover_population_totals():
    repos = pd.DataFrame(
        {
            "bucket": pd.Categorical(["10-100", "10-100", "10k+"], categories=A.BUCKET_ORDER),
            "created_year": [2020, 2020, 2020],
            "lang_group": ["Go", "Go", "Go"],
        }
    )
    population = pd.DataFrame(
        {"bucket": ["10-100", "10k+"], "year": [2020, 2020], "lang_group": ["Go", "Go"], "population": [1000, 4]}
    )
    w = A.stratum_weights(repos, population)
    assert w.tolist() == [500, 500, 4]
    assert w.sum() == population["population"].sum()


def test_weighted_share():
    mask = pd.Series([True, False, True])
    weights = pd.Series([1.0, 3.0, np.nan])  # missing weight is ignored
    assert A.weighted_share(mask, weights) == pytest.approx(0.25)


def test_spearman_ci_brackets_the_estimate():
    rng = np.random.default_rng(0)
    x = rng.normal(size=400)
    y = x + rng.normal(scale=1.0, size=400)
    rho, lo, hi = A.spearman_ci(x, y, n_boot=200)
    assert lo < rho < hi
    assert 0.55 < rho < 0.8


def test_partial_spearman_removes_a_shared_driver():
    rng = np.random.default_rng(1)
    z = rng.normal(size=2000)
    x = z + rng.normal(scale=0.3, size=2000)
    y = z + rng.normal(scale=0.3, size=2000)
    assert np.corrcoef(x, y)[0, 1] > 0.8
    assert abs(A.partial_spearman(x, y, pd.DataFrame({"z": z}))) < 0.1


def test_survival_share_only_counts_repos_observed_long_enough():
    days = pd.Series([10, 400, 50, 20])
    observed = pd.Series([1000, 1000, 1000, 100])  # the last repo is too young to judge
    assert A.survival_share(days, 365, observed) == pytest.approx(2 / 3)
