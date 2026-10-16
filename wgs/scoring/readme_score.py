"""Score a README against the READMEs of popular repositories.

The score compares each README feature with the distribution among repos with 1,000+ stars
(`readme_benchmarks.json`, built by the feature pipeline) and weights the checks by how much each
feature helped the README-only model (`models/readme_rf.json`). It describes how much a README
*looks like* those of popular projects; it does not predict stars.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from wgs.config import ROOT
from wgs.features.readme import extract_readme_features

MODEL_DIR = ROOT / "models"
BENCHMARKS = ROOT / "data" / "processed" / "readme_benchmarks.json"


@dataclass(frozen=True)
class Check:
    feature: str
    label: str
    kind: str  # "count" or "flag"
    advice: str


CHECKS = [
    Check("readme_words", "Length (words)", "count",
          "Explain what the project does, who it is for and why it exists. Popular READMEs are a few hundred words."),
    Check("readme_headings", "Headings", "count",
          "Split the README into sections (Features, Installation, Usage, Contributing...) so readers can scan it."),
    Check("readme_links", "Links", "count",
          "Link to documentation, a website, package registries, examples and related projects."),
    Check("readme_code_blocks", "Code blocks", "count",
          "Show install commands and a minimal usage example in fenced code blocks."),
    Check("readme_images", "Screenshots / diagrams", "count",
          "Add a screenshot, diagram or GIF that shows the project in action."),
    Check("readme_badges", "Badges", "count",
          "Add a few status badges (build, version, licence) near the top."),
    Check("has_install", "Installation section", "flag",
          "Add an Installation / Getting started section."),
    Check("has_usage", "Usage / examples section", "flag",
          "Add a Usage or Examples section."),
    Check("has_contributing", "Contributing section", "flag",
          "Tell people how to contribute or report issues."),
    Check("has_license_section", "Licence section", "flag",
          "Mention the licence in the README."),
    Check("has_demo_link", "Link to a live demo or video", "flag",
          "Link a live demo, playground or short video if the project has a visible result."),
]


@dataclass
class CheckResult:
    feature: str
    label: str
    value: float
    typical: float  # median among popular repos (count) or share that has it (flag)
    score: float  # 0..1
    weight: float
    status: str  # "good" | "improve" | "missing"
    advice: str


@dataclass
class ReadmeReport:
    score: float  # 0..100
    features: dict
    checks: list[CheckResult]
    resemblance: dict[str, float] | None  # bucket -> probability from the README-only model


def load_benchmarks(path: Path = BENCHMARKS) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_model_meta(model_dir: Path = MODEL_DIR) -> dict:
    return json.loads((Path(model_dir) / "readme_rf.json").read_text(encoding="utf-8"))


def load_model(model_dir: Path = MODEL_DIR):
    import joblib

    model = joblib.load(Path(model_dir) / "readme_rf.joblib")
    model.n_jobs = 1  # one README at a time: thread start-up would cost more than it saves
    return model


def count_score(value: float, p25: float, p50: float) -> float:
    """1 at or above the popular median, 0.5 at the popular 25th percentile, linear below."""
    if p50 <= 0:
        return 1.0
    if value >= p50:
        return 1.0
    if p25 > 0 and value >= p25:
        return 0.5 + 0.5 * (value - p25) / (p50 - p25) if p50 > p25 else 1.0
    floor = p25 if p25 > 0 else p50
    return max(0.0, 0.5 * value / floor)


def check_weights(importance: dict[str, float]) -> dict[str, float]:
    """Model importance, softened (square root) so one feature cannot dominate, plus a floor."""
    raw = {c.feature: math.sqrt(max(importance.get(c.feature, 0.0), 0.0)) + 0.05 for c in CHECKS}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def score_readme(
    text: str,
    benchmarks: dict,
    model_meta: dict,
    model=None,
    group: str = "popular",
) -> ReadmeReport:
    features = extract_readme_features(text)
    ref = benchmarks["groups"][group]
    weights = check_weights(model_meta.get("permutation_importance", {}))

    results = []
    for c in CHECKS:
        value = float(features[c.feature])
        if c.kind == "count":
            q = ref["numeric"][c.feature]
            typical = q["p50"]
            s = count_score(value, q["p25"], q["p50"])
        else:
            typical = ref["flags"][c.feature]
            s = 1.0 if value else 0.0
        status = "good" if s >= 0.999 else ("improve" if s > 0 else "missing")
        results.append(CheckResult(c.feature, c.label, value, typical, s, weights[c.feature], status, c.advice))

    total = 100 * sum(r.score * r.weight for r in results)

    resemblance = None
    if model is not None:
        x = pd.DataFrame([{f: float(features[f]) for f in model_meta["features"]}])
        proba = model.predict_proba(x)[0]
        resemblance = {cls: float(p) for cls, p in zip(model.classes_, proba)}

    return ReadmeReport(score=round(total, 1), features=features, checks=results, resemblance=resemblance)
