"""Small presentation helpers: a minimal Markdown subset and label lookups."""

from __future__ import annotations

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])")
_CODE = re.compile(r"`([^`]+?)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BARE_URL = re.compile(r"(?<![\"'>=])(https?://[^\s<>\"')]+)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")


def _inline(text: str) -> str:
    """Escape, then re-introduce the handful of inline marks we support."""
    out = escape(text)
    out = _LINK.sub(
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', out
    )
    out = _BARE_URL.sub(
        r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', out
    )
    out = _CODE.sub(r"<code>\1</code>", out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    return out


@register.filter(name="richtext")
def richtext(value: str | None) -> str:
    """Render the light Markdown used in the imported notes.

    Supports headings, bullet lists, paragraphs, bold, italic, inline code and
    links. Everything else is escaped -- the input is trusted but there is no
    reason to hand it raw to the browser.
    """
    if not value:
        return ""

    html: list[str] = []
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            html.append("</ul>")
            in_list = False

    for raw_line in str(value).replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            close_list()
            continue

        heading = _HEADING.match(line)
        if heading:
            close_list()
            level = min(len(heading.group(1)) + 2, 6)
            html.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        bullet = _BULLET.match(line)
        if bullet:
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{_inline(bullet.group(1))}</li>")
            continue

        close_list()
        html.append(f"<p>{_inline(line)}</p>")

    close_list()
    return mark_safe("".join(html))


@register.filter(name="pluralize_fr")
def pluralize_fr(count, suffixes: str = "s") -> str:
    """French pluralisation: 0 and 1 stay singular."""
    try:
        value = abs(int(count))
    except (TypeError, ValueError):
        return ""
    singular, _, plural = suffixes.partition(",")
    if not plural:
        singular, plural = "", singular
    return singular if value < 2 else plural


@register.simple_tag
def percent_of(value, total) -> str:
    """Percentage as a CSS-safe string.

    Returned pre-formatted because a French locale would render a float as
    "37,5", and `width: 37,5%` is dropped by the browser.
    """
    try:
        total = float(total)
        if total <= 0:
            return "0"
        return f"{float(value) / total * 100:.2f}"
    except (TypeError, ValueError):
        return "0"
