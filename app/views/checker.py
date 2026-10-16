import plotly.graph_objects as go
import streamlit as st

from components.charts import BUCKET_COLORS, show
from components.data import load, readme_model, readme_model_meta
from wgs import analysis as A
from wgs.scoring.readme_score import score_readme

data = load()

st.title("README checker")
st.markdown(
    "Paste a README (Markdown) or upload a `.md` file. It is compared with the READMEs of repos that have "
    "**1,000+ stars**, and the checks are weighted by how much each feature mattered to the README-only "
    "model. Nothing is stored or sent anywhere."
)

EXAMPLE = """# fastcsv

[![CI](https://github.com/you/fastcsv/actions/workflows/ci.yml/badge.svg)](https://github.com/you/fastcsv/actions)
[![PyPI](https://img.shields.io/pypi/v/fastcsv)](https://pypi.org/project/fastcsv)

**fastcsv** reads and writes CSV files 10× faster than the standard library, with the same API.
It is written in Rust and ships as a pure-Python wheel for Linux, macOS and Windows.

![benchmark](docs/benchmark.png)

## Installation

```bash
pip install fastcsv
```

## Usage

```python
import fastcsv
rows = fastcsv.read("data.csv")
```

See the [documentation](https://fastcsv.dev) and the [examples](https://fastcsv.dev/examples).

## Contributing

Issues and pull requests are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
"""

if "readme_text" not in st.session_state:
    st.session_state["readme_text"] = ""


def use_example() -> None:
    st.session_state["readme_text"] = EXAMPLE


def clear() -> None:
    st.session_state["readme_text"] = ""


def load_upload() -> None:
    """Read the file once, when it is uploaded, so later edits and Clear are not overwritten."""
    uploaded = st.session_state.get("readme_upload")
    if uploaded is not None:
        st.session_state["readme_text"] = uploaded.getvalue().decode("utf-8", errors="replace")


st.file_uploader("Upload README.md", type=["md", "markdown", "txt"], key="readme_upload", on_change=load_upload)

st.text_area("README content", key="readme_text", height=280, placeholder="# My project\n\nWhat it does...")
b1, b2, _ = st.columns([1, 1, 5])
b1.button("Try an example", on_click=use_example, icon=":material/auto_awesome:", width="stretch")
b2.button("Clear", on_click=clear, icon=":material/close:", width="stretch")

text = st.session_state["readme_text"]
if not text.strip():
    st.caption("Paste a README above to see its score.")
    st.stop()

report = score_readme(text, data.benchmarks, readme_model_meta(), readme_model())

st.divider()
left, right = st.columns([1, 2], gap="large")
with left:
    verdict = "Strong" if report.score >= 80 else "Decent" if report.score >= 55 else "Needs work"
    st.metric("README score", f"{report.score:.0f} / 100", border=True,
              help="Weighted share of the checks passed, relative to the READMEs of 1,000+ star repos.")
    st.progress(min(report.score / 100, 1.0), text=verdict)
    f = report.features
    st.caption(f"{f['readme_words']:,} words · {f['readme_headings']} headings · {f['readme_links']} links · "
               f"{f['readme_images']} images · {f['readme_badges']} badges · {f['readme_code_blocks']} code blocks")
with right:
    probs = {A.BUCKET_LABELS[b]: report.resemblance[b] for b in A.BUCKET_ORDER}
    fig = go.Figure(go.Bar(
        x=list(probs), y=list(probs.values()), marker_color=[BUCKET_COLORS[k] for k in probs],
        text=[f"{v:.0%}" for v in probs.values()], textposition="outside", cliponaxis=False,
        hovertemplate="%{x} stars: %{y:.0%}<extra></extra>",
    ))
    show(fig, height=260, title="Which star range's READMEs does this resemble?", yaxis_tickformat=".0%",
         yaxis_range=[0, 1], xaxis_title="stars", showlegend=False, margin=dict(t=48, b=8, l=8, r=8))
    st.caption("From a random forest trained on README features only (macro-F1 0.46 vs 0.25 by chance). "
               "It describes resemblance, **not** the chance of getting stars: on GitHub, 10–99 star repos "
               "outnumber 10k+ repos about 440 to 1, and what a project does matters far more than its README.")

st.subheader("Checks")
ICON = {"good": ":material/check_circle:", "improve": ":material/error:", "missing": ":material/cancel:"}
COLOR = {"good": "green", "improve": "orange", "missing": "red"}
for r in sorted(report.checks, key=lambda r: (r.status == "good", -r.weight)):
    with st.container(border=True):
        c1, c2 = st.columns([3, 2])
        typical = (f"popular READMEs: median {r.typical:,.0f}" if r.feature.startswith("readme_")
                   else f"{r.typical:.0%} of popular READMEs have one")
        yours = f"{r.value:,.0f}" if r.feature.startswith("readme_") else ("yes" if r.value else "no")
        c1.markdown(f":{COLOR[r.status]}[{ICON[r.status]}] **{r.label}**: {yours}")
        c2.caption(typical)
        if r.status != "good":
            st.caption(r.advice)

with st.expander("How the score works"):
    st.markdown(
        """
- **Counts** (words, headings, links, code blocks, images, badges) score 1 at or above the median of
  1,000+ star READMEs, 0.5 at their 25th percentile, and proportionally less below it.
- **Sections** (installation, usage, contributing, licence, demo link) score 1 if present.
- **Weights** are the square root of each feature's permutation importance in the README-only model,
  plus a small floor, so links count most but no single feature dominates.
- Benchmarks exclude awesome lists and link collections.
"""
    )
