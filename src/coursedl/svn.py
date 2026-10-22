"""`coursedl-svn:` an SVN folder, kept as a working copy with the svn client.

The source's `output_dir` is the working copy: checked out on the first run, `svn update`
afterwards. svn does the tracking, so its rules apply instead of coursedl's (see
docs/files.md): files deleted online are deleted locally, local deletions are restored,
local edits are merged, and a change that can't be merged is a conflict, which is
reported. Transform rules don't apply; the repository's layout is kept.

Login: svn's own credential cache (`~/.subversion`, or the desktop keyring). The user logs
in once by running any svn command on the URL interactively (`svn ls URL`); coursedl then
runs svn non-interactively with the cached credentials and reports a failed login, so
there is no password in coursedl's config.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from coursedl.movepp import Status

# `svn update`/`checkout` lines: text, properties, lock, tree conflict, then the path.
UPDATE_LINE = re.compile(r"^([ADUCGE ])([UCG ])([B ])([C ]) (.+)$")
STATUS_FOR = {"A": "new", "U": "updated", "G": "updated", "D": "deleted"}


@dataclass
class SvnResult:
    files: list[tuple[Status, Path]] = field(default_factory=list)
    conflicts: list[Path] = field(default_factory=list)


# svn's errors for missing or rejected credentials, and for a login without access (403).
AUTH_ERRORS = ("E215004", "E170001")
FORBIDDEN = "E175013"


class NoAccessError(RuntimeError):
    """Logged in, but the account may not read the folder (e.g. a course's solutions for
    someone who is not a member of the course)."""


def _svn(args: list[str], url: str) -> str:
    if not shutil.which("svn"):
        raise RuntimeError("the svn client is not installed (package: subversion)")
    # --non-interactive: never wait for a password prompt under a timer. The timeout ends a
    # stalled run that would otherwise hold the source's lock forever.
    res = subprocess.run(
        ["svn", "--non-interactive", *args], capture_output=True, text=True, timeout=3600
    )
    if res.returncode != 0:
        if FORBIDDEN in res.stderr:
            raise NoAccessError(
                f"not accessible: {url} (your login has no access; not a member of the course?)"
            )
        if any(code in res.stderr for code in AUTH_ERRORS):
            raise RuntimeError(
                f"svn is not logged in for {url}: run `svn ls {url}` once and log in"
            )
        raise RuntimeError(f"svn {args[0]} failed: {res.stderr.strip()}")
    return res.stdout


def sync(url: str, wc: Path, dry_run: bool) -> SvnResult:
    """Checks `url` out into `wc`, or updates the working copy there."""
    result = SvnResult()
    if not (wc / ".svn").exists():
        if wc.exists() and any(wc.iterdir()):
            raise RuntimeError(f"{wc} is not empty and not an svn working copy")
        if dry_run:
            print(f"would check out {url} to {wc}")
            return result
        out = _svn(["checkout", url, str(wc)], url)
    else:
        current = _svn(["info", "--show-item", "url", str(wc)], url).strip()
        if current.rstrip("/") != url.rstrip("/"):
            raise RuntimeError(f"{wc} is a working copy of {current}, not {url}")
        if dry_run:
            # `status -u`: "*" marks what an update would bring.
            for line in _svn(["status", "-u", str(wc)], url).splitlines():
                if line[8:9] == "*":
                    print(f"would update {line[21:].strip()}")
            return result
        out = _svn(["update", "--accept", "postpone", str(wc)], url)

    for line in out.splitlines():
        if line.startswith("Restored '"):
            result.files.append(("restored", Path(line[10:].rstrip("'"))))
        elif (m := UPDATE_LINE.match(line)) and (status := STATUS_FOR.get(m[1])):
            path = Path(m[5].strip())
            if status == "deleted" or path.is_file():  # folders are listed too
                result.files.append((status, path))  # pyright: ignore[reportArgumentType]

    # Conflicts that are still open, new or from earlier runs: text (column 1) or tree
    # conflicts (column 7) in `svn status`.
    for line in _svn(["status", str(wc)], url).splitlines():
        if len(line) > 8 and "C" in (line[0], line[6]):
            result.conflicts.append(Path(line[8:].strip()))
    return result
