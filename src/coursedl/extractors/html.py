"""Links from the content area of an HTML page, and login forms (stdlib parser only)."""

import re
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from typing import final, override

from coursedl.config import SELECTOR_RE

# Elements without an end tag.
VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})  # fmt: skip
# Never content, even inside the content area.
SKIP = frozenset(["nav", "footer", "script", "style", "template"])
# Tried in order when the source sets no `content_selector`: the first one present on the
# page is the content area. Covers HTML5 pages (`<main>`), MoinMoin and many older sites
# (`#content`), and everything else (`<body>`).
AUTO_SELECTORS = ["main", "#content", "body"]
SCOPES = {
    **dict.fromkeys(["tr", "td", "th", "tbody", "thead", "tfoot"], frozenset({"table"})),
    "li": frozenset({"ul", "ol"}),
}
# h1 is the page title on typical lecture pages, so folders start at h2.
FOLDER_HEADINGS = {"h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


@dataclass(frozen=True)
class Link:
    href: str
    text: str
    folders: tuple[str, ...]  # headings and list labels above the link
    # Text of the enclosing list item or table row before its first link, e.g. "Lecture 9,
    # Tuesday 23 June 2026 (Sorting)". Often names the unit.
    item: str = ""
    # Text of the row up to and including this link, link texts too: "2 Class 2: Filters
    # Recording". Names the unit of links like "Recording".
    row: str = ""


@dataclass
class _ListItem:
    """An open <li> or <tr>."""

    tag: str
    depth: int  # stack length while the element is open
    # Text up to the first link: "Week 1 (<a>Sheet</a> | <a>PDF</a>):" gives "Week 1".
    text: list[str] = field(default_factory=list)
    text_done: bool = False
    row: list[str] = field(default_factory=list)
    # Set when a nested list starts; None while there is none (yet).
    label: str | None = None
    # Links seen before the sublist started. They belong to this item (e.g. the exercise
    # sheet of a week), so they get the label too once it is known.
    early_links: list[int] = field(default_factory=list)


@final
class _Parser(HTMLParser):
    def __init__(self, selector: str):
        super().__init__(convert_charrefs=True)
        m = SELECTOR_RE.fullmatch(selector)
        assert m
        self.want_tag, self.want_id, self.want_cls = m["tag"], m["id"], m["cls"]
        # Open elements: (tag, inside content area, inside a skipped element).
        self.stack: list[tuple[str, bool, bool]] = []
        self.found = False
        self.links: list[Link] = []
        self.headings: dict[int, str] = {}
        self.items: list[_ListItem] = []
        self._heading: tuple[int, list[str]] | None = None
        self._link: tuple[str, list[str]] | None = None

    def _state(self) -> tuple[bool, bool]:
        return (self.stack[-1][1], self.stack[-1][2]) if self.stack else (False, False)

    def _matches(self, tag: str, attrs: dict[str, str | None]) -> bool:
        return (
            (not self.want_tag or tag == self.want_tag)
            and (not self.want_id or attrs.get("id") == self.want_id)
            and (not self.want_cls or self.want_cls in (attrs.get("class") or "").split())
        )

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        inside, skip = self._state()
        if not inside and self._matches(tag, a):
            inside = self.found = True
        skip = skip or tag in SKIP
        if inside and not skip:
            if tag in FOLDER_HEADINGS:
                self._heading = (FOLDER_HEADINGS[tag], [])
            elif tag == "a" and a.get("href"):
                self._link = (a["href"] or "", [])
                if self.items:
                    self.items[-1].text_done = True
            elif (
                tag in ("ul", "ol")
                and self.items
                and self.items[-1].tag == "li"
                and self.items[-1].label is None
            ):
                # A list item that contains a sublist labels it, like a heading.
                item = self.items[-1]
                item.label = _clean(item.text).rstrip(" :(-|").strip()
                if item.label:
                    for i in item.early_links:
                        link = self.links[i]
                        self.links[i] = replace(link, folders=(*link.folders, item.label))
        if tag not in VOID:
            self.stack.append((tag, inside, skip))
        if tag in ("td", "th") and self.items:
            self.items[-1].row.append(" ")
            if not self.items[-1].text_done:
                self.items[-1].text.append(" ")  # cells of a row: "Tu 2026-10-20 Introduction"
        if tag in ("li", "tr") and inside and not skip:
            self.items.append(_ListItem(tag, len(self.stack)))

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._link:
            href, text = self._link
            item = _clean(self.items[-1].text).rstrip(" :(-|").strip() if self.items else ""
            row = _clean(self.items[-1].row) if self.items else ""
            self.links.append(Link(href, _clean(text), self._current_headings(), item, row))
            self._link = None
            if self.items and self.items[-1].label is None:
                self.items[-1].early_links.append(len(self.links) - 1)
        elif tag in FOLDER_HEADINGS and self._heading:
            level, text = self._heading
            self.headings = {k: v for k, v in self.headings.items() if k < level}
            if title := _clean(text):
                self.headings[level] = title
            self._heading = None
        # Tolerate unclosed elements (<p>, <li>, …): pop up to the matching open tag. Like
        # browsers, a stray </tr> or </li> doesn't close anything outside its own table or
        # list (seen on a course page whose layout table holds the content area).
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
            if self.stack[i][0] in SCOPES.get(tag, ()):
                break
        self.items = [item for item in self.items if item.depth <= len(self.stack)]

    @override
    def handle_data(self, data: str) -> None:
        for part in (self._heading, self._link):
            if part:
                part[1].append(data)
        if self.items and self.items[-1].label is None:
            self.items[-1].row.append(data)
            if not self.items[-1].text_done:
                self.items[-1].text.append(data)

    def _current_headings(self) -> tuple[str, ...]:
        labels = (item.label for item in self.items if item.label)
        return (*(self.headings[k] for k in sorted(self.headings)), *labels)


def _clean(parts: list[str]) -> str:
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def content_links(html: str, selector: str | None = None) -> list[Link]:
    """Links in the page's content area, each with the folders above it (the h2-h6
    headings, then the labels of enclosing list items that contain a sublist) and the text
    of its list item or table row.

    Navigation is left out: only the content area counts, and `<nav>`/`<footer>` inside
    it are skipped too.
    """
    for sel in [selector] if selector else AUTO_SELECTORS:
        p = _Parser(sel)
        p.feed(html)
        p.close()
        if p.found:
            return p.links
    return []


@dataclass
class Form:
    action: str
    method: str
    fields: dict[str, str]
    password_field: str
    username_field: str | None


@final
class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[tuple[dict[str, str | None], list[dict[str, str | None]]]] = []
        self._open = False

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "form":
            self.forms.append((dict(attrs), []))
            self._open = True
        elif tag in ("input", "button") and self._open:
            a = dict(attrs)
            # Types are case-insensitive; a <button> without one submits the form.
            a["type"] = (a.get("type") or ("submit" if tag == "button" else "text")).lower()
            self.forms[-1][1].append(a)

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._open = False


def password_form(html: str) -> Form | None:
    """The first form with a password field, with the values its other fields would
    send (hidden inputs, the first submit button)."""
    p = _FormParser()
    p.feed(html)
    p.close()
    for attrs, inputs in p.forms:
        named = [i for i in inputs if i.get("name")]
        password = next((i["name"] for i in named if i.get("type") == "password"), None)
        if not password:
            continue
        fields: dict[str, str] = {}
        submitted = False
        for i in named:
            kind = i["type"]
            if kind == "submit":
                if submitted:
                    continue
                submitted = True
            elif kind in ("checkbox", "radio") and "checked" not in i:
                continue
            fields[i["name"] or ""] = i.get("value") or ""
        user = next((i["name"] for i in named if i["type"] in ("text", "email")), None)
        return Form(
            attrs.get("action") or "",
            (attrs.get("method") or "get").lower(),
            fields,
            password,
            user,
        )
    return None
