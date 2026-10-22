"""Extractors against saved pages (tests/fixtures), network calls replaced."""

from pathlib import Path
from typing import Any

import pytest
from yt_dlp import YoutubeDL

from coursedl.config import Source
from coursedl.extractors import Context, CoursedlIE
from coursedl.extractors.common import Head
from coursedl.extractors.dirlist import CoursedlDirIE
from coursedl.extractors.html import content_links, password_form
from coursedl.extractors.nextcloud import CoursedlNextcloudIE
from coursedl.extractors.web import CoursedlWebIE

FIXTURES = Path(__file__).parent / "fixtures"
CPP = "https://ad-wiki.informatik.example.edu/teaching/ProgrammierenCplusplusSS2026"
SVN_WEB = "https://svn.example.edu/ss2026/ProgrammierenCplusplus/svn/public/"


def page(name: str) -> str:
    raw = (FIXTURES / name).read_bytes()
    return raw.decode("latin-1" if name == "lmb.html" else "utf-8")


def make_ie[T: CoursedlIE](
    cls: type[T], url: str, pages: dict[str, str], **opts: Any
) -> tuple[T, Context]:
    ctx = Context("test", Source(url=url, output_dir=Path("out"), **opts))
    ie = cls(YoutubeDL({"coursedl": ctx, "quiet": True}))  # pyright: ignore[reportArgumentType]

    def fetch(url: str, note: str = "") -> tuple[str, str]:
        del note
        return pages[url], url

    ie.fetch = fetch
    ie.head = lambda url: Head("v1")
    return ie, ctx


def test_content_area_skips_navigation() -> None:
    hrefs = [link.href for link in content_links(page("cpp.html"))]
    # MoinMoin's sidebar and wiki actions are outside #content.
    assert not any("action=" in h or h.endswith("RecentChanges") for h in hrefs)
    assert "https://youtu.be/rgs3qj-YPYM" in hrefs
    # LMB: navigation tables are outside #content; headings become folders.
    links = {link.href: link for link in content_links(page("lmb.html"))}
    assert "/people/team.php" not in links
    assert links["slides/ImageProcessing01.pdf"].folders == ("Materials",)
    # A stray </tr> in the material table must not end the content area.
    assert links["slides/ImageProcessing09.pdf"].folders == ("Materials",)
    # Lean: HTML5 <main>, the <nav> above it is ignored.
    links = {link.href: link for link in content_links(page("lean.html"))}
    assert links["/teaching/26ws/lean/slides/lecture01.pdf"].folders == ("Lecture",)


def test_content_selector() -> None:
    cpp = page("cpp.html")
    assert content_links(cpp, "div#content") == content_links(cpp)
    lmb = content_links(page("lmb.html"), "table")  # first <table> is the navigation
    assert lmb[0].href == "/index.php"


def test_web_page() -> None:
    sources = {
        CPP: page("cpp.html"),
        # Only the code listing for lecture 1 is a fixture; the others stay unhandled.
        SVN_WEB + "code/vorlesung-01/": page("svn_code_vl01.html"),
    }
    ie, ctx = make_ie(
        CoursedlWebIE,
        f"coursedl-web:{CPP}",
        sources,
        dir_regex=r"/svn/public/code/vorlesung-01/",
        ignore_regex=r"/svn/loesungen/",
    )
    result = ie._real_extract(f"coursedl-web:{CPP}")  # pyright: ignore[reportPrivateUsage]
    entries = result["entries"]
    folder = "Vorlesungsfolien, Videoaufzeichnungen, Übungsblätter und Codedateien"
    by_path = {e.get("cd_path"): e for e in entries}

    pdf = by_path[f"{folder}/vorlesung-01.pdf"]
    assert pdf["id"].endswith("/svn/public/folien/vorlesung-01.pdf@v1")
    assert by_path[f"{folder}/vorlesung-01/PrimeMain.cpp"]["cd_kind"] == "file"

    videos = [e for e in entries if e.get("cd_kind") == "video"]
    assert len(videos) == 12
    assert videos[0]["_type"] == "url_transparent"
    assert videos[0]["cd_dir"] == folder
    assert ctx.listed == len(entries)
    # Links in the content that no rule handles; wiki actions and anchors are not.
    assert "https://ad.informatik.example.edu/staff/lehmann" in ctx.unhandled
    assert not any("action=" in u for u in ctx.unhandled)


