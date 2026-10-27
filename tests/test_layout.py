"""tests/fixtures/courses.toml against the sample pages: saved page -> extractor -> template ->
transform rules, as in a run. Sources without a saved page use the remote paths of a
dry run (tests/fixtures/remote_paths/<source>.txt).

Every file must match a rule and land in the course layout (see courses.toml), and no
two files may share a local path."""

from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from yt_dlp import YoutubeDL

from coursedl import config
from coursedl.extractors import Context
from coursedl.extractors.common import Head
from coursedl.extractors.web import CoursedlWebIE
from coursedl.movepp import local_path, unmatched

FIXTURES = Path(__file__).parent / "fixtures"
LAYOUT = ("Slides/", "Recordings/", "Exercises/", "Solutions/", "Media/", "Exam/", "Project/")
CFG = config.load(FIXTURES / "courses.toml")
DATA = "https://course.example.edu/ExampleCourse/data-extra/"
PAGES = {  # source -> {url: sample page}
    "lecture-wiki": {"": "wiki.html", DATA: "autoindex.html"},
    "table": {"": "table.html"},
    "html5": {"": "html5.html"},
}


def page_entries(source: str) -> list[dict[str, Any]]:
    src = CFG.sources[source]
    root = src.url.removeprefix("coursedl-web:")
    pages = {url or root: (FIXTURES / name).read_bytes() for url, name in PAGES[source].items()}
    ie = CoursedlWebIE(YoutubeDL({"coursedl": Context(source, src), "quiet": True}))  # pyright: ignore[reportArgumentType]

    def fetch(url: str, note: str = "") -> tuple[str, str]:
        del note
        raw = pages[url]
        return raw.decode("latin-1" if source == "table" else "utf-8"), url

    ie.fetch = fetch
    ie.head = lambda url: Head("v1")
    return ie._real_extract(src.url)["entries"]  # pyright: ignore[reportPrivateUsage]


def path_entries(source: str) -> list[dict[str, Any]]:
    paths = (FIXTURES / f"remote_paths/{source}.txt").read_text().splitlines()
    return [{"cd_kind": "file", "cd_path": p, "cd_name": PurePosixPath(p).name} for p in paths]


def mapping(source: str) -> dict[str, str]:
    entries = page_entries(source) if source in PAGES else path_entries(source)
    ydl = YoutubeDL({"quiet": True})
    result: dict[str, str] = {}
    for entry in entries:
        remote, local = local_path(ydl, CFG.sources[source], entry)
        no_rule = unmatched(CFG.sources[source], remote)
        result[str(remote)] = "(no rule matches)" if no_rule else str(local)
    return result


@pytest.mark.parametrize("source", ["lecture-wiki", "lecture-svn", "weekly", "table", "html5"])
def test_layout(source: str) -> None:
    local = mapping(source)
    assert local
    assert {r: p for r, p in local.items() if not p.startswith(LAYOUT)} == {}
    assert len(set(local.values())) == len(local), "two files map to one path"


def test_names() -> None:
    svn = mapping("lecture-svn")
    assert svn["slides/lecture-03.pdf"] == "Slides/03 lecture.pdf"
    assert svn["exercises/sheet-03.pdf"] == "Exercises/03 sheet.pdf"
    assert svn["code/lecture-03/Makefile"] == "Media/03/Makefile"
    assert svn["code/project/template/Game.h"] == "Project/template/Game.h"

    wiki = set(mapping("lecture-wiki").values())
    assert "Recordings/09 Sorting.mp4" in wiki
    assert "Media/09/data-de.tsv" in wiki  # linked in lecture 9's list item
    assert "Media/09/data-extra/data-us.tsv" in wiki

    weekly = mapping("weekly")
    assert weekly["Introduction_Slides_2026.pdf"] == "Slides/00 Introduction.pdf"
    assert weekly["Week 0/w01_t01_01_overview.pdf"] == "Slides/00 overview.pdf"
    assert weekly["Week 0/Week_01_all_videos.mp4"] == "Recordings/00 merged week 01.mp4"
    assert weekly["Week 6/Exercise 5 - Search.pdf"] == "Exercises/06 Search.pdf"  # the page's week
    assert weekly["Week 1/3_Basics_ALL.pdf"] == "Slides/01 Basics_ALL.pdf"

    table = set(mapping("table").values())
    assert "Slides/01 Introduction and basics.pdf" in table
    assert "Recordings/01 Lecture.mp4" in table
    assert "Recordings/03 Appendix.mp4" in table
    assert "Exercises/02 Exercise.zip" in table
    assert "Solutions/02 Solution.zip" in table

    html5 = set(mapping("html5").values())
    assert html5 == {
        "Slides/01 Introduction.pdf",
        "Recordings/01 Introduction.mp4",
        "Media/01/lecture01.py",
    }
