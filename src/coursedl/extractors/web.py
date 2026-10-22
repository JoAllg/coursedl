"""`coursedl-web:` a course web page, optionally with subpages.

Only links in the page's content area count (see `html.content_links`). Each link is, in
this order: ignored, a YouTube video, a directory listing (`dir_regex`), a file
(`link_regex`), a subpage to crawl (`follow_regex`), or reported as unhandled.
"""

import itertools
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, final, override
from urllib.parse import urljoin, urlsplit, urlunsplit

from yt_dlp.utils import ExtractorError, sanitize_filename

from coursedl.extractors.common import CoursedlIE, Entry, link_fields, norm_url, url_name
from coursedl.extractors.dirlist import crawl_listing
from coursedl.extractors.html import Link, content_links

# Never material: other schemes, wiki actions (edit, login, attachments, …), login pages.
BUILTIN_IGNORE = re.compile(r"^(?!https?:)|[?&;]action=|/log(in|out)\b", re.IGNORECASE)
YOUTUBE = re.compile(
    r"^https?://(www\.|m\.)?(youtube\.com/(watch|embed/|shorts/|live/|playlist)|youtu\.be/)"
)
# Google Drive file links ("…/file/d/<id>/view", "open?id=", "uc?id="). yt-dlp's own
# Drive extractor only handles videos, so these become plain files: the direct download
# URL (`confirm=t` skips the virus-scan page for large files) sends the real file name in
# Content-Disposition and Last-Modified, so changes are detected like for other files.
GOOGLE_DRIVE = re.compile(
    r"^https://drive\.google\.com/(?:file/d/|open\?(?:.*&)?id=|uc\?(?:.*&)?id=)([\w-]+)"
)
DRIVE_DOWNLOAD = "https://drive.usercontent.google.com/download?id={}&export=download&confirm=t"


@final
class CoursedlWebIE(CoursedlIE):
    PREFIX = "coursedl-web:"
    _VALID_URL = r"coursedl-web:https?://.+"

    @override
    def _real_extract(self, url: str) -> Any:
        root = url.removeprefix(self.PREFIX)
        return self._result(self._crawl(root, PurePosixPath(), 0, _Crawl()), root)

    def _crawl(self, url: str, folder: PurePosixPath, depth: int, crawl: _Crawl) -> list[Entry]:
        src = self.ctx.source
        html, page = self.fetch(url)
        seen = crawl.seen
        seen.add(norm_url(page))
        entries: list[Entry] = []
        for link in content_links(html, src.content_selector):
            target = urlunsplit(urlsplit(urljoin(page, link.href))._replace(fragment=""))
            key = norm_url(target)
            crawl.key, crawl.page = key, page
            if key in seen:
                continue
            seen.add(key)
            if BUILTIN_IGNORE.search(target):
                continue
            if src.ignore_regex and src.ignore_regex.search(target):
                continue
            here = folder
            if src.folders == "headings":
                here = here.joinpath(*(sanitize_filename(h) for h in link.folders))
            path = urlsplit(target).path
            try:
                if src.youtube and YOUTUBE.match(target):
                    entries.append(
                        {
                            **self.url_result(target, url_transparent=True),
                            **link_fields(link),
                            "cd_dir": str(here),
                            "cd_kind": "video",
                        }
                    )
                elif src.dir_regex and src.dir_regex.search(target):
                    entries += crawl_listing(self, target, here / url_name(target), link)
                elif src.google_drive and (m := GOOGLE_DRIVE.match(target)):
                    entries += self._file(DRIVE_DOWNLOAD.format(m[1]), here / m[1], link, crawl)
                elif src.link_regex.search(path):
                    entries += self._file(target, here / url_name(target), link, crawl)
                elif (
                    src.follow_regex
                    and src.follow_regex.search(target)
                    and depth < src.follow_depth
                ):
                    sub = here / sanitize_filename(url_name(target))
                    entries += self._crawl(target, sub, depth + 1, crawl)
                elif src.warn_unhandled:
                    self.ctx.unhandled[key] = page
            except ExtractorError as e:
                # A broken subpage or listing is reported once like a broken file link,
                # instead of failing the whole source on every run.
                self.report_warning(f"{target} failed: {e.orig_msg}")
                self.ctx.unhandled[key] = page
        return entries

    def _file(self, url: str, path: PurePosixPath, link: Link, crawl: _Crawl) -> list[Entry]:
        """A file entry, named after Content-Disposition if the server sends one, else
        `path`'s name. A failing HEAD makes the link unhandled: warned about once instead
        of failing every run."""
        if (head := self.head(url)) is None:
            self.ctx.unhandled[crawl.key] = crawl.page
            return []
        if head.filename:
            path = path.with_name(sanitize_filename(head.filename))
        if path in crawl.paths:
            # Two different files with the same name in one folder (e.g. a video with and
            # without subtitles): the link text tells them apart.
            path = path.with_stem(f"{path.stem} ({sanitize_filename(link.text)})")
        base = path
        for i in itertools.count(2):
            if path not in crawl.paths:
                break
            path = base.with_stem(f"{base.stem}-{i}")
        crawl.paths.add(path)
        return [self.file_entry(url, url, path, head.version, head.timestamp, link)]


@dataclass
class _Crawl:
    """State of one crawl over a page and its subpages."""

    seen: set[str] = field(default_factory=set)  # normalised URLs already handled
    paths: set[PurePosixPath] = field(default_factory=set)  # cd_paths already used
    # The link being handled, for reporting it as unhandled.
    key: str = ""
    page: str = ""
