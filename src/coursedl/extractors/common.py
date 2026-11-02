"""Base class and helpers shared by the coursedl extractors."""

import json
from dataclasses import dataclass, field
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from typing import Any, ClassVar
from urllib.parse import unquote, urlencode, urljoin, urlsplit, urlunsplit

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking import Request
from yt_dlp.utils import ExtractorError

from coursedl.config import GITHUB_PAGES, FormAuth, Source
from coursedl.extractors.html import Form, Link, password_form

Entry = dict[str, Any]


@dataclass
class Context:
    """Everything a run passes to the extractors, via `params["coursedl"]`.

    yt-dlp params accept arbitrary keys when it is used as a library, and the object
    reaches the extractor unchanged (checked in tests/api_surface.py).
    """

    name: str
    source: Source
    auth_password: str | None = None
    auth_headers: dict[str, str] = field(default_factory=dict)
    # Filled by the extractors, read by the wrapper after the run.
    unhandled: dict[str, str] = field(default_factory=dict)  # url -> page it was found on
    unmatched: dict[str, str] = field(default_factory=dict)  # remote path -> url (no rule)
    listed: int = 0
    github_trees: dict[str, dict[str, str] | None] = field(default_factory=dict)  # by repo


@dataclass(frozen=True)
class Head:
    """What a HEAD request tells about a file."""

    version: str | None = None
    timestamp: int | None = None
    filename: str | None = None  # from Content-Disposition (e.g. Google Drive)


