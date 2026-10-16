import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components.charts import empty_state, pct, show
from components.data import current_filters, filter_repos, load
from wgs import analysis as A

data = load()
f = current_filters()

st.title("Licences & topics")
st.caption(f"Showing: {f.describe()}")

repos = filter_repos(data.repos, f)
if repos.empty:
    empty_state()
    st.stop()

FAMILY = {"MIT": "MIT", "Apache-2.0": "Apache-2.0", "GPL-3.0": "GPL family", "GPL-2.0": "GPL family",
          "AGPL-3.0": "GPL family", "LGPL": "GPL family", "Custom": "Custom / unrecognised", "None": "No licence"}
ORDER = ["MIT", "Apache-2.0", "Other permissive", "GPL family", "Custom / unrecognised", "No licence"]
COLORS = dict(zip(ORDER, [A.CATEGORICAL[0], A.CATEGORICAL[2], A.CATEGORICAL[5], A.CATEGORICAL[1],
                          A.CATEGORICAL[4], A.MUTED]))
fam = repos["license_group"].map(FAMILY).fillna("Other permissive")
mix = pd.crosstab(repos["bucket"], fam, normalize="index").reindex(columns=ORDER, fill_value=0)
labels = [A.BUCKET_LABELS[b] for b in mix.index]
fig = go.Figure()
for family in ORDER:
    fig.add_trace(go.Bar(
        y=labels, x=mix[family], name=family, orientation="h",
        marker=dict(color=COLORS[family], line=dict(color="white", width=2)),
        text=[pct(v) if v >= 0.07 else "" for v in mix[family]], textposition="inside", insidetextanchor="middle",
        textfont=dict(color="white" if family in ("MIT", "GPL family") else A.INK["primary"]),
        hovertemplate=f"%{{y}} stars<br>{family}: %{{x:.1%}}<extra></extra>",
    ))
show(fig, height=320, barmode="stack", title="Licence by star range", xaxis_tickformat=".0%",
     xaxis_range=[0, 1], yaxis=dict(autorange="reversed", title="stars"))
st.caption("Popular repos almost always declare a licence. 'Custom / unrecognised' is a licence file GitHub "
           "could not match to a standard licence, often a dual or source-available licence.")

st.subheader("Topics")
topics = data.topics[data.topics["id"].isin(repos["id"])]
left, right = st.columns(2)
with left:
    n = st.slider("Topics to show", 10, 30, 15, key="n_topics")
    top = (topics["topic"].value_counts().head(n) / len(repos)).sort_values()
    fig = go.Figure(go.Bar(
        x=top.values, y=top.index, orientation="h", marker_color=A.CATEGORICAL[0],
        text=[f"{v:.1%}" for v in top.values], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x:.1%} of repos<extra></extra>",
    ))
    show(fig, height=max(320, 24 * n + 80), title="Most common topics", xaxis_tickformat=".0%", showlegend=False)
    has = repos.groupby("bucket", observed=True)["n_topics"].apply(lambda s: (s > 0).mean())
    st.caption("Repos with at least one topic: " +
               " · ".join(f"{A.BUCKET_LABELS[b]} **{pct(v)}**" for b, v in has.items()))
with right:
    min_support = st.slider("Minimum repos created 2023-2025", 5, 30, 12, key="min_support")
    t = topics.merge(repos[["id", "created_year"]], on="id")
    recent_n = (repos["created_year"] >= 2023).sum()
    older_n = (repos["created_year"] < 2023).sum()
    if recent_n == 0 or older_n == 0:
        st.caption("Rising topics need repos from both before and after 2023 in the year filter.")
    else:
        counts = pd.DataFrame({
            "recent": t[t["created_year"] >= 2023]["topic"].value_counts(),
            "older": t[t["created_year"] < 2023]["topic"].value_counts(),
        }).fillna(0)
        counts["share_recent"] = counts["recent"] / recent_n
        counts["share_older"] = counts["older"] / older_n
        counts["lift"] = (counts["share_recent"] + 0.001) / (counts["share_older"] + 0.001)
        rising = counts[counts["recent"] >= min_support].sort_values("lift", ascending=False).head(n).iloc[::-1]
        if rising.empty:
            st.caption("No topic reaches the minimum support. Lower it with the slider.")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=[v for a, b in zip(rising["share_older"], rising["share_recent"]) for v in (a, b, None)],
                y=[v for name in rising.index for v in (name, name, None)],
                mode="lines", line=dict(color=A.INK["grid"], width=4), hoverinfo="skip", showlegend=False,
            ))
            fig.add_trace(go.Scatter(
                x=rising["share_older"], y=rising.index, mode="markers", name="created 2012-2022",
                marker=dict(size=11, color=A.MUTED, line=dict(color="white", width=2)),
                hovertemplate="%{y}<br>2012-2022: %{x:.1%}<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=rising["share_recent"], y=rising.index, mode="markers", name="created 2023-2025",
                marker=dict(size=12, color=A.CATEGORICAL[1], line=dict(color="white", width=2)),
                hovertemplate="%{y}<br>2023-2025: %{x:.1%}<extra></extra>",
            ))
            show(fig, height=max(320, 24 * len(rising) + 80), title="Rising topics (share of repos)",
                 xaxis_tickformat=".0%", yaxis=dict(gridcolor="rgba(0,0,0,0)"))
