import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components.charts import BUCKET_COLORS, empty_state, pct, show
from components.data import current_filters, filter_repos, load
from wgs import analysis as A

data = load()
f = current_filters()

st.title("Activity")
st.markdown(
    "Do popular repos keep being worked on, and how many have quietly stopped? A repo is **inactive** "
    "when it is archived, has not been pushed to in 365 days, or had no commits on its default branch "
    "in the last 52 weeks."
)
st.caption(f"Showing: {f.describe()}")

repos = filter_repos(data.repos, f)
if repos.empty:
    empty_state()
    st.stop()


def weighted(mask) -> str:
    g = repos[mask]
    return pct(A.weighted_share(g["is_inactive"], g["weight"])) if len(g) else "–"


cols = st.columns(3)
cols[0].metric("Inactive, all repos in view", weighted(repos["stars"] >= 0), border=True,
               help="Population-weighted: each repo counts for the GitHub repos its stratum represents.")
cols[1].metric("Inactive, 1k+ stars", weighted(repos["bucket"].isin(["1k-10k", "10k+"])), border=True)
cols[2].metric("Inactive, 10k+ stars", weighted(repos["bucket"] == "10k+"), border=True)

REASONS = ["active", "no commits in a year", "no push in a year", "archived"]
COLORS = dict(zip(REASONS, [A.CATEGORICAL[0], A.CATEGORICAL[3], A.CATEGORICAL[1], A.CATEGORICAL[6]]))
mix = pd.crosstab(repos["bucket"], repos["inactive_reason"], normalize="index").reindex(columns=REASONS, fill_value=0)
labels = [A.BUCKET_LABELS[b] for b in mix.index]
fig = go.Figure()
for reason in REASONS:
    fig.add_trace(go.Bar(
        y=labels, x=mix[reason], name=reason, orientation="h", marker=dict(color=COLORS[reason], line=dict(color="white", width=2)),
        text=[pct(v) if v >= 0.06 else "" for v in mix[reason]], textposition="inside", insidetextanchor="middle",
        textfont=dict(color="white" if reason != "no commits in a year" else A.INK["primary"]),
        hovertemplate=f"%{{y}} stars<br>{reason}: %{{x:.1%}}<extra></extra>",
    ))
show(fig, height=300, barmode="stack", title="Activity status by star range (sample)",
     xaxis_tickformat=".0%", xaxis_range=[0, 1], yaxis=dict(autorange="reversed", title="stars"))

left, right = st.columns(2)
with left:
    fig = go.Figure()
    for b, g in repos.groupby("bucket", observed=True):
        label = A.BUCKET_LABELS[b]
        fig.add_trace(go.Box(
            x=np.log10(g["commits_52w"].dropna() + 1), name=label, boxpoints=False,
            marker_color=BUCKET_COLORS[label], fillcolor=BUCKET_COLORS[label], line=dict(width=1.5), hoverinfo="skip",
        ))
    ticks = [0, 1, 10, 100, 1000, 5000]
    show(fig, height=330, title="Commits in the last 52 weeks", showlegend=False,
         xaxis=dict(tickvals=np.log10(np.array(ticks) + 1), ticktext=[f"{t:,}" for t in ticks], title="log scale"))
    med = repos.groupby("bucket", observed=True)["commits_52w"].median()
    st.caption("Median commits: " + " · ".join(f"{A.BUCKET_LABELS[b]} **{v:,.0f}**" for b, v in med.items()))
with right:
    by_year = repos.groupby(["bucket", "created_year"], observed=True)["is_inactive"].mean().reset_index()
    fig = go.Figure()
    for b, g in by_year.groupby("bucket", observed=True):
        label = A.BUCKET_LABELS[b]
        fig.add_trace(go.Scatter(
            x=g["created_year"], y=g["is_inactive"], mode="lines+markers", name=f"{label} stars",
            line=dict(color=BUCKET_COLORS[label], width=2), marker=dict(size=7),
            hovertemplate=f"{label} stars, created %{{x}}<br>%{{y:.0%}} inactive<extra></extra>",
        ))
    show(fig, height=330, title="Inactive share by creation year", yaxis_tickformat=".0%",
         yaxis_range=[0, 1], hovermode="x unified")

st.info(
    "Stars accumulate and never expire, so finished tools, tutorials and abandoned experiments keep their "
    "counts long after work stops. Activity and popularity also feed each other: popular projects receive "
    "issues and pull requests that keep them active.", icon=":material/lightbulb:",
)
