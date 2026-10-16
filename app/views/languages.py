import plotly.graph_objects as go
import streamlit as st

from components.charts import LANG_STYLE, empty_state, show
from components.data import current_filters, filter_population, load
from wgs import analysis as A

data = load()
f = current_filters()

st.title("Languages")
st.markdown(
    "Which languages do popular repos use, and how has that changed? These charts use the **census**: "
    "the number of GitHub repos with 10+ stars in every star range × creation year × language, so "
    "they describe GitHub itself rather than the balanced sample."
)
st.caption(f"Showing: {f.describe()}")

pop = filter_population(data.population, f)
if pop.empty:
    empty_state()
    st.stop()

by_year = pop.groupby(["year", "lang_group"])["population"].sum().unstack(fill_value=0)
share = by_year.div(by_year.sum(axis=1), axis=0)

options = [l for l in LANG_STYLE if l in share.columns]
default = [l for l in ["Python", "TypeScript", "JavaScript", "Go", "Rust", "Java"] if l in options]
picked = st.multiselect("Highlight languages (up to 6)", options, default=default, max_selections=6)

fig = go.Figure()
for lang in share.columns:
    if lang in picked:
        continue
    fig.add_trace(go.Scatter(
        x=share.index, y=share[lang], mode="lines", name=lang, showlegend=False,
        line=dict(color=A.MUTED, width=1), opacity=0.7,
        hovertemplate=f"{lang}<br>%{{x}}: %{{y:.1%}}<extra></extra>",
    ))
for lang in picked:
    style = LANG_STYLE[lang]
    fig.add_trace(go.Scatter(
        x=share.index, y=share[lang], mode="lines", name=lang,
        line=dict(color=style["color"], dash=style["dash"], width=2.5),
        hovertemplate=f"{lang}<br>%{{x}}: %{{y:.1%}}<extra></extra>",
    ))
buckets = ", ".join(A.BUCKET_LABELS[b] for b in f.buckets)
show(fig, height=440, title=f"Share of new repos by language (stars: {buckets})",
     yaxis_tickformat=".0%", xaxis_title="creation year", hovermode="x unified",
     xaxis_dtick=1 if len(share) <= 8 else 2)
st.caption("Grey lines: languages not highlighted. Each line is that language's share of all repos created "
           "that year within the selected star ranges.")

st.subheader("Hit rate")
st.markdown(
    "Of the repos in a language that collected at least 10 stars, what share went on to 1,000+? "
    "This separates *how often a language succeeds* from *how much is written in it*."
)
pop_all = filter_population(data.population, f, use_buckets=False)
totals = pop_all.groupby("lang_group")["population"].sum()
hits = pop_all[pop_all["bucket"].isin(["1k-10k", "10k+"])].groupby("lang_group")["population"].sum()
rate = (hits / totals).dropna().sort_values()
avg = hits.sum() / totals.sum()
fig = go.Figure(go.Bar(
    x=rate.values, y=rate.index, orientation="h", marker_color=A.CATEGORICAL[0],
    text=[f"{v:.1%}" for v in rate.values], textposition="outside", cliponaxis=False,
    hovertemplate="%{y}: %{x:.2%} reached 1,000+ stars<extra></extra>",
))
fig.add_vline(x=avg, line=dict(color=A.INK["muted"], width=1))
fig.add_annotation(x=avg, y=1, yref="paper", text=f"average {avg:.1%}", showarrow=False,
                   xanchor="left", xshift=4, font=dict(size=11, color=A.INK["muted"]))
show(fig, height=max(300, 28 * len(rate) + 80), title="Share of 10+ star repos that reached 1,000+ stars",
     xaxis_tickformat=".1%", showlegend=False)
st.caption(f"Repos created {f.years[0]}–{f.years[1]}. Very recent years lower every rate, since young repos "
           "have had less time to grow; the star-range filter does not apply here.")

with st.expander("Table: census counts by language"):
    table = pop_all.pivot_table(index="lang_group", columns="bucket", values="population", aggfunc="sum")
    table = table[[b for b in A.BUCKET_ORDER if b in table.columns]].rename(columns=A.BUCKET_LABELS)
    table["hit rate"] = rate
    st.dataframe(table.sort_values("hit rate", ascending=False), column_config={
        "hit rate": st.column_config.NumberColumn(format="percent"),
    })