def test_dir_listing() -> None:
    ie, _ = make_ie(CoursedlDirIE, f"coursedl-dir:{SVN_WEB}", {})
    pages = {
        SVN_WEB: page("svn_public.html"),
        SVN_WEB + "code/": '<main><a href="vorlesung-01/?">vorlesung-01</a></main>',
        SVN_WEB + "code/vorlesung-01/": page("svn_code_vl01.html"),
        SVN_WEB + "folien/": page("svn_folien.html"),
        SVN_WEB + "uebungen/": "<main></main>",
    }
    ie.fetch = lambda url, note="": (pages[url], url)
    entries = ie._real_extract(f"coursedl-dir:{SVN_WEB}")["entries"]  # pyright: ignore[reportPrivateUsage]
    paths = {e["cd_path"] for e in entries}
    assert "folien/vorlesung-01.pdf" in paths
    assert "code/vorlesung-01/Makefile" in paths
    # "..", breadcrumbs and "?revision=" links are not below the listing.
    assert all(not p.startswith(("..", "/")) for p in paths)
    assert all("?" not in e["url"] for e in entries)


def test_autoindex_listing() -> None:
    url = "https://ad-teaching.cs.example.edu/ProgrammierenCplusplusSS2026/osm-extra/"
    ie, _ = make_ie(CoursedlDirIE, f"coursedl-dir:{url}", {url: page("osm.html")})
    entries = ie._real_extract(f"coursedl-dir:{url}")["entries"]  # pyright: ignore[reportPrivateUsage]
    names = {e["cd_path"] for e in entries}
    assert "osm-ch-all-nodes.tsv" in names
    assert len(names) == 12  # sort links and "Parent Directory" are dropped


NC_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
 <d:response><d:href>/public.php/webdav/{dir}</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
 </d:response>
 {children}
