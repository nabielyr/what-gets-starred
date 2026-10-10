from wgs.features.readme import classify_image, extract_readme_features, readme_format

RICH_MARKDOWN = """\
<p align="center">
  <img src="docs/logo.png" width="200">
</p>
<h1 align="center">Rocket</h1>

[![Build](https://github.com/o/rocket/actions/workflows/ci.yml/badge.svg)](https://github.com/o/rocket/actions)
[![PyPI](https://img.shields.io/pypi/v/rocket)](https://pypi.org/project/rocket)
[![codecov](https://codecov.io/gh/o/rocket/branch/main/graph/badge.svg)](https://codecov.io)

Rocket launches your scripts into space, really fast.

![Demo](docs/demo.gif)

**[Live demo](https://o.github.io/rocket)**

## Installation

```bash
pip install rocket
```

## Usage

    rocket launch --fast

| Flag | Meaning |
|------|---------|
| fast | go fast |

## Contributors

<a href="https://github.com/o/rocket/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=o/rocket" />
</a>
<img src="https://avatars.githubusercontent.com/u/1?v=4" width="50">

## License

MIT
"""


def test_rich_markdown_readme():
    f = extract_readme_features(RICH_MARKDOWN, "README.md")
    assert f["readme_format"] == "md"
    assert f["readme_headings"] == 5  # <h1> + 4 x "##"
    assert f["readme_max_depth"] == 2
    assert f["readme_badges"] == 3
    assert f["readme_images"] == 2  # logo + demo gif
    assert f["readme_gifs"] == 1
    assert f["readme_decorations"] == 2  # contrib.rocks + avatar
    assert f["readme_code_blocks"] == 2  # fenced + indented
    assert f["readme_tables"] == 1
    assert f["has_install"] and f["has_usage"] and f["has_contributing"] and f["has_license_section"]
    assert not f["has_demo_section"]
    assert f["has_demo_link"]


def test_word_count_ignores_code_urls_and_markup():
    text = "# Title here\n\nOne two three https://example.com/a/b four.\n\n```\nnot counted at all\n```\n"
    assert extract_readme_features(text, "README.md")["readme_words"] == 6


def test_setext_headings():
    text = "Project\n=======\n\nIntro text.\n\nGetting started\n---------------\n\nRun it.\n"
    f = extract_readme_features(text, "README.md")
    assert f["readme_headings"] == 2
    assert f["has_install"]


def test_demo_section_and_video_link():
    text = "# App\n\n## Screenshots\n\n![screen](shot.png)\n\nWatch [the video](https://youtu.be/abc).\n"
    f = extract_readme_features(text)
    assert f["has_demo_section"] and f["has_demo_link"]
    assert f["readme_images"] == 1 and f["readme_gifs"] == 0


def test_tiny_html_images_are_decorations():
    text = '<img src="icons/python.svg" width="32"> <img src="shot.png" width="600">\n'
    f = extract_readme_features(text)
    assert f["readme_decorations"] == 1 and f["readme_images"] == 1


def test_rst_readme():
    text = """\
Rocket
======

.. image:: https://img.shields.io/pypi/v/rocket.svg
    :target: https://pypi.org/project/rocket

Rocket launches scripts. See the `docs <https://rocket.readthedocs.io>`_.

Installation
------------

Install it with pip::

    pip install rocket

Usage
-----

.. code-block:: python

    import rocket
"""
    f = extract_readme_features(text, "README.rst")
    assert f["readme_format"] == "rst"
    assert f["readme_headings"] == 3
    assert f["readme_max_depth"] == 2
    assert f["readme_badges"] == 1 and f["readme_images"] == 0
    assert f["readme_code_blocks"] == 2
    assert f["has_install"] and f["has_usage"]
    assert f["readme_words"] == 10


def test_plain_text_readme():
    f = extract_readme_features("A tiny tool. Demo at https://tool.vercel.app\n", "README.txt")
    assert f["readme_format"] == "other"
    assert f["readme_words"] == 5
    assert f["has_demo_link"] and f["readme_links"] == 1


def test_empty_or_missing_readme():
    for text in (None, "", "   \n"):
        f = extract_readme_features(text, "README.md")
        assert f["readme_format"] == "none" and f["readme_words"] == 0 and not f["has_install"]


def test_readme_format():
    assert readme_format("README.md") == readme_format("docs/README") == readme_format(None) == "md"
    assert readme_format("README.rst") == "rst"
    assert readme_format("README.adoc") == "other"


def test_classify_image():
    assert classify_image("https://img.shields.io/badge/x-y-green") == "badge"
    assert classify_image("https://badgen.net/npm/v/x") == "badge"
    assert classify_image("https://avatars.githubusercontent.com/u/1") == "decoration"
    assert classify_image("https://www.google.com/s2/favicons?domain=x.com") == "decoration"
    assert classify_image("https://api.star-history.com/svg?repos=o/r") == "decoration"
    assert classify_image("https://user-images.githubusercontent.com/1/screen.png") == "content"
    assert classify_image("docs/architecture.svg") == "content"
