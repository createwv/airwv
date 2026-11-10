"""docs/GLOSSARY.md: never "source" alone in page text, nav or popups (on its own it reads as a
pollution source). Scans the templates and the strings in static/*.js, so text built in the
browser (map popups, cards) is covered too; no server, node or network."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "src/airwv/web"
PAGES = sorted((WEB / "templates").glob("*.html")) + sorted((WEB / "static").glob("*.js"))  # vendor/ is a subfolder

# what the word may be attached to: the glossary's "data source", plus plain English and terms of art
QUALIFIED = re.compile(r"""\b(?:data|pollution|water|open|point|major)[ -]sources?\b
                           | \bnew\ source\ review\b | \bsources?[ -]water\b | \bsource\ of\ pollution\b
                           | "source" """, re.I | re.X)
# not after - / # . _ (open-source, /api/sources, #ev-sources) or before = (a URL's source=outdoor)
BARE = re.compile(r"(?<![-/#._\w])sources?(?![=\w])", re.I)
SHOWN_ATTRS = {"title", "placeholder", "aria-label", "alt", "data-label"}
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)


class _Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.scripts, self._in = [], [], None

    def handle_starttag(self, tag, attrs):
        self._in = tag if tag in ("script", "style") else None
        self.text += [v for k, v in attrs if k in SHOWN_ATTRS and v]

    def handle_endtag(self, tag):
        self._in = None

    def handle_data(self, data):
        (self.scripts if self._in == "script" else [] if self._in else self.text).append(data)


def _js_literals(src: str) -> list[str]:
    """Every string and template literal in src, with `${…}` blanked (the literals inside one come
    back separately). Regex literals are skipped: /"/g would otherwise open a string."""
    found = []

    def quoted(i):
        q, j = src[i], i + 1
        while j < len(src) and src[j] not in (q, "\n"):
            j += 2 if src[j] == "\\" else 1
        if src[j:j + 1] == q:
            found.append(src[i + 1:j])
            return j + 1
        return j

    def template(i):
        parts, j = [], i + 1
        while j < len(src) and src[j] != "`":
            if src[j] == "\\":
                j += 2
            elif src.startswith("${", j):
                parts.append(" ")
                j = code(j + 2, nested=True)
            else:
                parts.append(src[j])
                j += 1
        found.append("".join(parts))
        return j + 1

    def regex(i):
        j, in_class = i + 1, False
        while j < len(src) and src[j] != "\n":
            if src[j] == "\\":
                j += 1
            elif src[j] in "[]":
                in_class = src[j] == "["
            elif src[j] == "/" and not in_class:
                return j + 1
            j += 1
        return i + 1  # a division after all

    def code(i, nested=False):
        depth = 0
        while i < len(src):
            c = src[i]
            if src.startswith("//", i):
                i = src.find("\n", i)
                i = len(src) if i < 0 else i
            elif src.startswith("/*", i):
                i = src.find("*/", i)
                i = len(src) if i < 0 else i + 2
            elif c == "/" and src[:i].rstrip().endswith((*"(,=:[!&|?{};", "return")):
                i = regex(i)
            elif c in "'\"":
                i = quoted(i)
            elif c == "`":
                i = template(i)
            else:
                if c == "}" and nested and depth == 0:
                    return i + 1
                depth += (c == "{") - (c == "}")
                i += 1
        return i

    code(0)
    return found


def _visible(markup: str) -> tuple[list[str], list[str]]:
    p = _Visible()
    p.feed(JINJA.sub(" ", markup))
    p.close()
    return p.text, p.scripts


def _bare(text: str) -> list[str]:
    text = " ".join(text.split())
    text = QUALIFIED.sub(lambda m: "_" * len(m[0]), text)
    return [text[max(m.start() - 30, 0):m.end() + 30] for m in BARE.finditer(text)]


def _js_chunks(js: str) -> list[str]:
    # a lowercase one-word string is a key or id, not copy
    return [" ".join(_visible(s)[0]) for s in _js_literals(js) if not re.fullmatch(r"[a-z_-]+", s)]


def _hits(path_name: str, content: str) -> list[str]:
    if path_name.endswith(".js"):
        chunks = _js_chunks(content)
    else:
        text, scripts = _visible(content)
        chunks = [" ".join(text)]
        chunks += [c for js in scripts for c in _js_chunks(js)]
    return [f"{path_name}: …{hit}…" for chunk in chunks for hit in _bare(chunk)]


def test_no_bare_source_in_page_text_nav_or_popups():
    hits = [h for path in PAGES for h in _hits(path.name, path.read_text(encoding="utf-8"))]
    assert not hits, 'say "data source" (or "facility"), per docs/GLOSSARY.md:\n' + "\n".join(hits)


@pytest.mark.parametrize("name,content", [
    ("a.html", "<p>Source: {{ prov.source }}</p>"),
    ("a.html", '<a href="/data">sources</a>'),
    ("a.html", '<input placeholder="nearby source…">'),
    ("a.html", "<script>el.innerHTML = '<b>Source</b>';</script>"),
    ("a.js", "x.innerHTML = `<b>Sources (${c.sources.length})</b>`;"),
    ("a.js", "bindPopup(`${ok ? `<i>the source</i>` : ''}`);"),
    ("a.js", "h = '<span title=\"Source\">x</span>';"),
    ("a.js", """o = `<option value="${s.replace(/"/g,'&quot;')}">`; p = '<b>Source</b>';"""),
])
def test_scanner_catches_bare_source(name, content):
    assert _hits(name, content), content


@pytest.mark.parametrize("name,content", [
    ("a.html", "<b>Data</b> sources, <i>pollution sources</i>, open-source, Open source, source-water"),
    ("a.html", '<script src="/static/sources.js"></script><div id="ev-sources"></div>'),
    ("a.html", "{% for s in dc.sources %}{{ s.source }}{% endfor %} {# the source #}"),
    ("a.html", '<style>.source { color: red }</style>Never "source" alone'),
    ("a.js", "// the source\n/* source */ const sources = p.source; fetch('/api/sources');"),
    ("a.js", "$('ev-sources'); u = `&source=outdoor&k=${s.source}`; o.sources = 'source';"),
])
def test_scanner_leaves_code_and_qualified_uses_alone(name, content):
    assert _hits(name, content) == [], content
