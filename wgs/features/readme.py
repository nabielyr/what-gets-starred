"""Turn README text into numeric features.

Pure functions only: the same code scores READMEs in the build pipeline and in the
dashboard's README checker.

Images are split into three kinds, because they mean very different things:
- badges: status shields (build passing, version, downloads...);
- decorations: contributor avatars, sponsor logos, tiny icons, star-history widgets;
- content images: screenshots, diagrams, GIF demos, video thumbnails.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser

from markdown_it import MarkdownIt

MARKDOWN_EXTENSIONS = {"md", "markdown", "mdown", "mkd", "mkdn", ""}

BADGE_PATTERN = re.compile(
    r"shields\.io|badgen\.net|badge\.fury\.io|badges?\b|/badge\.svg|travis-ci\.(org|com)|"
    r"circleci\.com|codecov\.io|coveralls\.io|ci\.appveyor\.com|poser\.pugx\.org|"
    r"goreportcard\.com|pepy\.tech|snyk\.io/test|codacy\.com|codeclimate\.com|"
    r"sonarcloud\.io|deepsource\.io|bestpractices\.(dev|coreinfrastructure)|"
    r"herokucdn\.com/deploy|vercel\.com/button|deploy-button",
    re.IGNORECASE,
)
DECORATION_PATTERN = re.compile(
    r"avatars\d*\.githubusercontent\.com|github\.com/[^/]+\.png|gravatar\.com|"
    r"opencollective\.com|contrib\.rocks|contributors-img|allcontributors|"
    r"google\.com/s2/favicons|edent\.github\.io/supertinyicons|crowdin-static|"
    r"star-history\.com|starchart\.cc|repobeats|github-readme-stats|komarev\.com|"
    r"skillicons\.dev|simpleicons\.org|cdn\.jsdelivr\.net/gh/devicons|"
    r"/flags?/|buymeacoffee\.com|ko-fi\.com|liberapay\.com|paypal(objects)?\.com",
    re.IGNORECASE,
)
DEMO_LINK_PATTERN = re.compile(
    r"\.github\.io|\.vercel\.app|\.netlify\.app|\.streamlit\.app|huggingface\.co/spaces|"
    r"\.herokuapp\.com|\.pages\.dev|\.surge\.sh|\.glitch\.me|codesandbox\.io|stackblitz\.com|"
    r"replit\.com|colab\.research\.google\.com|youtube\.com/watch|youtu\.be/|vimeo\.com|asciinema\.org",
    re.IGNORECASE,
)
DEMO_TEXT_PATTERN = re.compile(r"\b(demo|live|try it|playground|preview)\b", re.IGNORECASE)

SECTION_PATTERNS = {
    "has_install": re.compile(
        r"\b(install(ation|ing)?|setup|set up|getting started|quick ?start|download|build(ing)?)\b", re.I
    ),
    "has_usage": re.compile(r"\b(usage|how to use|examples?|tutorial|guide|quick ?start)\b", re.I),
    "has_demo_section": re.compile(r"\b(demo|screenshots?|preview|showcase|gallery)\b", re.I),
    "has_contributing": re.compile(r"\b(contribut(e|ing|ors?|ion)|development)\b", re.I),
    "has_license_section": re.compile(r"\blicen[cs]e\b", re.I),
}

URL_PATTERN = re.compile(r"https?://\S+")
WORD_PATTERN = re.compile(r"[^\W_][\w'’-]*", re.UNICODE)
RST_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+<>:._])\1{2,}\s*$")

_md = MarkdownIt("commonmark").enable("table").enable("strikethrough")


@dataclass
class ReadmeFeatures:
    readme_format: str = "none"  # md | rst | other | none
    readme_words: int = 0
    readme_headings: int = 0
    readme_max_depth: int = 0  # deepest heading level used (1-6)
    readme_images: int = 0  # content images (screenshots, diagrams, GIFs)
    readme_gifs: int = 0
    readme_badges: int = 0
    readme_decorations: int = 0  # avatars, logos, icons, widgets
    readme_links: int = 0
    readme_code_blocks: int = 0
    readme_tables: int = 0
    has_install: bool = False
    has_usage: bool = False
    has_demo_section: bool = False
    has_contributing: bool = False
    has_license_section: bool = False
    has_demo_link: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Image:
    src: str
    width: int | None = None


class _HTMLCollector(HTMLParser):
    """Pulls images, links, headings and visible text out of raw HTML in a README."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images: list[_Image] = []
        self.links: list[str] = []
        self.headings: list[tuple[int, str]] = []
        self.text: list[str] = []
        self._heading: int | None = None
        self._heading_text: list[str] = []
        self._skip = 0  # inside <script>/<style>/<pre>/<code>

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "img" and a.get("src"):
            self.images.append(_Image(a["src"], _int(a.get("width"))))
        elif tag in ("source",) and a.get("srcset"):
            pass  # <picture> sources duplicate the <img> fallback
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif re.fullmatch(r"h[1-6]", tag):
            self._heading, self._heading_text = int(tag[1]), []
        elif tag in ("script", "style", "pre", "code"):
            self._skip += 1

    def handle_endtag(self, tag):
        if re.fullmatch(r"h[1-6]", tag) and self._heading:
            self.headings.append((self._heading, " ".join(self._heading_text)))
            self._heading = None
        elif tag in ("script", "style", "pre", "code") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        self.text.append(data)
        if self._heading:
            self._heading_text.append(data)


