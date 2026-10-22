"""`coursedl-youtube:` a YouTube playlist, all playlists of a channel (its `/playlists`
tab), or a single video.

Lists the videos with yt-dlp's own YouTube extractors, without downloading anything:
`cd_dir` is the playlist's title, below the channel tab's title for a channel
(`<channel> - Playlists/<playlist>`), `cd_index` the video's position in its playlist. The
videos themselves are extracted by yt-dlp later, like the YouTube links of `coursedl-web:`.
A single video has no playlist: `cd_dir` is empty and `cd_index` 1.
"""

from pathlib import PurePosixPath
from typing import Any, final, override

from yt_dlp.utils import ExtractorError, sanitize_filename

from coursedl.extractors.common import CoursedlIE, Entry


@final
class CoursedlYoutubeIE(CoursedlIE):
    PREFIX = "coursedl-youtube:"
    _VALID_URL = r"coursedl-youtube:https?://(www\.|m\.)?youtube\.com/.+"

    @override
    def _real_extract(self, url: str) -> Any:
        root = url.removeprefix(self.PREFIX)
        return self._result(self._list(root, PurePosixPath()), root)

    def _list(self, url: str, parent: PurePosixPath) -> list[Entry]:
        # process=False: only the listing (title + lazy entries), no video extraction.
        # `_downloader` (the running YoutubeDL) is missing from the stubs.
        ydl: Any = self._downloader  # pyright: ignore[reportAttributeAccessIssue]
        info: Any = ydl.extract_info(url, download=False, process=False)
        if not info:
            raise ExtractorError(f"nothing found at {url}", expected=True)
        if info.get("_type") != "playlist":  # a single video (fully extracted here already)
            return [self._video(url, parent, 1)]
        here = parent / sanitize_filename(info.get("title") or info["id"])
        entries: list[Entry] = []
        for index, e in enumerate(info["entries"], start=1):
            if e.get("ie_key") == "Youtube":  # a video
                entries.append(self._video(e["url"], here, index))
            else:  # a playlist of the channel
                entries += self._list(e["url"], here)
        return entries

    def _video(self, url: str, folder: PurePosixPath, index: int) -> Entry:
        return {
            **self.url_result(url, url_transparent=True),
            "cd_dir": str(folder),
            "cd_index": index,
            "cd_kind": "video",
        }
