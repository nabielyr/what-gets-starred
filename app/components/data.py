"""Cached data access for the dashboard. Everything is read from data/processed and models/;
the app never calls the GitHub API."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from wgs import analysis as A
from wgs.scoring import readme_score


@st.cache_data(show_spinner="Loading data...")
def load() -> A.Data:
    data = A.load()
    data.repos["weight"] = A.stratum_weights(data.repos, data.population)
    data.repos["bucket_label"] = data.repos["bucket"].astype(str).map(A.BUCKET_LABELS)
    return data


@st.cache_resource(show_spinner="Loading the README model...")
def readme_model():
    return readme_score.load_model()


@st.cache_data
def readme_model_meta() -> dict:
    return readme_score.load_model_meta()


@dataclass(frozen=True)
class Filters:
    buckets: tuple[str, ...]
    languages: tuple[str, ...]
    years: tuple[int, int]

    @property
    def is_default(self) -> bool:
        return (
            len(self.buckets) == len(A.BUCKET_ORDER)
            and len(self.languages) == len(LANGUAGES)
            and self.years == (YEARS[0], YEARS[-1])
        )

    def describe(self) -> str:
        if self.is_default:
            return "All repos"
        parts = []
        if len(self.buckets) != len(A.BUCKET_ORDER):
            parts.append("stars " + ", ".join(A.BUCKET_LABELS[b] for b in self.buckets))
        if len(self.languages) != len(LANGUAGES):
            parts.append(", ".join(self.languages) if len(self.languages) <= 4 else f"{len(self.languages)} languages")
        if self.years != (YEARS[0], YEARS[-1]):
            parts.append(f"created {self.years[0]}–{self.years[1]}")
        return " · ".join(parts)


LANGUAGES = [
    "Python", "JavaScript", "TypeScript", "Java", "Go", "Rust", "C++", "C", "C#", "PHP", "Ruby", "Swift", "Other",
]
YEARS = list(range(2012, 2026))


def current_filters() -> Filters:
    return st.session_state.get("filters", Filters(tuple(A.BUCKET_ORDER), tuple(LANGUAGES), (YEARS[0], YEARS[-1])))


def filter_repos(repos: pd.DataFrame, f: Filters) -> pd.DataFrame:
    return repos[
        repos["bucket"].astype(str).isin(f.buckets)
        & repos["lang_group"].isin(f.languages)
        & repos["created_year"].between(*f.years)
    ]


def filter_population(pop: pd.DataFrame, f: Filters, use_buckets: bool = True) -> pd.DataFrame:
    mask = pop["lang_group"].isin(f.languages) & pop["year"].between(*f.years)
    if use_buckets:
        mask &= pop["bucket"].isin(f.buckets)
    return pop[mask]