def _int(value) -> int | None:
    try:
        return int(str(value).rstrip("px"))
    except (TypeError, ValueError):
        return None


def readme_format(path: str | None) -> str:
    if path is None:
        return "md"
    name = path.rsplit("/", 1)[-1].lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in MARKDOWN_EXTENSIONS:
        return "md"
    if ext in ("rst", "rest"):
        return "rst"
    return "other"


def classify_image(src: str, width: int | None = None) -> str:
    if BADGE_PATTERN.search(src):
        return "badge"
    if DECORATION_PATTERN.search(src) or (width is not None and width <= 40):
        return "decoration"
    return "content"


def _count_words(text: str) -> int:
    return len(WORD_PATTERN.findall(URL_PATTERN.sub(" ", text)))


def _apply_images(features: ReadmeFeatures, images: list[_Image]) -> None:
    for img in images:
        kind = classify_image(img.src, img.width)
        if kind == "badge":
            features.readme_badges += 1
        elif kind == "decoration":
            features.readme_decorations += 1
        else:
            features.readme_images += 1
            if re.search(r"\.gif(\?|#|$)", img.src, re.I):
                features.readme_gifs += 1


def _apply_sections(features: ReadmeFeatures, headings: list[str]) -> None:
    for name, pattern in SECTION_PATTERNS.items():
        if any(pattern.search(h) for h in headings):
            setattr(features, name, True)


def _markdown_features(text: str) -> ReadmeFeatures:
    f = ReadmeFeatures(readme_format="md")
    images: list[_Image] = []
    links: list[tuple[str, str]] = []  # (href, link text)
    headings: list[tuple[int, str]] = []
    words = 0

    tokens = _md.parse(text)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            level = int(tok.tag[1])
            headings.append((level, tokens[i + 1].content if i + 1 < len(tokens) else ""))
        elif tok.type in ("fence", "code_block"):
            f.readme_code_blocks += 1
        elif tok.type == "table_open":
            f.readme_tables += 1
        elif tok.type == "html_block":
            html = _HTMLCollector()
            html.feed(tok.content)
            images += html.images
            links += [(href, "") for href in html.links]
            headings += html.headings
            words += _count_words(" ".join(html.text))
        elif tok.type == "inline":
            words += _inline_features(tok.children or [], images, links)

    _apply_images(f, images)
    f.readme_words = words
    f.readme_headings = len(headings)
    f.readme_max_depth = max((lvl for lvl, _ in headings), default=0)
    f.readme_links = len(links)
    f.has_demo_link = any(DEMO_LINK_PATTERN.search(h) or DEMO_TEXT_PATTERN.search(t) for h, t in links)
    _apply_sections(f, [h for _, h in headings])
    return f


