"""tests/fixtures/courses.toml against the real pages: saved page -> extractor -> template ->
transform rules, as in a run. Sources without a saved page use the remote paths of a
real dry run (tests/fixtures/remote_paths/<source>.txt).

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
OSM = "https://ad-teaching.cs.example.edu/ProgrammierenCplusplusSS2026/osm-extra/"
PAGES = {  # source -> {url: saved page}
    "cpp": {"": "cpp.html", OSM: "osm.html"},
    "imageproc": {"": "lmb.html"},
    "lean": {"": "lean.html"},
}


def page_entries(source: str) -> list[dict[str, Any]]:
    src = CFG.sources[source]
    root = src.url.removeprefix("coursedl-web:")
    pages = {url or root: (FIXTURES / name).read_bytes() for url, name in PAGES[source].items()}
    ie = CoursedlWebIE(YoutubeDL({"coursedl": Context(source, src), "quiet": True}))  # pyright: ignore[reportArgumentType]

    def fetch(url: str, note: str = "") -> tuple[str, str]:
        del note
        raw = pages[url]
        return raw.decode("latin-1" if source == "imageproc" else "utf-8"), url

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


@pytest.mark.parametrize("source", ["cpp", "cpp-public", "automl", "imageproc", "lean"])
def test_layout(source: str) -> None:
    local = mapping(source)
    assert local
    assert {r: p for r, p in local.items() if not p.startswith(LAYOUT)} == {}
    assert len(set(local.values())) == len(local), "two files map to one path"


def test_names() -> None:
    public = mapping("cpp-public")
    assert public["folien/vorlesung-03.pdf"] == "Slides/03 vorlesung.pdf"
    assert public["uebungen/blatt-03.pdf"] == "Exercises/03 blatt.pdf"
    assert public["code/vorlesung-03/Makefile"] == "Media/03/Makefile"
    assert public["code/projekt/template/TerminalManager.h"] == "Project/template/TerminalManager.h"

    cpp = set(mapping("cpp").values())
    assert "Recordings/09 STL = Standard Template Library.mp4" in cpp
    assert "Media/09/osm-de.tsv" in cpp  # linked in lecture 9's list item
    assert "Media/09/osm-extra/osm-us.tsv" in cpp

    automl = mapping("automl")
    assert automl["Introduction_Slides_2026.pdf"] == "Slides/00 Introduction.pdf"
    assert automl["Week 0/w01_t01_04_big_picture.pdf"] == "Slides/00 big_picture.pdf"
    assert automl["Week 0/Week_01_all_videos.mp4"] == "Recordings/00 MOOC week 01.mp4"
    assert automl["Week 6/Exercise 5 - NAS.pdf"] == "Exercises/06 NAS.pdf"  # the page's week
    assert automl["Week 1/3_Basic-of-HPO_ALL.pdf"] == "Slides/01 Basic-of-HPO_ALL.pdf"

    lmb = set(mapping("imageproc").values())
    assert "Slides/01 Introduction and image basics.pdf" in lmb
    assert "Recordings/01 ImageProcessing.mp4" in lmb
    assert "Recordings/03 Appendix.mp4" in lmb
    assert "Exercises/02 Exercise.zip" in lmb
    assert "Solutions/02 Solution.zip" in lmb

    lean = set(mapping("lean").values())
    assert lean == {
        "Slides/01 Introduction.pdf",
        "Recordings/01 Introduction.mp4",
        "Media/01/lecture01.lean",
    }
