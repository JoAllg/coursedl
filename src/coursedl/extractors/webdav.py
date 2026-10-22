"""Walking a WebDAV tree with `PROPFIND Depth: 1` (Nextcloud shares)."""

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from yt_dlp.networking import Request

from coursedl.extractors.common import CoursedlIE, http_date

DAV = "{DAV:}"
ROOT = PurePosixPath()
PROPFIND_BODY = (
    b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
    b"<d:resourcetype/><d:getetag/><d:getlastmodified/><d:version-name/>"
    b"</d:prop></d:propfind>"
)


@dataclass(frozen=True)
class DavFile:
    path: PurePosixPath  # relative to the walked root
    url: str
    version: str | None
    timestamp: int | None


def walk(
    ie: CoursedlIE, root: str, headers: dict[str, str], folder: PurePosixPath = ROOT
) -> list[DavFile]:
    """All files below `root` (a URL ending in `/`), one PROPFIND per folder: `Depth:
    infinity` is often disabled. The version is `version-name` (SVN: the revision of the
    last change), else the ETag; both change exactly when the file does."""
    url = root + (quote(str(folder)) + "/" if folder.parts else "")
    xml = ie._download_xml(
        # The stubs expect urllib's Request; yt-dlp takes its own.
        Request(  # pyright: ignore[reportArgumentType]
            url,
            data=PROPFIND_BODY,
            method="PROPFIND",
            headers={**headers, "Depth": "1", "Content-Type": "application/xml"},
        ),
        ie.ctx.name,
        f"Listing /{folder}",
    )
    root_path = unquote(urlsplit(root).path)
    files: list[DavFile] = []
    for resp in xml.iter(f"{DAV}response"):
        href = unquote(urlsplit(resp.findtext(f"{DAV}href") or "").path)
        rel = PurePosixPath(href.removeprefix(root_path).strip("/"))
        if rel == folder:
            continue  # the folder itself
        if resp.find(f".//{DAV}resourcetype/{DAV}collection") is not None:
            files += walk(ie, root, headers, rel)
            continue
        version = resp.findtext(f".//{DAV}version-name") or (
            (resp.findtext(f".//{DAV}getetag") or "").strip('"') or None
        )
        ts = http_date(resp.findtext(f".//{DAV}getlastmodified"))
        files.append(DavFile(rel, root + quote(str(rel)), version, ts))
    return files