def _inline_features(children, images: list[_Image], links: list[tuple[str, str]]) -> int:
    """Collect images and links from an inline token; returns its word count."""
    words = 0
    open_link: list[str] | None = None
    link_text: list[str] = []
    for child in children:
        if child.type == "image":
            images.append(_Image(child.attrGet("src") or ""))
        elif child.type == "link_open":
            open_link, link_text = [child.attrGet("href") or ""], []
        elif child.type == "link_close" and open_link is not None:
            links.append((open_link[0], " ".join(link_text)))
            open_link = None
        elif child.type == "html_inline":
            html = _HTMLCollector()
            html.feed(child.content)
            images.extend(html.images)
            links.extend((href, "") for href in html.links)
        elif child.type in ("text", "code_inline"):
            words += _count_words(child.content)
            if open_link is not None:
                link_text.append(child.content)
    return words


def _rst_features(text: str) -> ReadmeFeatures:
    f = ReadmeFeatures(readme_format="rst")
    lines = text.splitlines()
    headings: list[str] = []
    levels: dict[str, int] = {}
    body: list[str] = []
    images: list[_Image] = []
    in_directive = False
    for i, line in enumerate(lines):
        prev = lines[i - 1].strip() if i else ""
        match = RST_UNDERLINE.match(line)
        if match and prev and not RST_UNDERLINE.match(prev) and len(line.strip()) >= len(prev):
            levels.setdefault(match.group(1), len(levels) + 1)
            headings.append(prev)
            if body and body[-1] == prev:
                body.pop()
            continue
        directive = re.match(r"\s*\.\.\s+(\|[^|]+\|\s+)?(image|figure)::\s*(\S+)", line)
        if directive:
            images.append(_Image(directive.group(3)))
            in_directive = True
            continue
        if re.match(r"\s*\.\.\s+(code-block|code|sourcecode)::", line):
            f.readme_code_blocks += 1
            in_directive = True
            continue
        if line.rstrip().endswith("::"):
            # "Install it with pip::" is prose that introduces a literal block.
            f.readme_code_blocks += 1
            body.append(line.rstrip()[:-2])
            in_directive = True
            continue
        if in_directive and (line.startswith((" ", "\t")) or not line.strip()):
            continue  # directive options and code block body
        in_directive = False
        if not re.match(r"\s*\.\.\s", line):
            body.append(line.strip())

    links = re.findall(r"`([^`<]*)<([^>]+)>`_", text) + [("", u) for u in URL_PATTERN.findall(text)]
    _apply_images(f, images)
    f.readme_words = _count_words(" ".join(re.sub(r"`([^`<]*)<[^>]+>`_", r"\1", b) for b in body))
    f.readme_headings = len(headings)
    f.readme_max_depth = max(levels.values(), default=0)
    f.readme_links = len(links)
    f.has_demo_link = any(DEMO_LINK_PATTERN.search(u) or DEMO_TEXT_PATTERN.search(t) for t, u in links)
    _apply_sections(f, headings)
    return f


def _plain_features(text: str) -> ReadmeFeatures:
    urls = URL_PATTERN.findall(text)
    return ReadmeFeatures(
        readme_format="other",
        readme_words=_count_words(text),
        readme_links=len(urls),
        has_demo_link=any(DEMO_LINK_PATTERN.search(u) for u in urls),
    )


def extract_readme_features(text: str | None, path: str | None = None) -> dict:
    """Features of one README. `path` decides the format; None means Markdown."""
    if not text or not text.strip():
        return ReadmeFeatures().to_dict()
    fmt = readme_format(path)
    if fmt == "md":
        return _markdown_features(text).to_dict()
    if fmt == "rst":
        return _rst_features(text).to_dict()
    return _plain_features(text).to_dict()


FEATURE_NAMES = [name for name in ReadmeFeatures().to_dict() if name != "readme_format"]