</d:multistatus>"""
NC_DIR = """<d:response><d:href>/public.php/webdav/{path}/</d:href><d:propstat><d:prop>
  <d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>"""
NC_FILE = """<d:response><d:href>/public.php/webdav/{path}</d:href><d:propstat><d:prop>
  <d:getetag>&quot;{etag}&quot;</d:getetag>
  <d:getlastmodified>Tue, 20 Oct 2026 13:54:33 GMT</d:getlastmodified>
  <d:resourcetype/></d:prop></d:propstat></d:response>"""


def test_nextcloud(monkeypatch: pytest.MonkeyPatch) -> None:
    import xml.etree.ElementTree as ET

    listings = {
        "": NC_XML.format(
            dir="",
            children=NC_DIR.format(path="Folien") + NC_FILE.format(path="Info.pdf", etag="e1"),
        ),
        "Folien/": NC_XML.format(
            dir="Folien/", children=NC_FILE.format(path="Folien/VL%2001.pdf", etag="e2")
        ),
    }
    share = "coursedl-nextcloud:https://nc.example.org/index.php/s/TOKEN"
    ie, ctx = make_ie(CoursedlNextcloudIE, share, {})
    requests: list[Any] = []

    def download_xml(req: Any, *_: Any) -> ET.Element:
        requests.append(req)
        rel = req.url.removeprefix("https://nc.example.org/public.php/webdav/")
        return ET.fromstring(listings[rel.replace("%2F", "/")])

    monkeypatch.setattr(ie, "_download_xml", download_xml)
    ctx.auth_password = "secret"
    entries = ie._real_extract(share)["entries"]  # pyright: ignore[reportPrivateUsage]
    by_path = {e["cd_path"]: e for e in entries}
    assert set(by_path) == {"Info.pdf", "Folien/VL 01.pdf"}
    vl = by_path["Folien/VL 01.pdf"]
    assert vl["id"] == "Folien/VL 01.pdf@e2"
    assert vl["url"] == "https://nc.example.org/public.php/webdav/Folien/VL%2001.pdf"
    assert vl["timestamp"] == 1792504473
    # Basic auth <token>:<password>, also on the file entries (aria2c needs it there).
    assert vl["http_headers"]["Authorization"] == "Basic VE9LRU46c2VjcmV0"
    assert requests[0].method == "PROPFIND"
    assert requests[0].headers["Depth"] == "1"


def test_password_form() -> None:
    # WordPress "protected post" page (AutoML lecture, before login).
    form = password_form(page("automl_login.html"))
    assert form
    assert form.action == "https://ml.informatik.example.edu/wp-login.php?action=postpass"
    assert form.method == "post"
    assert form.password_field == "post_password"
    assert form.fields["redirect_to"] == "https://ml.informatik.example.edu/ss26-automl-lecture/"
    assert form.fields["Submit"] == "Enter"
    assert password_form(page("cpp.html")) is None


LIST_PAGE = """<main><h4>Overview</h4><ul>
<li>Week 1 (<a href="sheet1.pdf">Sheet</a> | <a href="x">PDF</a>):
  <ul><li><a href="slides1.pdf">Slides</a></li>
      <li>Videos: <a href="v1.mp4">merged</a></li></ul></li>
<li>Week 2: <ul><li><a href="slides2.pdf">Slides</a></li></ul></li>
<li><a href="loose.pdf">no sublist</a></li>
</ul></main>"""


def test_list_labels_become_folders() -> None:
    folders = {link.href: link.folders for link in content_links(LIST_PAGE)}
    assert folders["slides1.pdf"] == ("Overview", "Week 1")
    assert folders["sheet1.pdf"] == ("Overview", "Week 1")  # in the label line itself
    assert folders["v1.mp4"] == ("Overview", "Week 1")  # no sublist: no own folder
    assert folders["slides2.pdf"] == ("Overview", "Week 2")
    assert folders["loose.pdf"] == ("Overview",)


def test_google_drive_and_name_clashes() -> None:
    base = "https://example.org/course/"
    html = """<main><h2>Videos</h2>
    <a href="https://drive.google.com/file/d/AAA/view?usp=sharing">merged videos</a>
    <a href="https://drive.google.com/open?id=BBB">merged videos with subtitles</a>
    <a href="https://drive.google.com/file/d/CCC/view">PDF</a></main>"""
    ie, _ = make_ie(CoursedlWebIE, f"coursedl-web:{base}", {base: html})
    names = {"AAA": "week.mp4", "BBB": "week.mp4", "CCC": None}
    ie.head = lambda url: Head("v1", None, names[url.split("id=")[1].split("&")[0]])
    entries = ie._real_extract(f"coursedl-web:{base}")["entries"]  # pyright: ignore[reportPrivateUsage]
    assert [e["cd_path"] for e in entries] == [
        "Videos/week.mp4",
        "Videos/week (merged videos with subtitles).mp4",
        "Videos/CCC",  # no Content-Disposition: the file ID
    ]
    assert entries[0]["url"] == (
        "https://drive.usercontent.google.com/download?id=AAA&export=download&confirm=t"
    )


def test_password_form_types() -> None:
    # Types are case-insensitive; a <button> without a type is a submit button.
    form = password_form(
        '<form><input name="u" type="TEXT"><input name="p" type="Password">'
        + '<button name="go" value="1">Go</button></form>'
    )
    assert form
    assert (form.password_field, form.username_field) == ("p", "u")
    assert form.fields["go"] == "1"
