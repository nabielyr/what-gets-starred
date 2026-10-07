# What Makes a GitHub Repo Popular?

A data science project that samples thousands of GitHub repositories through the GitHub REST API and
explores which factors are associated with the number of stars: language, README quality, commit
activity, licence and topics.

> 🚧 Work in progress. Data collection is being built first. Analysis, dashboard and findings come later.

## Project structure

```
wgs/                 shared Python package
  config.py          loads config.yaml and the token from .env
  collector/         GitHub API client, SQLite storage, sampling, enrichment, CLI
tests/               pytest suite (API calls are mocked)
config.yaml          sampling design and pipeline settings
data/raw/            SQLite database and logs (not committed)
data/processed/      cleaned datasets used by the notebooks and dashboard
```

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # then paste your GitHub token into .env
```

A [personal access token](https://github.com/settings/tokens) without any scopes is enough, since only
public data is read. The token raises the API limit from 60 to 5,000 requests per hour.

## Data collection

The sample is **stratified** so that unpopular repos are represented as well as popular ones:

| Dimension | Values |
|---|---|
| Star bucket | 10–99, 100–999, 1k–9.9k, 10k+ |
| Creation year | 2012–2025 |
| Language group | Python, JavaScript, TypeScript, Java, Go, Rust, C++, C, C#, PHP, Ruby, Swift, Other |

The Search API returns at most 1,000 results per query and orders them by "best match", so each
stratum is sampled from randomly chosen date windows and result pages. The `total_count` of every
stratum is also stored as a **census**, so population-level trends (for example, language
popularity by year) do not depend on the balanced sample.

For each sampled repo the collector stores the repository metadata, the README text and the
number of commits per week over the last 52 weeks.

```bash
python -m wgs.collector run --limit 50   # quick test run -> data/raw/sample.db
python -m wgs.collector run              # full run (about 4,000 repos, several hours)
python -m wgs.collector status           # progress and remaining API quota
```

The pipeline is **resumable**. Every result is committed to SQLite immediately, and search pages
are cached. If the run stops (Ctrl+C, a crash, a closed laptop), running the same command again
continues where it left off without repeating requests. Rate limits are handled automatically:
the client tracks the core and search quotas separately, sleeps until the reset time when a quota
runs low, honours `Retry-After` on secondary limits, and retries server errors with exponential
backoff.

## Tests

```bash
pytest
```
