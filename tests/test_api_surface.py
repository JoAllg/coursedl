"""The yt-dlp internals coursedl depends on: signatures (snapshot) and behaviour.

yt-dlp's extractor/postprocessor API is not a stable public API and YouTube forces us to
always use the latest version. This file is the list of everything we use; the
`yt-dlp-watch` workflow runs it against each new release. When it fails, the diff shows
which part changed.

Update the snapshot after a deliberate change:
`UPDATE_SNAPSHOT=1 uv run pytest tests/test_api_surface.py`.
"""

# typeshed's yt-dlp stubs are incomplete for the internals tested here.
# pyright: reportArgumentType=false

import inspect
import os
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, final, override

from yt_dlp import YoutubeDL
from yt_dlp.downloader import get_suitable_downloader
from yt_dlp.downloader.external import Aria2cFD
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking import Request
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import ExtractorError, sanitize_filename

SNAPSHOT = Path(__file__).with_name("api_surface.snapshot.txt")

SYMBOLS: dict[str, Any] = {
    "YoutubeDL.__init__": YoutubeDL.__init__,
    "YoutubeDL.add_info_extractor": YoutubeDL.add_info_extractor,
    "YoutubeDL.add_post_processor": YoutubeDL.add_post_processor,
    "YoutubeDL.extract_info": YoutubeDL.extract_info,
    "YoutubeDL.evaluate_outtmpl": YoutubeDL.evaluate_outtmpl,
    "InfoExtractor.__init__": InfoExtractor.__init__,
    "InfoExtractor.get_param": InfoExtractor.get_param,
    "InfoExtractor._download_webpage_handle": InfoExtractor._download_webpage_handle,
    "InfoExtractor._request_webpage": InfoExtractor._request_webpage,
    "InfoExtractor._download_xml": InfoExtractor._download_xml,
    "InfoExtractor._real_extract": InfoExtractor._real_extract,
    "InfoExtractor.playlist_result": InfoExtractor.playlist_result,
    "InfoExtractor.url_result": InfoExtractor.url_result,
    "InfoExtractor.report_warning": InfoExtractor.report_warning,
    "InfoExtractor.suitable": InfoExtractor.suitable,
    "InfoExtractor.ie_key": InfoExtractor.ie_key,
    "YoutubeDL.urlopen": YoutubeDL.urlopen,
    "PostProcessor.__init__": PostProcessor.__init__,
    "PostProcessor.run": PostProcessor.run,
    "Request.__init__": Request.__init__,
    "sanitize_filename": sanitize_filename,
    "ExtractorError.__init__": ExtractorError.__init__,
    "get_suitable_downloader": get_suitable_downloader,
}


def _snapshot() -> str:
    lines = [f"{name}{inspect.signature(obj)}" for name, obj in SYMBOLS.items()]
    return "\n".join(lines) + "\n"


def test_signatures() -> None:
    current = _snapshot()
    if os.environ.get("UPDATE_SNAPSHOT"):
        _ = SNAPSHOT.write_text(current)
    assert current == SNAPSHOT.read_text()


class _Marker:
    pass


@final
class _ListIE(InfoExtractor):
    """Stands in for our extractors: one file entry and one url_transparent entry."""

    _VALID_URL = r"cdtest-list:.+"

    @override
    def _real_extract(self, url: str) -> Any:
        assert isinstance(self.get_param("coursedl"), _Marker)
        # coursedl-youtube runs yt-dlp's YouTube extractors through the running instance.
        assert isinstance(self._downloader, YoutubeDL)  # pyright: ignore[reportAttributeAccessIssue]
        entries: Any = [
            {"id": "f@v1", "url": "https://example.org/f.pdf", "ext": "unknown_video",
             "title": "f", "cd_kind": "file"},
            {**self.url_result("cdtest-video:x", _VideoIE, url_transparent=True),
             "cd_dir": "A/B", "cd_kind": "video"},
        ]  # fmt: skip
        return self.playlist_result(entries, "pl")


