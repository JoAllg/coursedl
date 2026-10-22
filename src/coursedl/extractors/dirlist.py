"""`coursedl-dir:` HTTP directory listings: SVN web views and Apache/nginx autoindex pages.

No listing format is parsed. A link counts if its URL (without query) lies below the
listing's own URL: ending in `/` means subfolder, anything else a file. That covers
mod_dav_svn, SVN web views with `name?` links and autoindex pages (`?C=N;O=D` sort links
point at the listing itself and are dropped).
"""

from pathlib import PurePosixPath
from typing import Any, final, override
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from yt_dlp.utils import ExtractorError

from coursedl.extractors.common import CoursedlIE, Entry, norm_url
from coursedl.extractors.html import Link, content_links


def _strip_query(url: str) -> str:
    return urlunsplit(urlsplit(url)._replace(query="", fragment=""))


def crawl_listing(
    ie: CoursedlIE, url: str, folder: PurePosixPath, link: Link | None = None
) -> list[Entry]:
    """Files below the listing at `url`, placed under `folder`.

    The remote key is the URL, so a file listed twice (e.g. via a page and a listing) is
    one archive entry. Versions come from HEAD; PROPFIND is not used because SVN web views
    may refuse it (CSRF check). A subfolder or file that fails is reported as an unhandled
    link (warned about once), the rest of the listing still counts.
    """
    html, base = ie.fetch(url, "Downloading listing")
    base = _strip_query(base)
    if not base.endswith("/"):
        base += "/"
    entries: list[Entry] = []
    seen: set[str] = set()
    for a in content_links(html):
        target = _strip_query(urljoin(base, a.href))
        if not target.startswith(base) or target == base or target in seen:
            continue
        seen.add(target)
        rel = PurePosixPath(unquote(target.removeprefix(base)))
        if target.endswith("/"):
            try:
                entries += crawl_listing(ie, target, folder / rel, link)
            except ExtractorError as e:
                ie.report_warning(f"listing {target} failed: {e.orig_msg}")
                ie.ctx.unhandled[norm_url(target)] = base
        elif (head := ie.head(target)) is not None:
            entries.append(
                ie.file_entry(target, target, folder / rel, head.version, head.timestamp, link)
            )
        else:
            ie.ctx.unhandled[norm_url(target)] = base
    return entries


@final
class CoursedlDirIE(CoursedlIE):
    PREFIX = "coursedl-dir:"
    _VALID_URL = r"coursedl-dir:https?://.+"

    @override
    def _real_extract(self, url: str) -> Any:
        root = url.removeprefix(self.PREFIX)
        return self._result(crawl_listing(self, root, PurePosixPath()), root)
