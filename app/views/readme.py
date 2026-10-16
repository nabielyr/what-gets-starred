import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from scipy import stats

from components.charts import BUCKET_COLORS, empty_state, show
from components.data import current_filters, filter_repos, load
from wgs import analysis as A

data = load()
f = current_filters()

st.title("README")
st.markdown(
    "Do README length, images, badges and structure go together with stars? List repos (awesome lists, "
    "link collections) and repos without a README are left out, since they say nothing about how a "
    "project presents itself."
)
st.caption(f"Showing: {f.describe()}")

repos = filter_repos(data.repos, f)
software = repos[(repos["readme_format"] != "none") & ~repos["is_list_repo"]]
if software.empty:
    empty_state()
    st.stop()

COUNTS = {
    "readme_words": "Words", "readme_headings": "Headings", "readme_links": "Links",
    "readme_images": "Content images", "readme_badges": "Badges", "readme_code_blocks": "Code blocks",
    "readme_gifs": "GIFs",
}
FLAGS = {
    "has_install": "Install section", "has_usage": "Usage / examples", "has_demo_section": "Demo / screenshots",
    "has_demo_link": "Live demo or video link", "has_contributing": "Contributing section",
    "has_license_section": "Licence section",
}

feature = st.segmented_control("Feature", list(COUNTS), format_func=COUNTS.get, default="readme_words")
feature = feature or "readme_words"

fig = go.Figure()
for b, g in software.groupby("bucket", observed=True):
    label = A.BUCKET_LABELS[b]
    fig.add_trace(go.Box(
        x=np.log10(g[feature] + 1), name=label, marker_color=BUCKET_COLORS[label], boxpoints=False,
        line=dict(width=1.5), fillcolor=BUCKET_COLORS[label],
        customdata=g[feature], hoverinfo="skip",
    ))
med = software.groupby("bucket", observed=True)[feature].median()
ticks = [0, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]
top = software[feature].quantile(0.97)
ticks = [t for t in ticks if t <= max(top, 2) * 1.5]
show(fig, height=330, title=f"{COUNTS[feature]} per README by star range (median and interquartile range)",
     xaxis=dict(tickvals=np.log10(np.array(ticks) + 1), ticktext=[f"{t:,}" for t in ticks], title="log scale"),
     showlegend=False)
st.caption("Medians: " + " · ".join(f"{A.BUCKET_LABELS[b]} stars **{v:,.0f}**" for b, v in med.items()))

st.subheader("Which elements do READMEs include?")
prev = software.groupby("bucket", observed=True)[list(FLAGS)].mean().T
fig = go.Figure()
y_labels = [FLAGS[k] for k in prev.index]
fig.add_trace(go.Scatter(
    x=np.ravel(np.column_stack([prev.min(axis=1), prev.max(axis=1), [None] * len(prev)])),
    y=np.ravel(np.column_stack([y_labels, y_labels, [None] * len(prev)])),
    mode="lines", line=dict(color=A.INK["grid"], width=4), hoverinfo="skip", showlegend=False,
))
for b in prev.columns:
    label = A.BUCKET_LABELS[b]
    fig.add_trace(go.Scatter(
        x=prev[b], y=y_labels, mode="markers", name=f"{label} stars",
        marker=dict(size=13, color=BUCKET_COLORS[label], line=dict(color="white", width=2)),
        hovertemplate=f"{label} stars<br>%{{y}}: %{{x:.0%}}<extra></extra>",
    ))
show(fig, height=360, title="Share of READMEs with each element", xaxis_tickformat=".0%",
     yaxis=dict(autorange="reversed", gridcolor="rgba(0,0,0,0)"))

st.subheader("How strong is the relationship?")
st.markdown(
    "The **overall** correlation compares all repos at once, so it mostly reflects the gap between tiny "
    "and huge projects. The **within star range** correlation asks the harder question: among repos of "
    "similar popularity, do richer READMEs have more stars?"
)


@st.cache_data(show_spinner="Computing correlations...")
def correlations(key: tuple, df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col, name in {**COUNTS, **FLAGS}.items():
        x = df[col].astype(float)
        overall = stats.spearmanr(x, df["log_stars"]).statistic if df["bucket"].nunique() > 1 else np.nan
        within = [stats.spearmanr(g[col].astype(float), g["log_stars"]).statistic
                  for _, g in df.groupby("bucket", observed=True) if len(g) > 20 and g[col].nunique() > 1]
        rows.append({"feature": name, "overall": overall, "within": np.nanmean(within) if within else np.nan})
    return pd.DataFrame(rows).sort_values("overall", na_position="first")


corr = correlations((f.buckets, f.languages, f.years), software[list(COUNTS) + list(FLAGS) + ["log_stars", "bucket"]])
fig = go.Figure()
fig.add_trace(go.Scatter(
    x=corr["overall"], y=corr["feature"], mode="markers", name="overall",
    marker=dict(size=12, color=A.CATEGORICAL[0], line=dict(color="white", width=2)),
    hovertemplate="%{y}<br>overall ρ = %{x:.2f}<extra></extra>",
))
fig.add_trace(go.Scatter(
    x=corr["within"], y=corr["feature"], mode="markers", name="within star range",
    marker=dict(size=11, symbol="diamond", color=A.CATEGORICAL[1], line=dict(color="white", width=2)),
    hovertemplate="%{y}<br>within star range ρ = %{x:.2f}<extra></extra>",
))
fig.add_vline(x=0, line=dict(color=A.INK["muted"], width=1))
show(fig, height=480, title="Spearman correlation with log(stars)", xaxis_range=[-0.2, 0.75],
     yaxis=dict(gridcolor="rgba(0,0,0,0)"))
if len(f.buckets) == 1:
    st.caption("Only one star range is selected, so the overall correlation is not shown.")
st.info(
    "Within a star range, README features are only weakly related to stars. Part of the overall "
    "relationship is probably reverse causality: popular projects attract contributors who write docs, "
    "add CI badges and link documentation sites.", icon=":material/lightbulb:",
)