@final
class _VideoIE(InfoExtractor):
    _VALID_URL = r"cdtest-video:.+"

    @override
    def _real_extract(self, url: str) -> Any:
        return {"id": "vid", "title": "Video", "url": "https://example.org/v.mp4", "ext": "mp4"}


def _ydl(**params: Any) -> YoutubeDL:
    ydl = YoutubeDL({"quiet": True, "coursedl": _Marker(), "simulate": True, **params})
    ydl.add_info_extractor(_ListIE())
    ydl.add_info_extractor(_VideoIE())
    return ydl


def test_extraction_behaviour(tmp_path: Path) -> None:
    seen: list[tuple[str, bool]] = []

    def match_filter(info: Mapping[str, Any], incomplete: bool) -> str | None:
        seen.append((info.get("id", ""), incomplete))
        return "rejected" if info.get("id") == "f@v1" and not incomplete else None

    archive = tmp_path / "archive"
    ydl = _ydl(match_filter=match_filter, download_archive=str(archive))
    # Custom params object reaches the extractor (asserted in _ListIE), ie_key dispatch
    # works for extractors added with add_info_extractor.
    assert _ListIE.ie_key() == "_List"
    info: Any = ydl.extract_info("cdtest-list:x", ie_key="_List")
    assert info
    video = next(e for e in info["entries"] if e["id"] == "vid")
    # url_transparent keeps our fields next to the ones from the video's extractor.
    assert (video["cd_dir"], video["cd_kind"], video["title"]) == ("A/B", "video", "Video")
    # match_filter is called with incomplete=True first, then for the complete entry.
    assert ("f@v1", False) in seen
    # Rejected entries are not written to the archive.
    assert not archive.exists() or "f@v1" not in archive.read_text()


def test_outtmpl_behaviour() -> None:
    ydl = _ydl()
    info: Any = {"id": "x", "cd_path": "a/b.pdf", "title": "t", "ext": "pdf"}
    # evaluate_outtmpl keeps "/" in field values (our folders), prepare_filename doesn't.
    assert ydl.evaluate_outtmpl("%(cd_path)s", info) == "a/b.pdf"
    assert ydl.prepare_filename(info, outtmpl="%(cd_path)s") == "a\u29f8b.pdf"


def test_aria2c_behaviour() -> None:
    params: Any = {
        "external_downloader": {"http": "aria2c"},
        "external_downloader_args": {"aria2c": ["-x4"]},
    }

    def fd(protocol: str) -> Any:
        return get_suitable_downloader({"protocol": protocol, "url": "https://x/y"}, params)

    assert fd("https") is Aria2cFD
    assert fd("m3u8_native") is not Aria2cFD
    assert fd("http_dash_segments") is not Aria2cFD
    # Headers from the entry reach aria2c; our args come after its defaults,
    # so "-x4" wins over "-x16".
    ydl = YoutubeDL(params | {"quiet": True})
    cmd = Aria2cFD(ydl, ydl.params)._make_cmd(  # pyright: ignore[reportAttributeAccessIssue]
        "out.part", {"url": "https://x/y", "http_headers": {"Authorization": "Basic Zm9v"}}
    )
    assert "Authorization: Basic Zm9v" in cmd
    assert cmd.index("-x4") > cmd.index("-x16")
    assert any(a.startswith("--load-cookies=") for a in cmd)


def test_redirect_drops_authorization() -> None:
    """Basic auth must not follow a redirect to another host (here: another port)."""
    seen: list[str | None] = []

    @final
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{other.server_port}/end")
            else:
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Length", "0")
            self.end_headers()

        @override
        def log_message(self, format: str, *args: Any) -> None:
            pass

    first, other = HTTPServer(("127.0.0.1", 0), Handler), HTTPServer(("127.0.0.1", 0), Handler)
    for server in (first, other):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{first.server_port}/start"
        ydl = YoutubeDL({"quiet": True})
        _ = ydl.urlopen(Request(url, headers={"Authorization": "Basic Zm9v"})).read()
    finally:
        for server in (first, other):
            server.shutdown()
    assert seen == [None]
