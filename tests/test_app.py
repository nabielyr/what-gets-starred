"""Smoke tests: every dashboard page renders without exceptions, under several filter states."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
PAGES = ["overview", "languages", "readme", "activity", "growth", "topics", "checker", "methodology"]
TIMEOUT = 120


def run(page: str, **state) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    if page != "overview":
        at.switch_page(f"views/{page}.py")
        at.run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_default_filters(page):
    at = run(page)
    assert not at.exception, at.exception
    assert at.title, "every page has a title"


@pytest.mark.parametrize("page", ["overview", "languages", "readme", "activity", "growth", "topics"])
def test_page_handles_narrow_filters(page):
    at = run(page, f_buckets=["10k+"], f_languages=["Rust"], f_years=(2020, 2022))
    assert not at.exception, at.exception


@pytest.mark.parametrize("page", ["overview", "readme", "activity", "growth", "topics"])
def test_page_handles_empty_filters(page):
    at = run(page, f_buckets=[], f_languages=[])
    assert not at.exception, at.exception
    assert at.info, "an empty-state message is shown"


def test_readme_checker_scores_the_example():
    at = run("checker")
    at.button[0].click().run()  # "Try an example"
    assert not at.exception, at.exception
    score = [m for m in at.metric if m.label == "README score"]
    # The example is deliberately decent but improvable, so the page shows advice.
    assert score and 30 < float(score[0].value.split("/")[0]) < 90
    assert any("documentation" in c.value or "sections" in c.value for c in at.caption)
