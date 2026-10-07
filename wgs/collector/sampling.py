"""Stratified sampling design.

The GitHub Search API returns at most 1,000 results per query and orders them by
"best match", so simply paging through one big query gives a biased sample. Instead
the population is split into strata (star bucket x creation year x language group),
each stratum gets a quota, and repos are drawn from randomly chosen date windows and
result pages until the quota is met.
"""

from __future__ import annotations

import calendar
import hashlib
import random
from dataclasses import dataclass
from datetime import date

from wgs.config import Bucket, LanguageGroup, SamplingSettings

SEARCH_MAX_RESULTS = 1000
PER_PAGE = 100


@dataclass(frozen=True)
class Stratum:
    bucket: Bucket
    year: int
    group: LanguageGroup

    @property
    def key(self) -> str:
        return f"{self.bucket.name}|{self.year}|{self.group.name}"

    def query(self, start: date | None = None, end: date | None = None) -> str:
        start = start or date(self.year, 1, 1)
        end = end or date(self.year, 12, 31)
        return f"{self.bucket.qualifier} created:{start.isoformat()}..{end.isoformat()} {self.group.qualifier} fork:false"


def build_strata(settings: SamplingSettings) -> list[Stratum]:
    return [
        Stratum(bucket, year, group)
        for bucket in settings.buckets
        for year in settings.years
        for group in settings.language_groups
    ]


def stable_rng(*parts: object) -> random.Random:
    """A Random seeded from the given parts, identical across runs and processes."""
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def allocate_quotas(keys: list[str], target: int, seed: int, salt: str = "") -> dict[str, int]:
    """Split `target` as evenly as possible over `keys`; the remainder goes to random keys."""
    if not keys:
        return {}
    base, remainder = divmod(target, len(keys))
    quotas = {k: base for k in keys}
    rng = stable_rng(seed, "allocate", salt, target)
    for k in rng.sample(sorted(keys), remainder):
        quotas[k] += 1
    return quotas


def redistribute(deficit: int, capacity: dict[str, int], seed: int, salt: str = "") -> dict[str, int]:
    """Give `deficit` extra quota to strata that still have unsampled repos.

    `capacity` maps stratum key -> how many more repos it could still provide.
    Quota is handed out round-robin in a random order so no stratum dominates.
    """
    extra = {k: 0 for k in capacity}
    order = sorted(k for k, c in capacity.items() if c > 0)
    stable_rng(seed, "redistribute", salt, deficit).shuffle(order)
    while deficit > 0 and order:
        still_open = []
        for k in order:
            if deficit == 0:
                break
            if extra[k] < capacity[k]:
                extra[k] += 1
                deficit -= 1
            if extra[k] < capacity[k]:
                still_open.append(k)
        order = still_open
    return {k: v for k, v in extra.items() if v > 0}


def random_window(year: int, rng: random.Random, granularity: str) -> tuple[date, date]:
    """A random month or day inside the given year."""
    month = rng.randint(1, 12)
    last_day = calendar.monthrange(year, month)[1]
    if granularity == "month":
        return date(year, month, 1), date(year, month, last_day)
    day = date(year, month, rng.randint(1, last_day))
    return day, day


def page_count(total_count: int) -> int:
    reachable = min(total_count, SEARCH_MAX_RESULTS)
    return max(1, -(-reachable // PER_PAGE))