class CoursedlIE(InfoExtractor):
    PREFIX: ClassVar[str]

    @property
    def ctx(self) -> Context:
        return self.get_param("coursedl")

    def _headers_for(self, url: str) -> dict[str, str]:
        """Auth headers, but only for the source's own host, so a basic-auth password
        never goes to YouTube or another linked site. Redirects to another host drop them
        too: yt-dlp's requests handler strips Authorization there (urllib's would not)."""
        root = self.ctx.source.url.removeprefix(self.PREFIX)
        same_host = urlsplit(url).netloc == urlsplit(root).netloc
        return self.ctx.auth_headers if same_host else {}

    def fetch(self, url: str, note: str = "Downloading page") -> tuple[str, str]:
        """Returns (html, final URL after redirects). With `form` auth, a page that shows
        a password form is logged into first."""
        page, final = self._get(url, note)
        auth = self.ctx.source.auth
        if isinstance(auth, FormAuth) and (form := password_form(page)):
            self._submit(form, final, auth)
            page, final = self._get(url, note)
            if password_form(page):
                raise ExtractorError(f"login on {url} failed (form still shown)", expected=True)
        return page, final

    def _get(
        self, url: str, note: str, data: bytes | None = None, headers: dict[str, str] | None = None
    ) -> tuple[str, str]:
        headers = {**self._headers_for(url), **(headers or {})}
        res = self._download_webpage_handle(url, self.ctx.name, note, data=data, headers=headers)
        assert res  # fatal=True: raises instead of returning False
        page, urlh = res
        return page, urlh.url

    def _submit(self, form: Form, page: str, auth: FormAuth) -> None:
        action = urljoin(page, form.action)
        # The password only goes back to the host that asked for it.
        if urlsplit(action).netloc != urlsplit(page).netloc:
            raise ExtractorError(f"login form on {page} posts to another host: {action}")
        fields = {**form.fields, form.password_field: self.ctx.auth_password or ""}
        if form.username_field and auth.username:
            fields[form.username_field] = auth.username
        fields |= auth.fields
        body = urlencode(fields)
        if form.method == "post":
            headers = {"Content-Type": "application/x-www-form-urlencoded", "Referer": page}
            _ = self._get(action, "Logging in", body.encode(), headers)
        else:
            _ = self._get(f"{action}{'&' if '?' in action else '?'}{body}", "Logging in")

    def head(self, url: str) -> Head | None:
        """HEAD request; the version is the ETag, else Last-Modified, else Content-Length.
        `None` if the request fails, e.g. 401 for a protected file."""
        strategy = self.ctx.source.version
        if strategy == "none":
            return Head()
        headers = self._headers_for(url)
        if strategy == "size":
            # The file's own size: a gzip response has the compressed size. Only here, as
            # it also changes the ETag (`W/` prefix) and so the archive IDs of `head`.
            headers = {**headers, "Accept-Encoding": "identity"}
        try:
            urlh = self._request_webpage(
                # The stubs expect urllib's Request; yt-dlp takes its own.
                Request(url, method="HEAD", headers=headers),  # pyright: ignore[reportArgumentType]
                self.ctx.name,
                f"Checking {url_name(url)}",
            )
            assert urlh  # fatal=True
        except ExtractorError as e:
            self.report_warning(f"HEAD {url} failed: {e.orig_msg}")
            return None
        h = urlh.headers
        modified = h.get("Last-Modified")
        version = h.get("ETag") or modified or h.get("Content-Length")
        if strategy == "size":
            version = h.get("Content-Length")
        elif strategy == "github":
            version = self._blob_sha(url) or version
        disposition = Message()
        disposition["Content-Disposition"] = h.get("Content-Disposition") or ""
        return Head(version, http_date(modified), disposition.get_filename())

    def _blob_sha(self, url: str) -> str | None:
        """Git blob SHA of a GitHub Pages file: a hash of its content, unlike the ETag,
        which has the deploy time in it. A project site `<owner>.github.io/<repo>/…` is
        repo `<owner>/<repo>`, else the user site repo `<owner>/<owner>.github.io`; the
        site is published from the repo's root or `docs/` (GitHub's two folder choices).
        `None` for a file that is not in the repo (e.g. built by the site generator)."""
        m = GITHUB_PAGES.match(url)
        if not m:
            return None
        owner, path = m[1].lower(), unquote(m[2] or "").lstrip("/")
        first, _, rest = path.partition("/")
        candidates = [(f"{owner}/{owner}.github.io", path)]
        if rest:  # a project site takes precedence: GitHub serves it at that path
            candidates.insert(0, (f"{owner}/{first}", rest))
        trees = [(t, p) for repo, p in candidates if (t := self._github_tree(repo)) is not None]
        if not trees:
            # Falling back to the ETag here would change the archive IDs on every outage.
            raise ExtractorError(f"no GitHub repository found for {url}", expected=True)
        for tree, p in trees:
            for prefix in ("", "docs/"):
                if sha := tree.get(prefix + p):
                    return sha
        self.report_warning(f"{url} is not in the GitHub repository, using its ETag")
        return None

    def _github_tree(self, repo: str) -> dict[str, str] | None:
        """path -> blob SHA of all files in the repo's default branch: one API request per
        repo and run (60 per hour without a token). `None` if the repo does not exist."""
        trees = self.ctx.github_trees
        if repo not in trees:
            api = f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1"
            try:
                text, _ = self._get(api, f"Downloading file list of {repo}")
                data = json.loads(text)
            except ExtractorError:
                trees[repo] = None
            else:
                if data.get("truncated"):
                    self.report_warning(f"GitHub lists only part of {repo}")
                tree = data.get("tree", [])
                trees[repo] = {e["path"]: e["sha"] for e in tree if e["type"] == "blob"}
        return trees[repo]

    def file_entry(
        self,
        key: str,
        url: str,
        path: PurePosixPath,
        version: str | None,
        timestamp: int | None = None,
        link: Link | None = None,
    ) -> Entry:
        """One downloadable file. `key` identifies the file remotely; together with the
        version it forms the archive ID, so a new version means a new download. `link`:
        the page link the file came from, for the `cd_text`/`cd_item` template fields."""
        return {
            **link_fields(link),
            "id": f"{key}@{version}" if version else key,
            "url": url,
            "title": path.stem,
            # Only names the staging file; the local name comes from `cd_path`. A fixed,
            # allowed extension avoids yt-dlp's unsafe-extension check for .lean, .tsv, ….
            "ext": "unknown_video",
            "cd_path": str(path),
            "cd_name": path.name,
            "cd_dir": str(path.parent),
            "cd_kind": "file",
            "cd_key": key,
            "cd_version": version,
            "timestamp": timestamp,
            "http_headers": self._headers_for(url),
        }

    def _result(self, entries: list[Entry], title: str) -> Any:
        self.ctx.listed += len(entries)
        # The stubs' _InfoDict has no room for our cd_* fields.
        return self.playlist_result(entries, self.ctx.name, title)  # pyright: ignore[reportArgumentType]


def link_fields(link: Link | None) -> Entry:
    """`cd_text` (link text), `cd_item` (text of its list item or table row) and `cd_row`
    (row text up to and including the link), for
    templates that build names from the page. `None` when empty, so templates can give a
    default (`%(cd_text|-)s`); "/" can't start a folder."""
    text = link.text if link else ""
    item = link.item if link else ""
    row = link.row if link else ""
    return {
        "cd_text": text.replace("/", "\u29f8") or None,
        "cd_item": item.replace("/", "\u29f8") or None,
        "cd_row": row.replace("/", "\u29f8") or None,
    }


def http_date(value: str | None) -> int | None:
    """Unix time of an HTTP date; `None` if missing or malformed (`Last-Modified: 0`)."""
    try:
        return int(parsedate_to_datetime(value).timestamp()) if value else None
    except ValueError:
        return None


def url_name(url: str) -> str:
    """Last path segment of a URL, decoded (`a/b%20c/` -> `b c`)."""
    return unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])


def norm_url(url: str) -> str:
    """Normalised URL for "seen" checks and the link state: no fragment, sorted query,
    no trailing `/`."""
    u = urlsplit(url)
    query = "&".join(sorted(u.query.split("&"))) if u.query else ""
    return urlunsplit(u._replace(path=u.path.rstrip("/"), query=query, fragment=""))
