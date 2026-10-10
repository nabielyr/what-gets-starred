"""Load project configuration from config.yaml and secrets from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"


@dataclass(frozen=True)
class Bucket:
    name: str
    min_stars: int
    max_stars: int | None

    @property
    def qualifier(self) -> str:
        if self.max_stars is None:
            return f"stars:>={self.min_stars}"
        return f"stars:{self.min_stars}..{self.max_stars}"


@dataclass(frozen=True)
class LanguageGroup:
    name: str
    qualifier: str


@dataclass(frozen=True)
class GitHubSettings:
    api_url: str = "https://api.github.com"
    timeout: float = 30
    max_retries: int = 6
    backoff_base: float = 2.0
    backoff_max: float = 300
    min_remaining: dict[str, int] = field(default_factory=lambda: {"core": 25, "search": 1})


@dataclass(frozen=True)
class SamplingSettings:
    buckets: tuple[Bucket, ...]
    language_groups: tuple[LanguageGroup, ...]
    years: tuple[int, ...]
    target_per_bucket: int = 1000
    max_per_window: int = 3
    redistribute_rounds: int = 3


@dataclass(frozen=True)
class EnrichSettings:
    stats_max_attempts: int = 6
    stats_retry_wait: float = 60
    stats_timeout: float = 20


@dataclass(frozen=True)
class StarHistorySettings:
    url: str = "https://play.clickhouse.com/"
    user: str = "play"
    buckets: tuple[str, ...] = ("100-1k", "1k-10k", "10k+")
    batch_size: int = 50
    timeout: float = 120
    max_retries: int = 5


@dataclass(frozen=True)
class FeatureSettings:
    processed_dir: Path = ROOT / "data" / "processed"
    inactive_days: int = 365
    milestones: tuple[int, ...] = (100, 1000, 10000)
    coverage_range: tuple[float, float] = (0.5, 1.5)
    popular_buckets: tuple[str, ...] = ("1k-10k", "10k+")


@dataclass(frozen=True)
class Config:
    seed: int
    db_path: Path
    log_path: Path
    github: GitHubSettings
    sampling: SamplingSettings
    enrich: EnrichSettings
    star_history: StarHistorySettings = field(default_factory=StarHistorySettings)
    features: FeatureSettings = field(default_factory=FeatureSettings)

    def with_target(self, target_per_bucket: int) -> "Config":
        return replace(self, sampling=replace(self.sampling, target_per_bucket=target_per_bucket))

    def with_db(self, db_path: Path) -> "Config":
        return replace(self, db_path=db_path)


def _language_groups(raw: dict) -> tuple[LanguageGroup, ...]:
    langs = raw.get("languages", [])
    groups = [LanguageGroup(name=l["name"], qualifier=f"language:{l['slug']}") for l in langs]
    if raw.get("include_other", True):
        # Search qualifiers can be negated; this matches every other language and repos with none.
        negated = " ".join(f"-language:{l['slug']}" for l in langs)
        groups.append(LanguageGroup(name="Other", qualifier=negated))
    return tuple(groups)


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    s = raw["sampling"]
    first_year, last_year = s["years"]
    sampling = SamplingSettings(
        buckets=tuple(Bucket(b["name"], b["min"], b.get("max")) for b in s["buckets"]),
        language_groups=_language_groups(s),
        years=tuple(range(first_year, last_year + 1)),
        target_per_bucket=s.get("target_per_bucket", 1000),
        max_per_window=s.get("max_per_window", 3),
        redistribute_rounds=s.get("redistribute_rounds", 3),
    )

    paths = raw.get("paths", {})
    return Config(
        seed=raw.get("seed", 42),
        db_path=ROOT / paths.get("db", "data/raw/github.db"),
        log_path=ROOT / paths.get("log", "data/raw/collector.log"),
        github=GitHubSettings(**raw.get("github", {})),
        sampling=sampling,
        enrich=EnrichSettings(**raw.get("enrich", {})),
        star_history=StarHistorySettings(
            **{k: tuple(v) if isinstance(v, list) else v for k, v in raw.get("star_history", {}).items()}
        ),
        features=_feature_settings(raw.get("features", {})),
    )


def _feature_settings(raw: dict) -> FeatureSettings:
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()}
    if "processed_dir" in values:
        values["processed_dir"] = ROOT / values["processed_dir"]
    return FeatureSettings(**values)


def get_github_token() -> str:
    """Read GITHUB_TOKEN from the environment or the project's .env file."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token or token == "ghp_your_token_here":
        raise SystemExit(
            "GITHUB_TOKEN is not set. Copy .env.example to .env and paste a personal access token "
            "(https://github.com/settings/tokens, no scopes needed for public data)."
        )
    return token
