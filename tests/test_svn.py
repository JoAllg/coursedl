"""`coursedl-svn:` against a local repository (file://), with the real svn client."""

import shutil
import subprocess
from pathlib import Path

import pytest

from coursedl.cli import main

needs_svn = pytest.mark.skipif(
    not (shutil.which("svn") and shutil.which("svnadmin")), reason="svn not installed"
)


def run(*cmd: str | Path, cwd: Path | None = None) -> None:
    _ = subprocess.run([str(c) for c in cmd], check=True, capture_output=True, cwd=cwd)


def commit(work: Path, message: str) -> None:
    run("svn", "add", "--force", "-q", ".", cwd=work)
    run("svn", "commit", "-q", "-m", message, cwd=work)


@needs_svn
def test_svn(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run("svnadmin", "create", repo)
    url = f"file://{repo}"
    # A second working copy plays the lecturer.
    lecturer = tmp_path / "lecturer"
    run("svn", "checkout", "-q", url, lecturer)
    (lecturer / "folien").mkdir()
    _ = (lecturer / "folien/vorlesung-01.pdf").write_text("one\n")
    commit(lecturer, "slides 1")

    base = tmp_path / "lectures"
    base.mkdir()
    notes = tmp_path / "notes.txt"
    _ = (base / "coursedl.toml").write_text(f"""
[defaults]
notify_cmd = ["sh", "-c", "echo \\"$0\\" >> {notes}", "{{title}}"]

[sources.svn]
url = "coursedl-svn:{url}"
output_dir = "Kurs/SVN"
""")
    config = base / "coursedl.toml"
    wc = base / "Kurs/SVN"

    def sync() -> int:
        return main(["-c", str(config), "sync", "--notify"])

    # Run 1: checkout.
    assert sync() == 0
    assert (wc / "folien/vorlesung-01.pdf").read_text() == "one\n"
    assert notes.read_text().splitlines() == ["coursedl: 1 new"]

    # Run 2: a new and a changed file online; a locally deleted file comes back (svn).
    _ = (lecturer / "folien/vorlesung-02.pdf").write_text("two\n")
    commit(lecturer, "slides 2")
    assert sync() == 0
    assert (wc / "folien/vorlesung-02.pdf").exists()

    # Run 3: the same line changed online and locally: a conflict, reported once.
    _ = (lecturer / "folien/vorlesung-01.pdf").write_text("one, fixed\n")
    commit(lecturer, "fix")
    _ = (wc / "folien/vorlesung-01.pdf").write_text("one, my notes\n")
    assert sync() == 0
    last = notes.read_text().splitlines()[-1]
    assert last == "coursedl: 1 warnings"
    state = (base / ".coursedl/svn.json").read_text()
    assert "folien/vorlesung-01.pdf" in state
    assert sync() == 0
    assert notes.read_text().splitlines()[-1] == last  # no second notification
    assert len(notes.read_text().splitlines()) == 3

    # A folder that is not a working copy is never overwritten.
    _ = config.write_text(config.read_text().replace('"Kurs/SVN"', '"Kurs"'))
    assert sync() == 1


def test_not_logged_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coursedl import svn

    # An svn that answers like an SVN server without cached credentials.
    fake = tmp_path / "bin/svn"
    fake.parent.mkdir()
    _ = fake.write_text(
        '#!/bin/sh\necho "svn: E215004: No more credentials or we tried too many times." >&2\n'
        + "exit 1\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:/usr/bin:/bin")
    with pytest.raises(RuntimeError, match=r"not logged in .* run `svn ls https://x/svn/` once"):
        _ = svn.sync("https://x/svn/", tmp_path / "wc", dry_run=False)


def test_not_accessible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Logged in, but not a member of the course: the SVN server answers 403.
    fake = tmp_path / "bin/svn"
    fake.parent.mkdir()
    _ = fake.write_text("#!/bin/sh\necho \"svn: E175013: Access to '/x' forbidden\" >&2\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:/usr/bin:/bin")
    notes = tmp_path / "notes.txt"
    config = tmp_path / "coursedl.toml"
    _ = config.write_text(f"""
[defaults]
notify_cmd = ["sh", "-c", "echo \\"$0\\" >> {notes}", "{{body}}"]

[sources.sol]
url = "coursedl-svn:https://x/svn/loesungen/"
output_dir = "Solutions"
""")
    # Reported once as "not accessible", not as a failure (exit code 0).
    assert main(["-c", str(config), "sync", "--notify"]) == 0
    assert "warning: not accessible: https://x/svn/loesungen/" in notes.read_text()
    assert main(["-c", str(config), "sync", "--notify"]) == 0
    assert notes.read_text().count("not accessible") == 1
