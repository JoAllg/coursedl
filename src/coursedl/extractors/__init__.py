"""yt-dlp extractors for the coursedl source types. Each one only lists files; yt-dlp
downloads them. They are registered per run with `YoutubeDL.add_info_extractor`."""

from coursedl.extractors.common import Context, CoursedlIE
from coursedl.extractors.dirlist import CoursedlDirIE
from coursedl.extractors.nextcloud import CoursedlNextcloudIE
from coursedl.extractors.web import CoursedlWebIE
from coursedl.extractors.youtube import CoursedlYoutubeIE

EXTRACTORS: list[type[CoursedlIE]] = [
    CoursedlWebIE,
    CoursedlDirIE,
    CoursedlNextcloudIE,
    CoursedlYoutubeIE,
]

__all__ = ["EXTRACTORS", "Context", "CoursedlIE"]
