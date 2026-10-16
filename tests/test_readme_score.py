import pytest

from wgs.scoring.readme_score import (
    CHECKS,
    check_weights,
    count_score,
    load_benchmarks,
    load_model,
    load_model_meta,
    score_readme,
)

GOOD = """\
# Rocket

[![CI](https://github.com/o/r/actions/workflows/ci.yml/badge.svg)](https://github.com/o/r/actions)
[![PyPI](https://img.shields.io/pypi/v/rocket)](https://pypi.org/project/rocket)

Rocket launches your scripts into space. """ + "It is fast, small and well tested. " * 60 + """

![screenshot](docs/screenshot.png)

Try the [live demo](https://rocket.vercel.app) or read the [docs](https://rocket.dev).
""" + "".join(f"- [Example {i}](https://rocket.dev/examples/{i})\n" for i in range(25)) + """

## Features
## Installation

```bash
pip install rocket
```

## Usage

```python
import rocket
rocket.launch()
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
"""


def test_count_score():
    assert count_score(10, 5, 10) == 1.0
    assert count_score(20, 5, 10) == 1.0
    assert count_score(5, 5, 10) == pytest.approx(0.5)
    assert count_score(7.5, 5, 10) == pytest.approx(0.75)
    assert count_score(2.5, 5, 10) == pytest.approx(0.25)
    assert count_score(0, 0, 2) == 0.0
    assert count_score(0, 0, 0) == 1.0  # not typical even for popular repos: no penalty


def test_check_weights_are_normalised_and_softened():
    w = check_weights(load_model_meta()["permutation_importance"])
    assert sum(w.values()) == pytest.approx(1.0)
    assert set(w) == {c.feature for c in CHECKS}
    assert w["readme_links"] == max(w.values())  # the most important feature in the model
    assert w["readme_links"] < 0.25  # sqrt keeps it from dominating the score
    assert min(w.values()) > 0


def test_score_readme_with_real_benchmarks_and_model():
    bench, meta, model = load_benchmarks(), load_model_meta(), load_model()
    good = score_readme(GOOD, bench, meta, model)
    bare = score_readme("# tool\n\nA tool.\n", bench, meta, model)
    empty = score_readme("", bench, meta, model)

    assert good.score > 80
    assert bare.score < 30
    assert empty.score == 0
    assert {r.status for r in bare.checks} >= {"missing"}
    assert sum(good.resemblance.values()) == pytest.approx(1.0)
    assert set(good.resemblance) == {"10-100", "100-1k", "1k-10k", "10k+"}
    # a rich README should resemble popular buckets more than a one-liner does
    popular = lambda r: r.resemblance["1k-10k"] + r.resemblance["10k+"]  # noqa: E731
    assert popular(good) > popular(bare)
