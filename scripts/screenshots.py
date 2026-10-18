"""Take screenshots of every dashboard page (used for the README).

Start the app first:   streamlit run app/streamlit_app.py --server.port 8599
Then:                  python scripts/screenshots.py [--base http://localhost:8599] [--out docs/screenshots]

Requires Playwright (`pip install playwright && python -m playwright install chromium`).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = {
    "overview": "",
    "languages": "languages",
    "readme": "readme",
    "activity": "activity",
    "growth": "growth",
    "topics": "topics",
    "checker": "checker",
    "methodology": "methodology",
}


def wait_for_app(page, expect_charts: bool = True) -> None:
    page.wait_for_selector("[data-testid='stAppViewContainer']", timeout=60_000)
    page.wait_for_selector("h1", timeout=180_000)  # the page body has rendered
    # The first visit after a restart loads the data cache; wait until no spinner is left.
    page.wait_for_function(
        "() => !document.querySelector('[data-testid=\"stStatusWidget\"], [data-testid=\"stSpinner\"]')",
        timeout=180_000,
    )
    if expect_charts:
        page.wait_for_selector(".js-plotly-plot .main-svg", timeout=180_000)
    page.wait_for_timeout(3000)  # let Plotly finish its transitions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8599")
    parser.add_argument("--out", default="docs/screenshots")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--full-page", action="store_true", help="capture the whole page, not just the viewport")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": args.width, "height": 900}, device_scale_factor=1)
        for name, path in PAGES.items():
            page.goto(f"{args.base}/{path}")
            wait_for_app(page, expect_charts=name not in ("checker", "methodology"))
            if name == "checker":
                page.get_by_role("button", name="Try an example").click()
                wait_for_app(page)
                # Show the result (score, resemblance and checks) rather than the input box.
                page.get_by_text("README score").first.evaluate("e => e.scrollIntoView({block: 'start'})")
                page.mouse.move(800, 450)
                page.mouse.wheel(0, -150)
                page.wait_for_timeout(1500)
            page.screenshot(path=str(out / f"{name}.png"), full_page=args.full_page)
            print("saved", out / f"{name}.png")
        browser.close()


if __name__ == "__main__":
    main()
