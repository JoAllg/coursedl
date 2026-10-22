"""Remote path -> local path, and moving downloads into place without overwriting."""

import filecmp
import functools
import glob
import itertools
import logging
import os
import shutil
from collections.abc import Mapping
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Literal, override

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import sanitize_filename

from coursedl.config import Source
from coursedl.transform import Transformer

Status = Literal["new", "updated", "restored", "deleted"]

log = logging.getLogger(__name__)
_transformer = functools.cache(Transformer)


def local_path(
    ydl: YoutubeDL, source: Source, info: Mapping[str, Any]
) -> tuple[PurePosixPath, PurePosixPath | None]:
    """(remote path, local path). Template for the entry's `cd_kind` -> remote path, PFERD
    transform -> local path, relative to the source's `output_dir`. The local path is
    `None` if a transform rule excludes the entry (`!`)."""
    kind = info.get("cd_kind")
    template = source.all_templates.get(kind or "")
    if template is None:
        raise ValueError(f"no template for entry kind {kind!r}")
    # A "/" in metadata (e.g. a video title) must not create folders; only the cd_* fields
    # carry real path separators. Others get U+29F8 like in yt-dlp's own file names.
    # (evaluate_outtmpl keeps slashes, prepare_filename would replace all of them.)
    fields: Any = {
        k: v.replace("/", "\u29f8") if isinstance(v, str) and not k.startswith("cd_") else v
        for k, v in info.items()
    }
    remote = PurePosixPath(ydl.evaluate_outtmpl(template, fields))
    local = _transformer(source.transform).transform(remote)
    if local is None:
        return remote, None
    parts = [p for p in local.parts if p not in ("/", ".")]
    if not parts or ".." in parts:
        raise ValueError(f"local path {local} leaves the output folder")
    return remote, PurePosixPath(*(sanitize_filename(p) for p in parts))


def unmatched(source: Source, remote: PurePosixPath) -> bool:
    """Whether the source has rules but none of them matches the file."""
    return bool(source.transform.strip()) and not _transformer(source.transform).matches(remote)


def place(src: Path, dest: Path, on_change: str, restoring: bool) -> tuple[Status, Path] | None:
    """Moves `src` to `dest` without ever overwriting a file. `None` if `dest` already has
    the same content (nothing new for the user)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        _ = shutil.move(src, dest)
        return ("restored" if restoring else "new"), dest
    # Also compare with earlier copies ("name (2026-05-01).pdf"), so a re-download
    # (--restore-missing, a changed ID) doesn't add the same version again.
    copies = dest.parent.glob(f"{glob.escape(dest.stem)} (*){glob.escape(dest.suffix)}")
    if any(filecmp.cmp(src, c, shallow=False) for c in [dest, *copies]):
        src.unlink()
        return None
    if on_change == "backup-old":
        old = date.fromtimestamp(dest.stat().st_mtime)
        _ = shutil.move(dest, _free_name(dest, f"local {old}"))
        _ = shutil.move(src, dest)
        return "updated", dest
    # keep-both: the new version gets the remote date (yt-dlp sets mtime from it).
    new = _free_name(dest, str(date.fromtimestamp(src.stat().st_mtime)))
    _ = shutil.move(src, new)
    return "updated", new


def _free_name(path: Path, label: str) -> Path:
    for i in itertools.count(1):
        tag = label if i == 1 else f"{label}-{i}"
        candidate = path.with_name(f"{path.stem} ({tag}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise AssertionError  # unreachable


class MovePP(PostProcessor):
    """Runs after yt-dlp has downloaded an entry into the staging folder."""

    def __init__(
        self,
        ydl: YoutubeDL,
        source: Source,
        output_root: Path,
        on_change: str,
        restoring: bool,
        files: list[tuple[Status, str]],
    ):
        super().__init__(ydl)
        self._ydl: YoutubeDL = ydl
        self._source: Source = source
        self._root: Path = output_root
        self._on_change: str = on_change
        self._restoring: bool = restoring
        self.files: list[tuple[Status, str]] = files

    @override
    def run(self, information: Any) -> tuple[list[str], Any]:
        _, rel = local_path(self._ydl, self._source, information)
        assert rel is not None  # excluded entries never get here (match_filter)
        dest = self._root / self._source.output_dir / rel
        src = Path(information["filepath"])
        if ts := information.get("timestamp"):
            os.utime(src, (ts, ts))
        if placed := place(src, dest, self._on_change, self._restoring):
            status, path = placed
            self.files.append((status, str(path.relative_to(self._root))))
            log.info("[coursedl] %s: %s", status, path)
        else:
            log.info("[coursedl] unchanged: %s", dest)
        return [], information
