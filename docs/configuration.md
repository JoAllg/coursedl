# Configuration

coursedl reads one TOML file: `-c PATH`, default `coursedl.toml` in the current folder. The lecture folders go into `output_root`, and so does coursedl's state, in a hidden `.coursedl/` folder (see [files.md](files.md)). The file is personal: output folder, passwords and the rules that name your files are all in it, none of them in the code. `courses.example.toml` has one example source of each type. Unknown keys are errors, so typos are caught when the file is loaded; regexes and transform rules are checked then too.

```toml
# This file: /home/me/University/coursedl.toml
[defaults]
output_root = "."                               # default: the folder of this file

[sources.lean]                                  # one section per source; the name is free
url        = "coursedl-web:https://proglang.github.io/teaching/26ws/lean.html"
output_dir = "Functional Programming in Lean"   # -> /home/me/University/Functional Programming in Lean/
transform  = '''
.*/lecture(\d+)\.pdf -re-> "Slides/{i1:02} Lecture.pdf"
'''
```

## `[defaults]`

| Key | Default | Meaning |
|---|---|---|
| `output_root` | `.` (the config's folder) | Where the lecture folders go: each source's `output_dir` is below it. A relative path counts from the config's folder |
| `state_dir` | `.coursedl` (in `output_root`) | Download archives, link state, `history.jsonl`, staging folders (see [files.md](files.md)). A relative path counts from `output_root` |
| `on_change` | `keep-both` | What happens when a file changed online and your copy exists: `keep-both` or `backup-old` (see [files.md](files.md)) |
| `notify_cmd` | `["notify-send", "--app-name=coursedl", "{title}", "{body}"]` | Command for the notification with `--notify`. `{title}` and `{body}` are replaced in each argument, no shell is involved. E.g. ntfy: `["curl", "-s", "-H", "Title: {title}", "-d", "{body}", "https://ntfy.sh/my-topic"]` |
| `ytdlp` | aria2c for http/ftp, 4 connections | Raw yt-dlp parameters for all sources, merged into the built-in ones. `external_downloader = {}` turns aria2c off |

## `[sources.NAME]`

| Key | Default | Meaning |
|---|---|---|
| `url` | – | Source type prefix + URL, see [sources.md](sources.md): `coursedl-web:`, `coursedl-dir:`, `coursedl-svn:`, `coursedl-nextcloud:`, `coursedl-youtube:` |
| `output_dir` | – | Folder below `output_root`. Several sources may share one (e.g. a course page and its SVN) |
| `auth` | – | See [Authentication](#authentication) |
| `templates` | see [Naming](#naming-templates-and-transform-rules) | Remote path per entry kind |
| `transform` | – | [PFERD](https://github.com/Garmelon/PFERD) transform rules, remote path → local path |
| `unmatched` | `warn` | A file that no transform rule matches: `warn` = not downloaded, reported once ("no rule matches: …"); as soon as a rule maps it, the next run downloads it. `keep` = downloaded to its remote path, no report. Only applies when the source has rules |
| `ytdlp` | – | Raw yt-dlp parameters for this source, e.g. `format = "bv*[height<=?720]+ba/b"` (`<=?`: plain files have no height) |

Options of `coursedl-web:` sources (what each does: [sources.md](sources.md)):

| Key | Default | Meaning |
|---|---|---|
| `link_regex` | common material extensions | Links (URL path) that are files |
| `dir_regex` | – | Links that are directory listings, downloaded whole |
| `follow_regex`, `follow_depth` | –, `1` | Subpages to crawl |
| `ignore_regex` | – | Links to skip silently |
| `youtube` | `true` | YouTube links become video downloads |
| `google_drive` | `true` | Google Drive file links become file downloads |
| `folders` | `headings` | `headings`: headings and list labels become folders of the remote path; `none` |
| `content_selector` | auto | Content area: `tag`, `#id`, `.class`, or combined (`div#content`) |
| `warn_unhandled` | `true` | Warn about new links no rule handles |
| `version` | `head` | `head`: HEAD request per file to detect changes; `none`: each URL once (also for `coursedl-dir:`) |

## Authentication

| `auth` | For |
|---|---|
| `{ type = "basic", username = "…", password_cmd = "…" }` | HTTP basic auth. Only sent to the host of the source URL (never to YouTube or other linked sites). Also the share password (`coursedl-nextcloud:`, user name ignored). `coursedl-svn:` sources take no `auth`: svn's own login is used, see [sources.md](sources.md#coursedl-svn-svn-working-copy) |
| `{ type = "form", password_cmd = "…" }` | A password form on the page itself (WordPress "protected post", simple password pages). When a fetched page shows a form with a password field, coursedl fills in the password, sends the form's other fields as they are, and fetches the page again. Optional: `username` (goes into the form's text/email field), `fields = { name = "value" }` (added or overridden). The form must post to the same host. The session cookie is kept for the run, so downloads from that site get it too |
| `{ type = "cookies", file = "cookies.txt" }` or `browser = "firefox"` | Cookies from a Netscape cookie file or a browser profile, for logins coursedl can't do itself |

The password comes from, in this order: the environment variable `COURSEDL_<SOURCE>_PASSWORD` (source name upper-cased, other characters `_`, e.g. `COURSEDL_CPP_SOLUTIONS_PASSWORD`), `password_cmd` (a command; the first line of its output, e.g. `pass uni/lmb` or `secret-tool lookup …`), `password` (plain text in the file).

## Naming: templates and transform rules

Each file gets its local path in three steps:

1. **Remote path.** The source's template for the entry's kind is filled in (yt-dlp output template syntax). Defaults: `file = "%(cd_path)s"`, `video = "%(cd_dir)s/%(title)s.%(ext)s"`.
2. **Transform.** The [PFERD](https://github.com/Garmelon/PFERD) transform rules turn the remote path into the local path (relative to `output_dir`). A rule's target `!` excludes the file.
3. **Clean-up.** Each path segment is made a valid file name; `..` is refused.

Fields for templates:

| Field | Value |
|---|---|
| `cd_path` | Path from the source: headings/list labels + file name (`coursedl-web:`), path below the listing/share (others) |
| `cd_dir`, `cd_name` | Folder and file name part of `cd_path` |
| `cd_text` | Text of the link on the page (`coursedl-web:` only), e.g. `Class 1: Introduction and image basics` |
| `cd_item` | Text of the link's list item or table row before its first link, e.g. `Vorlesung 9, Dienstag 23. Juni 2026, … (STL = Standard Template Library)` |
| `cd_kind` | `file` or `video` (YouTube) |
| yt-dlp's fields | e.g. `title`, `ext`, `upload_date` for videos |

`cd_text`/`cd_item` are unset when empty, so give a default: `%(cd_item|-)s`. To name files after the page, put the text in front of the path and take it apart in the rules:

```toml
[sources.cpp.templates]
file = "%(cd_item|-)s/%(cd_path)s"     # "Vorlesung 9, … (STL = …)/<heading>/vorlesung-09.mp4"

[sources.cpp]
transform = '''
Vorlesung\s(\d+),[^/]*\((.*)\)[^/]*/.*\.mp4 -re-> "Recordings/{i1:02} {g2}.mp4"
'''
```

### Transform rules

The syntax is [PFERD](https://github.com/Garmelon/PFERD)'s ([full reference](https://github.com/Garmelon/PFERD/blob/master/CONFIG.md#transformation-rules)). One rule per line, `SOURCE ARROW TARGET`; the first matching rule wins, unless its arrow ends in `>>` (then the result goes through the following rules too).

| Arrow | Matches |
|---|---|
| `-->` | A path prefix: `Folien --> Slides` also renames `Folien/a.pdf` |
| `-name->` | Any single path segment |
| `-exact->` | Only the exact path |
| `-re->` | A regex over the whole path (anchored) |
| `-name-re->`, `-exact-re->` | Regex versions of the above |

In regex targets, `{g1}` is group 1, `{i1}` the same as an integer (so `{i1:02}` writes `3` as `03`), `{g0}` the whole match; Python expressions work too (`{g1.lower()}`).

Quoting: patterns and targets without spaces can stay unquoted, and then backslashes are literal (`\d`, `\s`). With spaces they need `"…"`, and inside quotes backslashes must be doubled. So write patterns unquoted with `\s` for spaces, and quote only the target.

Common rules:

```text
# Numbers: "blatt-3.pdf" -> "Exercises/03 blatt.pdf"; the generic form strips any
# trailing number from the name: (.*?) is the name, [-_]? the separator, (\d+) the number.
uebungen/(.*?)[-_]?(\d+)\.pdf -re-> "Exercises/{i2:02} {g1}.pdf"

# Leading numbers like "3_Basic-of-HPO.pdf" or "w01_t01_04_big_picture.pdf":
# (?:[a-z]?\d+[_-])* strips all of them.
Week\s(\d+)/(?:[a-z]?\d+[_-])*(.*\.pdf) -re-> "Slides/{i1:02} {g2}"

# A folder per unit for code: "code/vorlesung-03/Makefile" -> "Media/03/Makefile"
code/vorlesung-(\d+)/(.*) -re-> "Media/{i1:02}/{g2}"

# Exclude
.*\.tmp -re-> !

# Catch-all, e.g. for a source whose content is still unknown: download everything
# aside instead of holding unmatched files back
(.*) -re-> Other/{g1}
```

Files no rule matches are held back and reported once (`unmatched = "warn"`), so a new kind of file never lands in a wrong place unnoticed. `--dry-run` shows them as `… -> ? (no rule matches, not downloaded)`.

Check rules with `coursedl sync --dry-run SOURCE` (remote path → local path for each file) or `--explain` (also every rule tried). If two different files end up on the same local path, the run warns: the rules need to tell them apart.

## Command line

```text
coursedl [-c CONFIG] [-v] sync [SOURCE…] [--dry-run] [--explain] [--notify] [--restore-missing]
coursedl [-c CONFIG] links SOURCE
```

| Option | Effect |
|---|---|
| `SOURCE…` | Only these sources (default: all) |
| `--dry-run` | List and print remote → local paths; download nothing, change no state |
| `--explain` | Print the paths and each transform step during a real run |
| `--notify` | Run `notify_cmd` if there is something new (for timers) |
| `--restore-missing` | Ignore the archive once; see [files.md](files.md) |
| `links SOURCE` | Links that no rule handles, with the date they first appeared |
| `-v` | yt-dlp debug output |

Exit code 1 if a source failed (the others still run) or the notification failed, 2 for a broken config.
