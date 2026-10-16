import streamlit as st

from components.data import load

data = load()
report = data.report

st.title("Methodology")

st.markdown(
    f"""
### Data
- **Source:** GitHub REST API (search, repository, README and commit statistics endpoints). The snapshot
  was taken on **{report['snapshot']}**, with details fetched 9–10 October 2026.
- **Sample:** {report['rows']:,} repositories, 1,000 per star range (10–99, 100–999, 1k–9.9k, 10k+),
  spread evenly over creation years 2012–2025 and 13 language groups (728 strata). Forks are excluded.
- **Census:** for every stratum, the Search API's `total_count` was recorded. It gives the number of GitHub
  repos behind each sampled repo, which is used for language trends and population-weighted estimates.
- **Star history:** GitHub restricted its stargazer endpoints to repo admins in July 2026, so star
  dates come from [GH Archive](https://www.gharchive.org/) via ClickHouse's public playground.
  {report['star_history']['reliable']:,} of {report['star_history']['repos']:,} histories are reliable
  (archive coverage 0.5–1.5).

### Sampling
The Search API returns at most 1,000 results per query, ordered by "best match". Each stratum was sampled
from randomly chosen date windows (year → month → day) and result pages, taking at most 3 repos per
page. Quota left over by small strata was moved to others in the same star range.

### Features
- **README:** words, headings, links, code blocks and tables. Images are split into content images (and
  GIFs), badges and decorations (avatars, logos, icons). The README is also checked for install, usage,
  demo, contributing and licence sections, and for links to a live demo or video.
- **Metadata:** age, size, topics, homepage, licence, language and list-repo flag.
- **Activity:** commits and active weeks in the last 52 weeks and days since the last push. A repo is
  inactive when it is archived, has had no push in 365 days, or had no commits in 52 weeks.
- **Growth:** coverage-corrected dates of the 100th, 1,000th and 10,000th star, and monthly curves.

### Limitations
- **Correlation, not causation.** Popularity also *causes* docs, badges, contributors and commits.
- **Survivorship and snapshot.** Only public repos that still exist and have 10+ stars are included,
  each measured once. A README is today's README, not the one the stars were earned with.
- **API limits.** Commit statistics cover the default branch only and were unavailable for 5 very large
  repos. `pushed_at` counts pushes to any branch.
- **Archive gaps.** GH Archive misses some events and loses history across renames. Milestone dates are
  estimates.
- **Heuristics.** Section detection, badge/decoration classification and the list-repo flag are rules
  of thumb.

### Code and notebooks
Everything is open source:
[github.com/nabielyr/what-gets-starred](https://github.com/nabielyr/what-gets-starred).
The full analysis, with confidence intervals and the model evaluation, is in the notebooks
`01_analysis.ipynb` and `02_model.ipynb`.
"""
)
