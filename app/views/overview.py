import plotly.graph_objects as go
import streamlit as st

from components.charts import BUCKET_COLORS, empty_state, pct, show
from components.data import current_filters, filter_repos, load
from wgs import analysis as A

data = load()
f = current_filters()
repos = filter_repos(data.repos, f)

st.title("What makes a GitHub repo popular?")
st.markdown(
    "An analysis of **4,000 GitHub repositories**, sampled evenly across four star ranges, 14 creation "
    "years and 13 language groups, to see which factors go together with stars. "
    "Explore each question with the pages above, or score your own README in the **README checker**."
)
st.caption(f"Showing: {f.describe()}")

if repos.empty:
    empty_state()
    st.stop()

popular = repos[repos["bucket"].isin(["1k-10k", "10k+"])]
cols = st.columns(5)
cols[0].metric("Repos in view", f"{len(repos):,}", help="Sampled repos matching the filters.", border=True)
cols[1].metric(
    "GitHub repos (est.)", f"{repos['weight'].sum() / 1e6:.2f}M" if repos["weight"].sum() > 1e6 else f"{repos['weight'].sum():,.0f}",
    help="Population size behind the sample: each repo is weighted by how many GitHub repos with 10+ stars "
    "its stratum (star range × creation year × language) contains.", border=True,
)
cols[2].metric(
    "Inactive, 1k+ stars",
    pct(A.weighted_share(popular["is_inactive"], popular["weight"])) if len(popular) else "–",
    help="Population-weighted share of repos with 1,000+ stars that are archived, unpushed for a year, "
    "or had no commits in 52 weeks.", border=True,
)
reliable = repos[repos["history_reliable"] & (repos["stars"] >= 1000)]
cols[3].metric(
    "Months to 1k stars",
    f"{reliable['days_to_1000_from_first_star'].median() / 30.44:.0f}" if len(reliable) else "–",
    help="Median months from the first star, for repos that reached 1,000 stars and have a reliable "
    "GH Archive history.",
    border=True,
)
cols[4].metric(
    "README words", f"{repos['readme_words'].median():,.0f}",
    help="Median README length in words, excluding code, URLs and markup.", border=True,
)

st.subheader("Key findings")
findings = [
    (":material/code:", "Python and TypeScript took over",
     "Among repos that reached 1,000+ stars, Python grew from about 13% of those created in 2012 to "
     "34% in 2025, and TypeScript from 3% to 21%. Rust, Go and TypeScript have the best hit rate: "
     "about 2× the average chance of growing from 10 to 1,000+ stars."),
    (":material/description:", "A good README is hygiene, not a growth hack",
     "Popular repos have longer READMEs with more links, badges and images, but among repos of "
     "similar popularity those features barely differ (ρ ≤ 0.15). GIFs and demo sections show no "
     "relationship at all."),
    (":material/monitor_heart:", "Stars outlive maintenance",
     "The median 10k+ repo made about 100 commits last year, yet an estimated 48% of all 1k+ repos "
     "and 27% of 10k+ repos are inactive."),
    (":material/trending_up:", "Popularity is usually earned slowly",
     "Typical repos needed about 3 months to reach 100 stars, a year to 1,000 and 3 years to 10,000. "
     "Long-lived 10k+ repos had under half of today's stars four years after launch."),
    (":material/sell:", "Licences, topics and AI",
     "Popular repos nearly always have a licence (mostly MIT or Apache-2.0) and use topics. The "
     "fastest-rising topics are all about AI: llm, mcp, agents, rag, claude-code."),
    (":material/model_training:", "Measurable features explain only part of it",
     "A model using README, metadata and activity features places a repo in the right star range "
     "about half the time (macro-F1 0.52, against 0.25 for guessing)."),
]
for row in range(0, len(findings), 3):
    cols = st.columns(3)
    for col, (icon, title, text) in zip(cols, findings[row:row + 3]):
        with col.container(border=True, height="stretch"):
            st.markdown(f"**{title}**")
            st.markdown(f"<span style='color:#52514e;font-size:0.92rem'>{text}</span>", unsafe_allow_html=True)
st.caption("Findings are computed on the full dataset; the metrics above follow the filters.")

left, right = st.columns(2)
with left:
    share = (
        repos.groupby("bucket", observed=True)
        .apply(lambda g: A.weighted_share(g["is_inactive"], g["weight"]), include_groups=False)
    )
    labels = [A.BUCKET_LABELS[b] for b in share.index]
    fig = go.Figure(go.Bar(
        x=labels, y=share.values, marker_color=[BUCKET_COLORS[l] for l in labels],
        text=[pct(v) for v in share.values], textposition="outside", cliponaxis=False,
        hovertemplate="%{x} stars<br>%{y:.0%} inactive<extra></extra>",
    ))
    show(fig, height=330, title="Inactive repos by star range (population-weighted)", yaxis_tickformat=".0%",
         yaxis_range=[0, 1], xaxis_title="stars", showlegend=False)
with right:
    med = repos.groupby("bucket", observed=True)["readme_words"].median()
    labels = [A.BUCKET_LABELS[b] for b in med.index]
    fig = go.Figure(go.Bar(
        x=labels, y=med.values, marker_color=[BUCKET_COLORS[l] for l in labels],
        text=[f"{v:,.0f}" for v in med.values], textposition="outside", cliponaxis=False,
        hovertemplate="%{x} stars<br>median %{y:,.0f} words<extra></extra>",
    ))
    show(fig, height=330, title="Median README length (words)", xaxis_title="stars", showlegend=False)
