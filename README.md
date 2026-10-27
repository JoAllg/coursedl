# coursedl

This is a tool to download and synchronize files from web pages and other sources to a local directory. It was made for university courses, but can be used for any other website or source as well. It allows detailed transforming of names and paths using [PFERD](https://github.com/Garmelon/PFERD)'s [transformation rules](https://github.com/Garmelon/PFERD/blob/master/CONFIG.md#transformation-rules). It is meant to be extensible, so that users can add more extractors depending on their needs (see [Adding a source type](docs/extractors.md)).

It is built on [yt-dlp](https://github.com/yt-dlp/yt-dlp), which does all the downloading; each source type is a yt-dlp extractor that only lists the files. Made to run unattended from a timer. Sources it handles so far ([details](docs/sources.md)):

- Web pages and their subpages, including YouTube videos and Google Drive files linked on them
- HTTP directory listings (Apache/nginx autoindex, SVN web views)
- SVN repositories, as an svn working copy
- Public Nextcloud shares
- YouTube playlists, channels and single videos

How it syncs:

- Files you rename, move or delete locally are **not** downloaded again.
- Files that change online are downloaded again **without overwriting** your copy.
- New links that no rule handles are reported once, so you notice when a page changes.
- At most one notification per run, only when there is something new.

## Install

Runs on Linux only for now (Windows lacks `fcntl` and treats paths differently). Needs [uv](https://docs.astral.sh/uv/) (it fetches Python 3.14 itself).

```sh
uv tool install .            # or, from a checkout: uv sync && uv run coursedl ...
```

Other programs, depending on what you use:

- `notify-send` (package `libnotify`, on Debian/Ubuntu `libnotify-bin`) and a running notification daemon (every desktop has one): for `--notify` with the default `notify_cmd`.
- `ffmpeg`: for YouTube videos in good quality (video and audio come separately and are merged); without it yt-dlp falls back to a single combined file, often only 360p.
- `aria2c`: faster downloads of large files (optional).
- `svn`: for `coursedl-svn:` sources.

## Usage

```sh
cp courses.example.toml coursedl.toml   # then edit it: output_root, sources, rules
coursedl sync --dry-run SOURCE          # what would be downloaded, and where
coursedl sync                           # all sources (reads ./coursedl.toml, or -c PATH)
coursedl sync --explain SOURCE          # print remote -> local path for each file
coursedl sync --restore-missing         # download again whatever is missing locally
coursedl links SOURCE                   # links no rule handles
```

For `coursedl-svn:` sources, log in once with svn itself (`svn ls URL`); coursedl uses svn's stored login and never asks for a password.

## Notifications

`coursedl sync --notify` runs `notify_cmd` once per run, if there are new or changed files or warnings. The command is set in `[defaults]`; `{title}` and `{body}` are filled in, no shell is involved. The default is a desktop notification:

```toml
notify_cmd = ["notify-send", "--app-name=coursedl", "{title}", "{body}"]
```

Any other command works too, e.g. a push to your phone with [ntfy](https://ntfy.sh):

```toml
notify_cmd = ["curl", "-s", "-H", "Title: {title}", "-d", "{body}", "https://ntfy.sh/my-topic"]
```

or an email (the body goes to `mail`'s stdin, so a small shell wrapper is needed):

```toml
notify_cmd = ["sh", "-c", 'printf "%s\n" "$1" | mail -s "$0" me@example.org', "{title}", "{body}"]
```

Every run is also logged to `<output_root>/.coursedl/history.jsonl`.

## Running regularly

A systemd user timer is the recommended way: the service runs as you (never as root), and it has access to your session's D-Bus, so `notify-send` works. Replace the paths with yours.

```ini
# ~/.config/systemd/user/coursedl.service
[Unit]
Description=Sync course material with coursedl

[Service]
Type=oneshot
ExecStart=%h/.local/bin/coursedl -c %h/University/coursedl.toml sync --notify

# Hardening: everything is read-only except the output folder and the caches it needs.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
# output_root (the state in .coursedl/ lives there too)
ReadWritePaths=%h/University
# yt-dlp's cache and svn's login cache; "-": fine if missing
ReadWritePaths=-%h/.cache/yt-dlp -%h/.subversion
```

```ini
# ~/.config/systemd/user/coursedl.timer
[Unit]
Description=Run coursedl sync regularly

[Timer]
OnCalendar=*-*-* 08..22:00:00
RandomizedDelaySec=10min
# catch up on a missed run after sleep or shutdown
Persistent=true

[Install]
WantedBy=timers.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now coursedl.timer
journalctl --user -u coursedl     # output of the runs
```

If a `password_cmd` needs more than read access (e.g. a password manager that writes to its own folder), add that folder to `ReadWritePaths`.

## Documentation

- [docs/configuration.md](docs/configuration.md): config file, authentication, naming with templates and transform rules, command line.
- [docs/sources.md](docs/sources.md): the source types (`coursedl-web:`, `coursedl-dir:`, `coursedl-svn:`, `coursedl-nextcloud:`, `coursedl-youtube:`) and how each detects changes.
- [docs/files.md](docs/files.md): tracking, renamed/deleted/changed files, conflicts, warnings.
- [courses.example.toml](courses.example.toml): one example source of each type.

## Development

See [CLAUDE.md](CLAUDE.md) (design, tools and rules).
