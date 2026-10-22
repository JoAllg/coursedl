"""`coursedl sync` and `coursedl links`."""

import argparse
import base64
import fcntl
import logging
import shutil
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, final

from pydantic import ValidationError
from yt_dlp import YoutubeDL

from coursedl import config, svn
from coursedl.config import BasicAuth, Config, CookiesAuth, FormAuth, Source
from coursedl.extractors import EXTRACTORS, Context
from coursedl.movepp import MovePP, local_path, unmatched
from coursedl.report import (
    SourceReport,
    SourceState,
    append_history,
    notify,
    summary,
    update_state,
)

log = logging.getLogger("coursedl")
SVN_PREFIX = "coursedl-svn:"


@final
class _YdlLogger:
    """Routes yt-dlp's output through logging and collects its errors for the report."""

    def __init__(self, errors: list[str]):
        self.errors = errors

    def debug(self, msg: str) -> None:
        # yt-dlp sends normal screen output here too; only "[debug] " lines are debug.
        if msg.startswith("[debug] "):
            log.debug(msg)
        else:
            log.info(msg)

    def info(self, msg: str) -> None:
        log.info(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def error(self, msg: str) -> None:
        # External downloaders' stderr arrives here too, partly as blank lines.
        if text := msg.removeprefix("ERROR: ").strip():
            self.errors.append(text.splitlines()[0])
        log.error(msg)


def _context(name: str, src: Source) -> Context:
    ctx = Context(name, src)
    if isinstance(src.auth, BasicAuth | FormAuth):
        ctx.auth_password = src.auth.resolve_password(name)
    if isinstance(src.auth, BasicAuth):
        token = base64.b64encode(f"{src.auth.username}:{ctx.auth_password}".encode()).decode()
        ctx.auth_headers = {"Authorization": f"Basic {token}"}
    return ctx


def sync_source(cfg: Config, name: str, args: argparse.Namespace) -> SourceReport:
    src = cfg.sources[name]
    state_dir = cfg.defaults.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    rep = SourceReport(name)
    # One lock per source: runs for different sources may overlap, the same one may not.
    with (state_dir / f"{name}.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            rep.info.append("another run is busy with this source, skipped")
            return rep
        state_path = state_dir / f"{name}.json"
        state = SourceState.load(state_path)
        ctx = Context(name, src)
        listed: int | None = None
        conflicts: list[str] = []
        inaccessible: str | None = None
        try:
            ctx = _context(name, src)
            if src.url.startswith(SVN_PREFIX):
                listed, conflicts = _run_svn(cfg, src, args, rep)
            else:
                # The staging folder lives in state_dir; MovePP moves files out of it.
                with tempfile.TemporaryDirectory(dir=state_dir, prefix=f"{name}.staging-") as t:
                    listed = _run_ytdlp(cfg, src, ctx, args, Path(t), rep)
        except svn.NoAccessError as e:
            inaccessible = str(e)
            rep.info.append(inaccessible)
        except Exception as e:  # e.g. password_cmd failed; the other sources still run
            log.exception("%s: %s", name, e)
            rep.errors.append(str(e))
        new_state = update_state(
            state, rep, ctx.unhandled, ctx.unmatched, listed, conflicts, inaccessible
        )
        if not args.dry_run:
            new_state.save(state_path)
    return rep


def _run_svn(
    cfg: Config, src: Source, args: argparse.Namespace, rep: SourceReport
) -> tuple[int, list[str]]:
    """Checkout/update of an svn source. Returns (files in the working copy, open
    conflicts as paths relative to output_root)."""
    root = cfg.defaults.output_root
    wc = root / src.output_dir
    result = svn.sync(src.url.removeprefix(SVN_PREFIX), wc, args.dry_run)
    for status, path in result.files:
        rep.files.append((status, str(path.relative_to(root))))
    listed = sum(1 for f in wc.rglob("*") if f.is_file() and ".svn" not in f.parts)
    return listed, [str(p.relative_to(root)) for p in result.conflicts]


def _run_ytdlp(
    cfg: Config,
    src: Source,
    ctx: Context,
    args: argparse.Namespace,
    staging: Path,
    rep: SourceReport,
) -> int | None:
    """Lists and downloads one source. Returns the number of listed entries, or `None` if
    the listing itself failed."""

    claimed: dict[PurePosixPath, str] = {}  # local path -> entry ID, for this run

    def match_filter(info: Mapping[str, Any], incomplete: bool) -> str | None:
        # Only complete entries from our extractors; YouTube entries are complete only
        # after their own extraction, when title and upload date are known.
        if incomplete or "cd_kind" not in info:
            return None
        remote, local = local_path(ydl, src, info)
        if src.unmatched == "warn" and unmatched(src, remote):
            if args.explain or args.dry_run:
                print(f"{remote} -> ? (no rule matches, not downloaded)")
            ctx.unmatched[str(remote)] = info.get("url") or ""
            return "no transform rule matches"
        if args.explain or args.dry_run:
            print(f"{remote} -> {local or '! (excluded)'}")
        # Transform rules that map two files to one name would make the second look like
        # a new version of the first (keep-both copy).
        if local and claimed.setdefault(local, info["id"]) != info["id"]:
            rep.warnings.append(f"{remote} and another file both map to {local}; fix the rules")
        # Rejected entries are not written to the archive: changing the rule later picks
        # them up again.
        return None if local else "excluded by transform rule"

    archive = cfg.defaults.state_dir / f"{ctx.name}.archive"
    # Any: the stubs' _Params has no room for our "coursedl" key.
    params: Any = {
        **cfg.defaults.ytdlp,
        **src.ytdlp,
        "coursedl": ctx,
        "paths": {"home": str(staging), "temp": str(staging)},
        "outtmpl": "%(autonumber)s.%(ext)s",
        # --restore-missing: yt-dlp skips everything its archive lists, so it gets an empty
        # one; what it downloads is added to the real archive afterwards.
        "download_archive": str(staging / "archive" if args.restore_missing else archive),
        # Reads `ydl` only when called, after it is created below (evaluate_outtmpl).
        "match_filter": match_filter,
        "simulate": args.dry_run,
        "ignoreerrors": True,
        "noprogress": True,
        "logger": _YdlLogger(rep.errors),
        "verbose": args.verbose,
    }
    if isinstance(src.auth, BasicAuth):
        # aria2c would send the Authorization header on after a redirect to another host;
        # yt-dlp's own downloader drops it there.
        params.pop("external_downloader", None)
    if isinstance(src.auth, CookiesAuth):
        if src.auth.file:
            params["cookiefile"] = str(src.auth.file)
        if src.auth.browser:
            params["cookiesfrombrowser"] = (src.auth.browser,)
    else:
        # A cookie file per source, kept between runs: a form login stays valid, and
        # aria2c reads this file instead of yt-dlp writing a temporary copy per download.
        params["cookiefile"] = str(cfg.defaults.state_dir / f"{ctx.name}.cookies")

    with YoutubeDL(params) as ydl:
        for ie in EXTRACTORS:
            ydl.add_info_extractor(ie())
        ydl.add_post_processor(
            MovePP(
                ydl,
                src,
                cfg.defaults.output_root,
                cfg.defaults.on_change,
                args.restore_missing,
                rep.files,
            ),
            when="after_move",
        )
        ie_key = next(ie.ie_key() for ie in EXTRACTORS if ie.suitable(src.url))
        info = ydl.extract_info(src.url, ie_key=ie_key)
    if args.restore_missing and (new := staging / "archive").exists():
        old = set(archive.read_text().splitlines()) if archive.exists() else set()
        with archive.open("a") as f:
            f.writelines(f"{line}\n" for line in new.read_text().splitlines() if line not in old)
    return ctx.listed if info else None


def _print_report(rep: SourceReport) -> None:
    lines = [f"  {s}: {p}" for s, p in rep.files]
    lines += [f"  warning: {w}" for w in rep.warnings]
    lines += [f"  error: {e}" for e in rep.errors]
    lines += [f"  {i}" for i in rep.info]
    print(f"== {rep.name}" + ("" if lines else ": nothing new"))
    for line in lines:
        print(line)


def cmd_sync(cfg: Config, args: argparse.Namespace) -> int:
    names: list[str] = args.sources or list(cfg.sources)
    if unknown := [n for n in names if n not in cfg.sources]:
        print(f"unknown source(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    if "aria2c" in str(cfg.defaults.ytdlp.get("external_downloader")) and not shutil.which(
        "aria2c"
    ):
        log.info("aria2c is not installed; yt-dlp's own downloader is used instead")

    reports = [sync_source(cfg, n, args) for n in names]
    print()
    for rep in reports:
        _print_report(rep)
    if args.dry_run:
        return 0

    append_history(cfg.defaults.state_dir / "history.jsonl", reports)
    code = 1 if any(r.errors for r in reports) else 0
    if args.notify and (msg := summary(reports)):
        try:
            notify(cfg.defaults.notify_cmd, *msg)
        except Exception as e:  # the report is in history.jsonl anyway
            log.error("notify_cmd failed: %s", e)
            code = 1
    return code


def cmd_links(cfg: Config, args: argparse.Namespace) -> int:
    state = SourceState.load(cfg.defaults.state_dir / f"{args.source}.json")
    links = sorted((state.links or {}).items(), key=lambda kv: (kv[1]["first_seen"], kv[0]))
    for url, meta in links:
        print(f"{meta['first_seen']}  {url}  (on {meta['page']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="coursedl", description="Sync course material from university websites."
    )
    _ = p.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path(config.CONFIG_NAME),
        help=f"default: {config.CONFIG_NAME} in the current folder",
    )
    _ = p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="download new and changed files")
    _ = s.add_argument("sources", nargs="*", help="default: all")
    _ = s.add_argument("--dry-run", action="store_true", help="list only, change nothing")
    _ = s.add_argument("--explain", action="store_true", help="print remote -> local paths")
    _ = s.add_argument("--notify", action="store_true", help="run notify_cmd if there is news")
    _ = s.add_argument(
        "--restore-missing",
        action="store_true",
        help="ignore the archive: download everything whose local file is missing",
    )
    links = sub.add_parser("links", help="list links no rule handles")
    _ = links.add_argument("source")
    args = p.parse_args(argv)

    logging.basicConfig(format="%(message)s", level=logging.DEBUG if args.verbose else logging.INFO)
    if getattr(args, "explain", False):
        logging.getLogger("coursedl.transform").setLevel(logging.DEBUG)
    path: Path = args.config
    try:
        cfg = config.load(path)
    except (OSError, ValidationError) as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 2
    return cmd_sync(cfg, args) if args.cmd == "sync" else cmd_links(cfg, args)
