"""Turn the body of an e-mail into plain text a person (or a model) can read.

Mails carry a lot that is not text: markup, tracking links, and invisible padding
characters that marketing mails use to fill the preview line. This module removes
those and nothing else; it never rewrites words, numbers or dates.
"""

import re
import unicodedata
from html.parser import HTMLParser

DEFAULT_MAX_CHARS = 6000
# A plain-text part with fewer letters and digits than this is not taken as the body when
# the mail has an HTML part ("Your mail client cannot show HTML ..." and the like).
MIN_PLAIN_CHARS = 40

# Characters that print nothing: zero-width characters, direction marks, the word joiner
# and invisible operators, the byte order mark, the combining grapheme joiner (U+034F),
# the soft hyphen, the Mongolian vowel separator and the braille blank.
_INVISIBLE = {
    *range(0x200B, 0x2010),
    *range(0x2060, 0x2065),
    0xFEFF,
    0x034F,
    0x00AD,
    0x180E,
    0x2800,
}
# Spaces that must not break a line (no-break, narrow no-break, figure space).
_SPACES = {0x00A0, 0x202F, 0x2007}
_TRANSLATE: dict[int, str | None] = {
    **dict.fromkeys(_INVISIBLE),
    **dict.fromkeys(_SPACES, " "),
}

_MARKDOWN_LINK = re.compile(r"\[([^\]\n]*)\]\(\s*https?://[^)\s]*\s*\)", re.IGNORECASE)
_BARE_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_URL_END = ".,;:!?)]}"
_EMPTY_BRACKETS = re.compile(r"<\s*>|\(\s*\)|\[\s*\]")
_SPACE_RUN = re.compile(r"[ \t\f\v]+")
_BLANK_RUN = re.compile(r"\n{3,}")

_SKIPPED = {"script", "style", "head"}
# A line break before and after these.
_BLOCKS = {
    "address", "article", "aside", "blockquote", "body", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
    "h6", "header", "hr", "html", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "tbody", "tfoot", "thead", "tr", "ul",
}  # fmt: skip
# A table cell ends a line; a row of cells is then not split by blank lines.
_CELLS = {"td", "th"}


class _TextExtractor(HTMLParser):
    """Visible text of an HTML document; link targets are never read."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED:
            self._skip += 1
        elif tag in _BLOCKS and not self._skip:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self._skip = max(0, self._skip - 1)
        elif (tag in _BLOCKS or tag in _CELLS) and not self._skip:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - broken markup must not stop a run; keep what was read
        pass
    return parser.text()


def _drop_control(text: str) -> str:
    return "".join(
        c for c in text if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cs", "Co")
    )


def _tidy(text: str) -> str:
    """URLs, invisible characters and whitespace; see the module docstring."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_TRANSLATE)
    text = _drop_control(text)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    # Punctuation that ended the sentence, not the address, stays: "(see https://x.y/z)."
    text = _BARE_URL.sub(lambda m: m.group()[len(m.group().rstrip(_URL_END)) :], text)
    text = _EMPTY_BRACKETS.sub("", text)
    lines = (_SPACE_RUN.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def _substance(text: str) -> int:
    return sum(1 for c in text if c.isalnum())


def _cut(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    boundary = text.rfind("\n", 0, max_chars + 1)
    if boundary > 0:
        return text[:boundary].rstrip(), True
    return text[:max_chars].rstrip(), True


def clean_text(
    raw_plain: str | None, raw_html: str | None, *, max_chars: int = DEFAULT_MAX_CHARS
) -> tuple[str, bool]:
    """The cleaned body and whether it was cut at ``max_chars``.

    The plain-text part is used when it has real content; otherwise the HTML part is
    converted. When neither has, the one with more letters and digits is used.
    """
    plain = _tidy(raw_plain) if raw_plain else ""
    if _substance(plain) >= MIN_PLAIN_CHARS or not raw_html:
        return _cut(plain, max_chars)
    html = _tidy(html_to_text(raw_html))
    return _cut(html if _substance(html) > _substance(plain) else plain, max_chars)
