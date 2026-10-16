"""What Makes a GitHub Repo Popular? Streamlit dashboard.

Run from the repository root:  streamlit run app/streamlit_app.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # the shared `wgs` package lives at the repo root

import streamlit as st

from components.data import LANGUAGES, YEARS, Filters, load
from wgs import analysis as A

st.set_page_config(
    page_title="What Makes a GitHub Repo Popular?",
    page_icon=":material/star:",
    layout="wide",
)

data = load()

DEFAULTS = {"f_buckets": list(A.BUCKET_ORDER), "f_languages": list(LANGUAGES), "f_years": (YEARS[0], YEARS[-1])}
for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)


def reset_filters() -> None:
    for key, value in DEFAULTS.items():
        st.session_state[key] = value


with st.sidebar:
    st.markdown("### Filters")
    st.caption("Apply to every page except the README checker.")
    buckets = st.multiselect(
        "Stars", A.BUCKET_ORDER, key="f_buckets", format_func=lambda b: A.BUCKET_LABELS[b],
    )
    languages = st.multiselect("Language", LANGUAGES, key="f_languages")
    years = st.slider("Created", YEARS[0], YEARS[-1], key="f_years")
    st.button("Reset filters", on_click=reset_filters, icon=":material/restart_alt:", width="stretch")
    st.divider()
    st.caption(
        f"Snapshot **{data.report['snapshot']}** · {len(data.repos):,} sampled repos · "
        "[Source code](https://github.com/nabielyr/what-gets-starred)"
    )

st.session_state["filters"] = Filters(
    buckets=tuple(b for b in A.BUCKET_ORDER if b in buckets),
    languages=tuple(l for l in LANGUAGES if l in languages),
    years=tuple(years),
)

pages = [
    st.Page("views/overview.py", title="Overview", icon=":material/dashboard:", default=True),
    st.Page("views/languages.py", title="Languages", icon=":material/code:"),
    st.Page("views/readme.py", title="README", icon=":material/description:"),
    st.Page("views/activity.py", title="Activity", icon=":material/monitor_heart:"),
    st.Page("views/growth.py", title="Growth", icon=":material/trending_up:"),
    st.Page("views/topics.py", title="Licences & topics", icon=":material/sell:"),
    st.Page("views/checker.py", title="README checker", icon=":material/fact_check:"),
    st.Page("views/methodology.py", title="Methodology", icon=":material/menu_book:"),
]
st.navigation(pages, position="top").run()
