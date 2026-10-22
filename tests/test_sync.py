"""Whole runs against a local HTTP server: yt-dlp really downloads, nothing is mocked."""

import functools
import http.server
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import override

import pytest

from coursedl.cli import main
from coursedl.movepp import place
from coursedl.report import SourceReport, SourceState, update_state


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    @override
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def site(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    root = tmp_path / "site"
    root.mkdir()
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


def write_page(root: Path, *links: str) -> None:
    body = "".join(f'<a href="{link}">{link}</a> ' for link in links)
    html = "<html><body><nav><a href='/nav.html'>Home</a></nav><h1>Kurs</h1><main>"
    html += f"<h2>Folien</h2>{body}</main></body></html>"
    _ = (root / "index.html").write_text(html)


def write_file(root: Path, name: str, content: str, mtime: int) -> None:
    path = root / name
    _ = path.write_text(content)
    os.utime(path, (mtime, mtime))


def test_sync(site: tuple[Path, str], tmp_path: Path) -> None:
    root, base = site
    out = tmp_path / "out"
    notes = tmp_path / "notes.txt"
    config = tmp_path / "courses.toml"
    record = tmp_path / "record.py"  # stands in for notify-send
    _ = record.write_text(
        "import sys\n"
        + "with open(sys.argv[1], 'a') as f: f.write(f'{sys.argv[2]}\\n{sys.argv[3]}\\n--\\n')"
    )
    _ = config.write_text(f"""
[defaults]
output_root = "{out}"
state_dir = "{tmp_path / "state"}"
notify_cmd = ["{sys.executable}", "{record}", "{notes}", "{{title}}", "{{body}}"]

[defaults.ytdlp]
external_downloader = {{}}  # tests must not depend on aria2c

[sources.kurs]
url = "coursedl-web:{base}index.html"
output_dir = "Kurs"
transform = '''
Folien/secret.pdf --> !
Folien/(.*)\\.pdf -re-> Slides/{{g1}}.pdf
'''
""")

    def sync() -> int:
        return main(["-c", str(config), "sync", "--notify"])

    def notifications() -> list[str]:
        return notes.read_text().split("--\n")[:-1] if notes.exists() else []

    # Run 1: baseline. Files arrive, the unhandled link is only counted, not warned about.
    write_file(root, "a.pdf", "A", 1_700_000_000)
    write_file(root, "b.pdf", "B", 1_700_000_000)
    write_file(root, "secret.pdf", "S", 1_700_000_000)
    write_page(root, "a.pdf", "b.pdf", "secret.pdf", "other.html")
    assert sync() == 0
    assert sorted(p.name for p in (out / "Kurs/Slides").iterdir()) == ["a.pdf", "b.pdf"]
    assert notifications() == [
        "coursedl: 2 new\nkurs:\n  new: Kurs/Slides/a.pdf\n  new: Kurs/Slides/b.pdf\n"
    ]

    # Run 2: nothing new, no notification.
    assert sync() == 0
    assert len(notifications()) == 1

    # Run 3: a.pdf deleted locally stays deleted; b.pdf changed online gets a second copy;
    # a new file that no rule maps and a new unhandled link appear.
    (out / "Kurs/Slides/a.pdf").unlink()
    write_file(root, "b.pdf", "B2", 1_800_000_000)
    write_file(root, "c.ipynb", "{}", 1_800_000_000)
    write_page(
        root,
        "a.pdf",
        "b.pdf",
        "secret.pdf",
        "other.html",
        "c.ipynb",
        "https://gitlab.example.org/repo",
    )
    assert sync() == 0
    slides = out / "Kurs/Slides"
    assert sorted(p.name for p in slides.iterdir()) == ["b (2027-01-15).pdf", "b.pdf"]
    assert (slides / "b.pdf").read_text() == "B"
    assert (slides / "b (2027-01-15).pdf").read_text() == "B2"
    assert not (out / "Kurs/Folien/c.ipynb").exists()  # held back until a rule maps it
    title, *body = notifications()[1].splitlines()
    assert title == "coursedl: 1 updated, 2 warnings"
    assert any(line.startswith("  warning: no rule matches: Folien/c.ipynb") for line in body)
    assert any("new unhandled link: https://gitlab.example.org/repo (on " in x for x in body)

    # Run 4: the same links and the same unmatched file again: silent.
    assert sync() == 0
    assert len(notifications()) == 2

    # A rule for notebooks: the held-back file arrives where the rule puts it.
    _ = config.write_text(
        config.read_text().replace(
            "Slides/{g1}.pdf\n", "Slides/{g1}.pdf\nFolien/(.*) -re-> Notebooks/{g1}\n"
        )
    )
    assert sync() == 0
    assert (out / "Kurs/Notebooks/c.ipynb").read_text() == "{}"
    assert notifications()[2].startswith("coursedl: 1 new\n")

    # --restore-missing brings back the locally deleted file; identical files stay as they are.
    assert main(["-c", str(config), "sync", "--restore-missing"]) == 0
    assert (slides / "a.pdf").read_text() == "A"
    assert sorted(p.name for p in slides.iterdir()) == ["a.pdf", "b (2027-01-15).pdf", "b.pdf"]

    # Run 5 and 6: the page loses all files: one warning, then quiet.
    write_page(root, "other.html")
    assert sync() == 0
    assert "no files listed any more (last run: 4)" in notifications()[3]
    assert sync() == 0
    assert len(notifications()) == 4

    # `links` lists what no rule handles.
    assert main(["-c", str(config), "links", "kurs"]) == 0


def test_failed_source_warns_once(site: tuple[Path, str], tmp_path: Path) -> None:
    _, base = site
    config = tmp_path / "courses.toml"
    _ = config.write_text(f"""
[defaults]
output_root = "{tmp_path / "out"}"
state_dir = "{tmp_path / "state"}"
notify_cmd = ["false"]

[sources.gone]
url = "coursedl-web:{base}missing.html"
output_dir = "Gone"
""")
    # notify_cmd fails on the first run (there is a warning), no notification on the second.
    assert main(["-c", str(config), "sync", "--notify"]) == 1
    state = (tmp_path / "state/gone.json").read_text()
    assert '"status":"failed"' in state.replace(" ", "")


def test_place_backup_old(tmp_path: Path) -> None:
    dest = tmp_path / "x.pdf"
    _ = dest.write_text("old")
    os.utime(dest, (1_700_000_000, 1_700_000_000))
    new = tmp_path / "staged"
    _ = new.write_text("new")
    assert place(new, dest, "backup-old", restoring=False) == ("updated", dest)
    assert dest.read_text() == "new"
    assert (tmp_path / "x (local 2023-11-14).pdf").read_text() == "old"
    # Same content again: nothing to report, staged file removed.
    _ = new.write_text("new")
    assert place(new, dest, "backup-old", restoring=False) is None
    assert not new.exists()


class _LoginHandler(_QuietHandler):
    """A page behind a password form, like a WordPress protected post: the form posts to
    /login, which sets a cookie and redirects back."""

    def _logged_in(self) -> bool:
        return "pass=ok" in (self.headers.get("Cookie") or "")

    @override
    def do_GET(self) -> None:
        if self.path.endswith(".pdf") and not self._logged_in():
            self.send_error(403)
        elif self.path == "/index.html" and not self._logged_in():
            body = (
                b'<html><body><main><form action="/login?action=postpass" method="post">'
                b'<input type="hidden" name="redirect_to" value="/index.html">'
                b'<input type="password" name="post_password"><input type="submit" name="Submit"'
                b' value="Enter"></form></main></body></html>'
            )
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            _ = self.wfile.write(body)
        else:
            super().do_GET()

    posts: int = 0

    def do_POST(self) -> None:
        _LoginHandler.posts += 1
        length = int(self.headers.get("Content-Length") or 0)
        form = self.rfile.read(length).decode()
        self.send_response(302)
        if "post_password=s3cret" in form and "Submit=Enter" in form:
            self.send_header("Set-Cookie", "pass=ok; Path=/")
        self.send_header("Location", "/index.html")
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.mark.parametrize(("password", "code"), [("s3cret", 0), ("wrong", 1)])
def test_form_login(tmp_path: Path, password: str, code: int) -> None:
    root = tmp_path / "site"
    root.mkdir()
    write_file(root, "a.pdf", "A", 1_700_000_000)
    write_page(root, "a.pdf")
    handler = functools.partial(_LoginHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = tmp_path / "courses.toml"
    _ = config.write_text(f"""
[defaults]
output_root = "{tmp_path / "out"}"
state_dir = "{tmp_path / "state"}"

[defaults.ytdlp]
external_downloader = {{}}

[sources.kurs]
url = "coursedl-web:http://127.0.0.1:{server.server_port}/index.html"
output_dir = "Kurs"
auth = {{ type = "form", password = "{password}" }}
""")
    _LoginHandler.posts = 0
    try:
        assert main(["-c", str(config), "sync"]) == code
        if code == 0:
            # The session cookie is kept in the state folder: no second login.
            write_file(root, "b.pdf", "B", 1_700_000_000)
            write_page(root, "a.pdf", "b.pdf")
            assert main(["-c", str(config), "sync"]) == 0
            assert (tmp_path / "out/Kurs/Folien/b.pdf").exists()
            assert _LoginHandler.posts == 1
    finally:
        server.shutdown()
    assert (tmp_path / "out/Kurs/Folien/a.pdf").exists() == (code == 0)


def test_config_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coursedl import config

    # Relative paths count from the config's folder; the state goes into output_root.
    _ = (tmp_path / "coursedl.toml").write_text("""
[defaults]
output_root = "lectures"

[sources.k]
url = "coursedl-web:https://example.org/"
output_dir = "Kurs"
""")
    cfg = config.load(tmp_path / "coursedl.toml")
    assert cfg.defaults.output_root == tmp_path / "lectures"
    assert cfg.defaults.state_dir == tmp_path / "lectures/.coursedl"
    (tmp_path / "lectures").mkdir()
    # Default config: coursedl.toml in the current folder.
    monkeypatch.chdir(tmp_path / "lectures")
    assert main(["links", "k"]) == 2  # no config here


def test_failed_run_keeps_state() -> None:
    # A failed run saw nothing: the first-run baseline and the remembered warnings stay.
    state = SourceState(unmatched={"a.pdf": "2026-01-01"}, conflicts={"c.txt": "2026-01-01"})
    new = update_state(state, SourceReport("s"), {"https://x/u": "https://x/"}, {}, None, [])
    assert new.links is None
    assert (new.unmatched, new.conflicts) == (state.unmatched, state.conflicts)


def test_broken_subpage_is_unhandled(site: tuple[Path, str], tmp_path: Path) -> None:
    root, base = site
    write_page(root, "gone.html", "a.pdf")
    write_file(root, "a.pdf", "A", 1_700_000_000)
    config = tmp_path / "courses.toml"
    _ = config.write_text(f"""
[defaults]
output_root = "{tmp_path / "out"}"
state_dir = "{tmp_path / "state"}"

[sources.kurs]
url = "coursedl-web:{base}index.html"
output_dir = "Kurs"
follow_regex = "gone"
""")
    assert main(["-c", str(config), "sync"]) == 0
    assert (tmp_path / "out/Kurs/Folien/a.pdf").exists()
    state = SourceState.load(tmp_path / "state/kurs.json")
    assert state.status == "ok"
    assert state.links and f"{base}gone.html" in state.links
