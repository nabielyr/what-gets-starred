"""Plotly styling shared by every chart, using the same validated palettes as the notebooks."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from wgs import analysis as A

FONT = "Inter, -apple-system, Segoe UI, Roboto, sans-serif"

TEMPLATE = go.layout.Template(
    layout=dict(
        font=dict(family=FONT, color=A.INK["primary"], size=13),
        paper_bgcolor=A.SURFACE,
        plot_bgcolor=A.SURFACE,
        colorway=A.CATEGORICAL,
        title=dict(x=0, xanchor="left", font=dict(size=16, color=A.INK["primary"])),
        margin=dict(l=16, r=24, t=64, b=16),
        hoverlabel=dict(bgcolor="white", bordercolor=A.INK["grid"], font=dict(family=FONT, size=12, color=A.INK["primary"])),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title_text="",
                    font=dict(color=A.INK["secondary"])),
        xaxis=dict(gridcolor=A.INK["grid"], linecolor=A.INK["grid"], zeroline=False, ticks="", automargin=True,
                   tickfont=dict(color=A.INK["secondary"]), title_font=dict(color=A.INK["secondary"], size=12)),
        yaxis=dict(gridcolor=A.INK["grid"], linecolor=A.INK["grid"], zeroline=False, ticks="", automargin=True,
                   tickfont=dict(color=A.INK["secondary"]), title_font=dict(color=A.INK["secondary"], size=12)),
        bargap=0.35,
    )
)

BUCKET_COLORS = {A.BUCKET_LABELS[b]: c for b, c in A.BUCKET_COLORS.items()}

# Colour follows the language, never its rank. Eight hues are available, so five languages
# reuse a hue with a dashed line (hue + dash = composite encoding).
_LANG_ORDER = ["Python", "TypeScript", "JavaScript", "Go", "Rust", "Java", "C++", "C#",
               "C", "PHP", "Ruby", "Swift", "Other"]
LANG_STYLE = {
    lang: {"color": A.CATEGORICAL[i % 8], "dash": "solid" if i < 8 else "dash"}
    for i, lang in enumerate(_LANG_ORDER)
}


def show(fig: go.Figure, height: int = 400, legend_below: bool = False, **layout) -> None:
    fig.update_layout(template=TEMPLATE, height=height, plot_bgcolor=A.SURFACE, paper_bgcolor=A.SURFACE, **layout)
    if legend_below:  # narrow charts: keep the legend clear of the title
        fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0))
    st.plotly_chart(fig, theme=None, config={"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]})


def empty_state(message: str = "No repos match the current filters. Widen them in the sidebar.") -> None:
    st.info(message, icon=":material/filter_alt_off:")


def pct(x: float, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%"
