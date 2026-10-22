# Adding a source type

A source type is a yt-dlp extractor that lists the files of one kind of source. Write one only if none of the [existing types](sources.md) fits, e.g. a site with its own API or a login flow that `form` auth can't do. Keep it generic: it should handle a kind of site, not one page. The user's config picks the concrete URL and the rules.

## How an extractor fits in

1. A source's `url` starts with the type's prefix, e.g. `coursedl-web:https://…`. coursedl registers all extractors with yt-dlp (`YoutubeDL.add_info_extractor`) and hands it the URL. yt-dlp picks the extractor whose `_VALID_URL` matches.
2. The extractor only **lists** files: it returns a playlist of entries, one per file, each with a download URL, a remote path and a version. It downloads nothing itself.
3. yt-dlp downloads each entry, unless its ID is already in the download archive. The ID is `<remote key>@<version>` (built by `file_entry`), so a new version means a new download, and a file the user renamed or deleted locally is not downloaded again.
4. The remote path (`cd_path`) goes through the source's output template (`file = "%(cd_path)s"` by default) and then its [transform rules](configuration.md#transform-rules), which give the local path.

So an extractor's whole job is to answer: which files are there, where do they live remotely, and what is their current version?

## Writing one

Extractors live in `src/coursedl/extractors/`, one module per type. Subclass `CoursedlIE` from `common.py`:

- `PREFIX`: the URL prefix, e.g. `"coursedl-json:"`.
- `_VALID_URL`: a regex for the full source URL, prefix included.
- `_real_extract(url)`: lists the files and returns `self._result(entries, title)`.

Each entry comes from `self.file_entry(key, url, path, version, timestamp)`:

| Argument | Meaning |
|---|---|
| `key` | Identifies the file remotely and stays the same across versions: the URL, or the path inside a share. |
| `url` | Where yt-dlp downloads it from. |
| `path` | Remote path (`PurePosixPath`) that the transform rules see: folders the user can recognise from the site, file name with extension. |
| `version` | Anything that changes exactly when the file changes: ETag, revision, modification date, size. `None` if unknown (the file is then downloaded only once). |
| `timestamp` | Optional Unix time, set as the local file's modification time. |

Don't change what `key` and `version` contain once a type is in use: a different archive ID makes every file look new, and with `on_change = "keep-both"` (the default) all of them are downloaded again as copies.

### Example

A site that publishes its file list as JSON, e.g. `https://example.edu/course/materials.json` with `[{"path": "Slides/01 Intro.pdf", "url": "files/123", "modified": "2026-10-01T10:00:00Z"}, …]`:

```python
"""`coursedl-json:` a JSON file list: `[{"path", "url", "modified"}, …]`."""

import json
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, final, override
from urllib.parse import urljoin

from coursedl.extractors.common import CoursedlIE


@final
class CoursedlJsonIE(CoursedlIE):
    PREFIX = "coursedl-json:"
    _VALID_URL = r"coursedl-json:https?://.+"

    @override
    def _real_extract(self, url: str) -> Any:
        listing = url.removeprefix(self.PREFIX)
        text, final_url = self.fetch(listing, "Downloading file list")
        entries = []
        for f in json.loads(text):
            file_url = urljoin(final_url, f["url"])
            ts = int(datetime.fromisoformat(f["modified"]).timestamp())
            entries.append(
                self.file_entry(file_url, file_url, PurePosixPath(f["path"]), f["modified"], ts)
            )
        return self._result(entries, listing)
```

### Helpers you can use

- `self.fetch(url)`: GET a page with the source's auth; returns `(text, final URL)`. With `form` auth it logs in first when the page shows a password form.
- `self.head(url)`: HEAD request; `Head.version` is the ETag, else Last-Modified, else Content-Length. Use it when the listing itself has no version. Returns `None` on failure (e.g. 401); report such a file with `self.ctx.unhandled[norm_url(url)] = page` instead of failing the whole source.
- `content_links(html, selector)` (`html.py`): the links in a page's content area, with the headings and list labels above each one (`Link.folders`).
- `crawl_listing(ie, url, folder)` (`dirlist.py`): all files below an HTTP directory listing.
- `walk(ie, root, headers)` (`webdav.py`): all files below a WebDAV folder, with versions.
- `self.url_result(url, url_transparent=True)`: hand a link (e.g. a video) to yt-dlp's own extractors, as `youtube.py` does.

Auth: `self.ctx.auth_headers` holds the source's basic-auth or cookie headers and `self.ctx.auth_password` its password. `fetch`, `head` and `file_entry` send the headers only to the source's own host, so a password never goes to a linked site. If your source needs other headers to download its files, set `entry["http_headers"]` (see `nextcloud.py`).

### Options

A type that needs its own settings adds them to `Source` in `config.py`, under a comment naming the type (like the `# coursedl-web` block), with a default and pydantic validation. Read them from `self.ctx.source`. Document them in [configuration.md](configuration.md).

## Registering it

1. Add the class to `EXTRACTORS` in `src/coursedl/extractors/__init__.py`.
2. Add its prefix to the `url` pattern of `Source` in `src/coursedl/config.py`, otherwise configs using it fail validation.
3. Describe it in [sources.md](sources.md): the table at the top and a section with an example `url`.

## Testing

Tests run offline: save the pages or responses of a real source into `tests/fixtures/` (with personal data and real host names removed) and serve them from a dict. `make_ie` in `tests/test_extractors.py` builds an extractor with a test config and replaces `fetch` and `head`; for other requests, `monkeypatch` the yt-dlp method (see `test_nextcloud`). Check the entries' `cd_path`, `id` and `url`.

yt-dlp's extractor API is not stable. Every yt-dlp internal coursedl uses (methods like `_download_xml`, `_request_webpage`, `url_result`) is listed in `tests/test_api_surface.py`, which the `yt-dlp-watch` workflow runs against each new yt-dlp release. If your extractor uses one that isn't listed, add it and update the snapshot: `UPDATE_SNAPSHOT=1 uv run pytest tests/test_api_surface.py`.

Before you're done: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` and `uv run pytest` all pass.
