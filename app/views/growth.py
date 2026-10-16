import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components.charts import empty_state, show
from components.data import current_filters, filter_repos, load
from wgs import analysis as A

data = load()
f = current_filters()

st.title("Growth")
st.markdown(
    "How long does it take to reach 100, 1,000 and 10,000 stars? GitHub restricted its stargazer API in "
    "July 2026, so star dates come from **GH Archive**. Only repos with a reliable history are used (archive "
    "events between 0.5× and 1.5× the real star count). Time is measured from the **first star**, a better "
    "proxy for going public than the creation date."
)
st.caption(f"Showing: {f.describe()}")

repos = filter_repos(data.repos, f)
reliable = repos[repos["history_reliable"]]
if reliable.empty:
    empty_state("No repos with a reliable star history match the filters. Star histories exist for repos with 100+ stars.")
    st.stop()

MILESTONES = [100, 1000, 10000]
cols = st.columns(3)
for col, n in zip(cols, MILESTONES):
    days = reliable.loc[reliable["stars"] >= n, f"days_to_{n}_from_first_star"].dropna()
    if len(days):
        value = f"{days.median():,.0f} days" if days.median() < 120 else f"{days.median() / 30.44:,.0f} months"
        col.metric(f"Median time to {n:,} stars", value, border=True,
                   help=f"{len(days):,} repos. Fastest 10%: {days.quantile(0.1):,.0f} days.")
    else:
        col.metric(f"Median time to {n:,} stars", "–", border=True)

fig = go.Figure()
for i, n in enumerate(MILESTONES):
    days = np.sort(reliable.loc[reliable["stars"] >= n, f"days_to_{n}_from_first_star"].dropna().clip(lower=1))
    if len(days) == 0:
        continue
    fig.add_trace(go.Scatter(
        x=days, y=np.arange(1, len(days) + 1) / len(days), mode="lines", line_shape="hv",
        name=f"{n:,} stars (n={len(days):,})", line=dict(color=A.CATEGORICAL[i], width=2.5),
        hovertemplate=f"{n:,} stars<br>%{{y:.0%}} of repos within %{{x:,.0f}} days<extra></extra>",
    ))
fig.add_hline(y=0.5, line=dict(color=A.INK["grid"], width=1))
show(fig, height=400, title="Time from first star to each milestone (repos that reached it)",
     xaxis=dict(type="log", tickvals=[1, 7, 30, 90, 365, 1095, 3650],
                ticktext=["1 day", "1 week", "1 month", "3 months", "1 year", "3 years", "10 years"]),
     yaxis_tickformat=".0%", yaxis_title="cumulative share of repos")

st.subheader("Are new repos growing faster?")
st.markdown(
    "Comparing launch years naively is misleading: a repo launched in 2025 can only be in the data if it "
    "already reached 1,000 stars, i.e. fast. The chart below gives every launch year the same 5-year window."
)
snapshot = pd.Timestamp(data.report["snapshot"])
big = reliable[reliable["stars"] >= 1000].copy()
big["launch_year"] = pd.to_datetime(big["first_star_date"]).dt.year
fair = big[((snapshot - pd.to_datetime(big["first_star_date"])).dt.days >= 5 * 365)
           & (big["days_to_1000_from_first_star"] <= 5 * 365)]
if len(fair) >= 20:
    cohort = fair.groupby("launch_year").agg(
        repos=("id", "size"), fast=("days_to_1000_from_first_star", lambda s: (s <= 365).mean()))
    cohort = cohort[cohort["repos"] >= 10]
    fig = go.Figure(go.Bar(
        x=cohort.index, y=cohort["fast"], marker_color=A.CATEGORICAL[0], customdata=cohort["repos"],
        text=[f"{v:.0%}" for v in cohort["fast"]], textposition="outside", cliponaxis=False,
        hovertemplate="launched %{x}: %{y:.0%} within a year<br>%{customdata} repos<extra></extra>",
    ))
    show(fig, height=330, title="Reached 1,000 stars within a year of the first star", yaxis_tickformat=".0%",
         yaxis_range=[0, 1], xaxis_title="year of first star", xaxis_dtick=1, showlegend=False)
    st.caption("Repos that reached 1,000 stars within 5 years, launch years with at least 5 years of history "
               "and 10+ repos.")
else:
    st.caption("Too few repos in the current filters for a fair cohort comparison.")

st.subheader("Explore a repo's star history")
curves = data.monthly
choices = reliable[reliable["id"].isin(curves["id"].unique())].sort_values("stars", ascending=False)
if choices.empty:
    st.caption("No star histories for the current filters.")
    st.stop()
name = st.selectbox(
    "Repository", choices["full_name"], index=0,
    format_func=lambda n: f"{n}  ·  {int(choices.loc[choices['full_name'] == n, 'stars'].iloc[0]):,} stars",
)
repo = choices[choices["full_name"] == name].iloc[0]
curve = curves[curves["id"] == repo["id"]]
fig = go.Figure(go.Scatter(
    x=curve["month"], y=curve["stars_est"], mode="lines", line=dict(color=A.CATEGORICAL[0], width=2.5),
    fill="tozeroy", fillcolor="rgba(42,120,214,0.10)", name=name,
    hovertemplate="%{x|%b %Y}: ~%{y:,.0f} stars<extra></extra>",
))
for n in MILESTONES:
    d = repo[f"days_to_{n}_from_first_star"]
    if pd.notna(d):
        when = pd.Timestamp(repo["first_star_date"]) + pd.Timedelta(days=d)
        fig.add_vline(x=when, line=dict(color=A.INK["grid"], width=1))
        fig.add_annotation(x=when, y=1, yref="paper", text=f"{n:,}", showarrow=False, yshift=8,
                           font=dict(size=11, color=A.INK["secondary"]))
show(fig, height=360, title=f"{name}: estimated stars over time", showlegend=False, yaxis_title="stars")
c = st.columns(4)
c[0].metric("Stars now", f"{int(repo['stars']):,}")
c[1].metric("Created", pd.Timestamp(repo["created_at"]).strftime("%b %Y"))
c[2].metric("First star", pd.Timestamp(repo["first_star_date"]).strftime("%b %Y"))
c[3].metric("Archive coverage", f"{repo['coverage']:.2f}",
            help="GH Archive star events ÷ current stars. The curve is rescaled by this factor.")
st.link_button("Open on GitHub", f"https://github.com/{name}", icon=":material/open_in_new:")
