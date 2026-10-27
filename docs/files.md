# Files: tracking, changes, conflicts, deletions

coursedl never overwrites or deletes your files. What it downloaded is recorded per source, independent of what is on disk, so you can rename, move or delete files freely.

## What is remembered

In `state_dir` (default: `.coursedl/` in `output_root`, next to the lecture folders):

| File | Content |
|---|---|
| `<source>.archive` | yt-dlp download archive: one line per downloaded file version, `<extractor> <remote key>@<version>` (YouTube: the video ID) |
| `<source>.json` | Unhandled links already reported, last status (ok/failed/empty), number of listed files |
| `history.jsonl` | One line per run: new/updated files with their local paths, warnings, errors |
| `<source>.cookies` | Cookies of the source's site (e.g. a form login's session), kept between runs. Delete it to log in again |
| `<source>.lock` | Lock: two runs never work on the same source at once (different sources may) |
| `<source>.staging-*/` | Downloads in progress; removed after each run |

The remote key is the file's URL (path in the share for Nextcloud). The version comes from the source (see [sources.md](sources.md)): ETag, Last-Modified or size from a HEAD request, the SVN revision, or the WebDAV ETag.

## What happens in each case

| Situation | Result |
|---|---|
| New file online | Downloaded, placed at its local path |
| You renamed, moved or deleted the local file | Nothing: the version is in the archive |
| The file changed online (new version) | Downloaded again, **without overwriting** your copy (see `on_change` below) |
| The file changed online, your copy is gone | Downloaded to its normal path |
| The new version is byte-identical to the local file or an earlier copy | No new copy; only the archive is updated |
| A transform rule excludes the file (`!`) | Not downloaded and not recorded: change the rule later and it is picked up |
| No transform rule matches the file (`unmatched = "warn"`, the default) | Not downloaded and not recorded; reported once. Once a rule maps it, the next run downloads it |
| You change a transform rule | Only files downloaded from then on get the new name; existing files stay where they are |
| The file was removed online | Nothing; your copy stays |
| Two different files map to the same local path | Warning in the run; the second is stored like a changed version (below). Fix the rules |
| The run is interrupted | Partial downloads are discarded with the staging folder and not recorded; the next run downloads them again |
| Unknown version (`version = "none"`, or the server sends neither ETag, date nor size) | Downloaded once; later changes are not detected |

### `on_change`: a changed file when your copy exists

`keep-both` (default): your file stays; the new version is saved next to it with the remote modification date: `01 lecture (2026-05-03).pdf`. A second copy on the same day gets `-2`, `-3`, ….

`backup-old`: your file is renamed to `01 lecture (local 2026-04-21).pdf` (its own modification date), the new version takes the original name.

Either way you find both versions, and the notification lists the file as `updated`.

### Getting files back

`coursedl sync --restore-missing` ignores the archive for one run: everything whose local file is missing is downloaded again (`restored`); files that exist and are identical are left alone, changed ones go through `on_change`. Deleting `<source>.archive` has the same effect permanently.

Note: this also brings back files you deleted on purpose.

## Warnings

Each warning is reported once, not in every run (the path conflict only in the run that downloads both files):

| Warning | When |
|---|---|
| New unhandled link | A link on a page that no rule handles (e.g. a new `.ipynb` while `link_regex` doesn't know it, or a link to a new site). The first run of a source only records the existing ones and shows their number |
| Source failed | Listing or a download failed (login, server down). Again only after the source has worked in between |
| No rule matches | A file that no transform rule of its source matches (see above). Reported again if it later falls out of the rules once more |
| No files listed any more | A source that listed files lists none (page redesign, login now needed) |
| Not accessible | An svn folder your login may not read (403). Again only after it was readable in between |
| Two files on one path | See above |

`coursedl links SOURCE` shows all recorded unhandled links. Warnings and new/updated files go to the terminal, `history.jsonl` and, with `--notify`, one notification per run.

## svn sources

`coursedl-svn:` sources are svn working copies ([sources.md](sources.md#coursedl-svn-svn-working-copy)), and svn's rules apply to them instead of the ones above:

| Situation | Result |
|---|---|
| New or changed file online, no local edits | Downloaded / updated in place (`new`, `updated`) |
| File deleted online | Deleted locally too (`deleted` in the report) |
| You deleted a file locally | Restored by the next update (`restored`) |
| You renamed or moved a file locally | svn sees a missing file (restored) and an unknown one (left alone) |
| You edited a file, it also changed online, different lines | Merged by svn |
| You edited a file, it also changed online, same lines | **Conflict**: svn keeps your version and writes the online one and the base next to it (`file.mine`, `file.rNNN`). Reported once ("svn conflict: …"); resolve it, then `svn resolve --accept working <file>` |
| Login missing or no longer accepted | Source fails, reported: log in once with `svn ls <url>` ([access token](sources.md#course-svn-with-a-web-view)) |
| Logged in, but no access to the folder (403) | Reported once as "not accessible: …"; not a failure |

So keep your own notes and renamed copies outside the working copy, or use `svn` to move files.
