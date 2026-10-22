"""Per-source state, warnings that fire once, run report, history and notification."""

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from coursedl.movepp import Status

MAX_BODY_LINES = 15


class SourceState(BaseModel):
    """`state_dir/<source>.json`. `links` is `None` before the first run (baseline)."""

    links: dict[str, dict[str, str]] | None = None  # url -> {"first_seen", "page"}
    status: Literal["ok", "failed", "empty", "inaccessible"] = "ok"
    listed: int = 0
    unmatched: dict[str, str] = {}  # remote path -> first seen; files no rule matched
    conflicts: dict[str, str] = {}  # local path -> first seen; open svn conflicts

    @classmethod
    def load(cls, path: Path) -> SourceState:
        return cls.model_validate_json(path.read_text()) if path.exists() else cls()

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        _ = tmp.write_text(self.model_dump_json(indent=1))
        _ = tmp.replace(path)


@dataclass
class SourceReport:
    name: str
    files: list[tuple[Status, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)


def update_state(
    state: SourceState,
    rep: SourceReport,
    unhandled: dict[str, str],
    unmatched: dict[str, str],
    listed: int | None,
    conflicts: list[str] | None = None,
    inaccessible: str | None = None,
) -> SourceState:
    """Turns this run's findings into warnings. Each one fires only once: links and
    unmatched files when they first appear, "failed"/"empty" when the status changes.
    `listed=None`: listing failed; then the run saw only part of the source, so what is
    remembered stays and only new findings are added."""
    links = state.links
    if links is None:
        # The first run that lists the source is the baseline; a failed one is not.
        if listed is not None:
            if unhandled:
                rep.info.append(
                    f"{len(unhandled)} links not handled, see `coursedl links {rep.name}`"
                )
            links = {}
    else:
        rep.warnings += [
            f"new unhandled link: {url} (on {page})"
            for url, page in unhandled.items()
            if url not in links
        ]
    today = str(date.today())
    if links is not None:
        for url, page in unhandled.items():
            _ = links.setdefault(url, {"first_seen": today, "page": page})

    # Unmatched files are kept only while they stay unmatched: once a rule maps them they
    # are downloaded, and if they fall out of the rules again they are reported again.
    rep.warnings += [
        f"no rule matches: {path} ({url})"
        for path, url in unmatched.items()
        if path not in state.unmatched
    ]
    still = {path: state.unmatched.get(path, today) for path in unmatched}
    rep.warnings += [
        f"svn conflict: {path} (edit the file, then `svn resolve --accept working`)"
        for path in conflicts or []
        if path not in state.conflicts
    ]
    open_conflicts = {path: state.conflicts.get(path, today) for path in conflicts or []}
    if listed is None:
        still = state.unmatched | still
        open_conflicts = state.conflicts | open_conflicts

    status: Literal["ok", "failed", "empty", "inaccessible"] = "ok"
    if inaccessible:
        # Not an error of the run: the source exists, the account may just not read it.
        status = "inaccessible"
        if state.status != "inaccessible":
            rep.warnings.append(inaccessible)
    elif rep.errors or listed is None:
        status = "failed"
        if state.status != "failed":
            rep.warnings.append(f"source failed: {rep.errors[0] if rep.errors else '?'}")
    elif listed == 0 and state.listed > 0:
        status = "empty"
        if state.status != "empty":
            rep.warnings.append(f"no files listed any more (last run: {state.listed})")
    return SourceState(
        links=links,
        status=status,
        listed=state.listed if listed is None else listed,
        unmatched=still,
        conflicts=open_conflicts,
    )


def append_history(path: Path, reports: list[SourceReport]) -> None:
    line = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "sources": [asdict(r) for r in reports],
    }
    with path.open("a") as f:
        _ = f.write(json.dumps(line, ensure_ascii=False) + "\n")


def summary(reports: list[SourceReport]) -> tuple[str, str] | None:
    """(title, body) for the notification; `None` if there is nothing to report."""
    counts: dict[str, int] = {}
    lines: list[str] = []
    for r in reports:
        if not (r.files or r.warnings):
            continue
        lines.append(f"{r.name}:")
        for status, path in r.files:
            counts[status] = counts.get(status, 0) + 1
            lines.append(f"  {status}: {path}")
        lines += [f"  warning: {w}" for w in r.warnings]
    if not lines:
        return None
    n_warn = sum(len(r.warnings) for r in reports)
    parts = [f"{n} {s}" for s, n in counts.items()] + ([f"{n_warn} warnings"] if n_warn else [])
    if len(lines) > MAX_BODY_LINES:
        rest = len(lines) - MAX_BODY_LINES
        lines = [*lines[:MAX_BODY_LINES], f"… and {rest} more (see history.jsonl)"]
    return f"coursedl: {', '.join(parts)}", "\n".join(lines)


def notify(cmd: list[str], title: str, body: str) -> None:
    """Runs `notify_cmd`. Placeholders are filled per argument, no shell involved.
    Plain replace instead of str.format, so other braces in the command stay as they are."""
    args = [a.replace("{title}", title).replace("{body}", body) for a in cmd]
    _ = subprocess.run(args, check=True, timeout=60)
