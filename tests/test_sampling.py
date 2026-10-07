from datetime import date

from wgs.collector.sampling import (
    Stratum,
    allocate_quotas,
    build_strata,
    page_count,
    random_window,
    redistribute,
    stable_rng,
)
from wgs.config import Bucket, LanguageGroup


def test_config_builds_other_group(cfg):
    groups = {g.name: g.qualifier for g in cfg.sampling.language_groups}
    assert groups["C++"] == "language:cpp"
    assert groups["C#"] == "language:csharp"
    assert groups["Other"].count("-language:") == 12
    assert cfg.sampling.buckets[-1].qualifier == "stars:>=10000"


def test_build_strata_covers_every_combination(cfg):
    s = cfg.sampling
    strata = build_strata(s)
    assert len(strata) == len(s.buckets) * len(s.years) * len(s.language_groups)
    assert len({x.key for x in strata}) == len(strata)


def test_stratum_query():
    s = Stratum(Bucket("100-1k", 100, 999), 2019, LanguageGroup("Go", "language:go"))
    assert s.query() == "stars:100..999 created:2019-01-01..2019-12-31 language:go fork:false"
    assert "created:2019-03-05..2019-03-05" in s.query(date(2019, 3, 5), date(2019, 3, 5))


def test_allocate_quotas_sums_to_target_and_is_deterministic():
    keys = [f"k{i}" for i in range(182)]
    q1 = allocate_quotas(keys, 1000, seed=42, salt="b")
    q2 = allocate_quotas(keys, 1000, seed=42, salt="b")
    assert q1 == q2
    assert sum(q1.values()) == 1000
    assert set(q1.values()) <= {5, 6}


def test_allocate_quotas_small_target():
    quotas = allocate_quotas([f"k{i}" for i in range(182)], 13, seed=1)
    assert sum(quotas.values()) == 13
    assert max(quotas.values()) == 1


def test_redistribute_respects_capacity():
    extra = redistribute(10, {"a": 2, "b": 0, "c": 100}, seed=1)
    assert sum(extra.values()) == 10
    assert extra.get("a", 0) <= 2
    assert "b" not in extra


def test_redistribute_when_capacity_runs_out():
    extra = redistribute(10, {"a": 1, "b": 2}, seed=1)
    assert extra == {"a": 1, "b": 2}


def test_random_window_stays_in_year():
    rng = stable_rng("x")
    for _ in range(100):
        start, end = random_window(2020, rng, "month")
        assert start.year == end.year == 2020 and start.day == 1 and start <= end
        day_start, day_end = random_window(2021, rng, "day")
        assert day_start == day_end and day_start.year == 2021


def test_page_count():
    assert page_count(0) == 1
    assert page_count(100) == 1
    assert page_count(101) == 2
    assert page_count(50_000) == 10  # the search API stops at 1,000 results
