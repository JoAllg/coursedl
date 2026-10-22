# coursedl

Syncs course material from university websites into local folders: plain web pages (and subpages), public Nextcloud shares, HTTP directory listings (autoindex, SVN web views), SVN working copies via the svn client, and YouTube links found on those pages. Meant to run unattended (systemd timer/cron). **ILIAS is out of scope**; it is handled by the PFERD fork in `../PFERD`.

Design in short (user docs with the details: `README.md`, `docs/`):
- **yt-dlp does all downloading**, used as a library. Each source type is a yt-dlp extractor (`coursedl-web:`, `coursedl-dir:`, `coursedl-nextcloud:`, `coursedl-youtube:` URL prefixes) that only lists files; they are registered per run with `YoutubeDL.add_info_extractor` (no plugin directory). aria2c is the external downloader for http/ftp, when installed.
- **Generic extractors:** they work on the structure of a kind of site (content area of a page, links below a listing URL, WebDAV), never on one specific site. The user's config picks the concrete page and its regexes.
- **Tracking** via yt-dlp's download archive. The entry ID includes the remote version, so changed files are downloaded again, while files renamed or deleted locally are not.
- **Naming:** a yt-dlp output template builds the remote path; [PFERD](https://github.com/Garmelon/PFERD)'s transform rules (copied from PFERD, MIT; [syntax](https://github.com/Garmelon/PFERD/blob/master/CONFIG.md#transformation-rules)) map it to the local path. Existing files are never overwritten (`on_change`).
- **Config:** one TOML file (`-c PATH`, default `./coursedl.toml`; the personal one is `../coursedl.toml` in the workspace root), state in `<output_root>/.coursedl/`, one section per source with its own auth. Validated with pydantic. Docs: `docs/configuration.md`, `docs/sources.md`, `docs/files.md`, `docs/extractors.md` (adding a source type).
- **Warnings and notifications:** unhandled new links are warned about once; one notification per run via `notify_cmd`.

## Development

- **uv** manages Python 3.14, the venv and the dependencies (`uv.lock`). Use `uv add <pkg>` (`--dev` for tools) and `uv run <cmd>`; don't use pip. With direnv, `.envrc` runs `uv sync` and activates `.venv` automatically.
- **types-yt-dlp** (dev): typeshed's stubs for yt-dlp.
- **pydantic** for config and other data models (`extra="forbid"`, validate regexes and paths on load).
- **ruff** for lint and format: `uv run ruff check --fix` and `uv run ruff format`.
- **basedpyright** for type checking: `uv run basedpyright` (mode "recommended"). It uses typeshed's yt-dlp stubs, which are incomplete for the internals we use (e.g. `_InfoDict` has no room for our `cd_*` fields, `Request` is typed as urllib's): use `Any` there or a `# pyright: ignore[rule]` with a short reason. The Any/Unknown rules are off.
- **pytest** with offline fixtures only: `uv run pytest`. Tests must not need the university servers.
- All tool config lives in `pyproject.toml`. Code lives in `src/coursedl/`, the extractors in `src/coursedl/extractors/`. Saved pages of the example courses are in `tests/fixtures/`.
- `src/coursedl/transform.py` is copied from PFERD: keep it close to upstream (checked less strictly).
- Before finishing a change: `ruff check`, `ruff format --check`, `basedpyright` and `pytest` all pass.
- Only use yt-dlp internals that are listed in `tests/test_api_surface.py`; add new ones there and update the snapshot with `UPDATE_SNAPSHOT=1 uv run pytest tests/test_api_surface.py` (the `yt-dlp-watch` workflow runs it for every new yt-dlp release).

## Rules

- **Don't overengineer.** Build the simplest thing that meets the current requirement. No abstractions, options or extension points "for later"; add them when a real source needs them. Prefer the stdlib, yt-dlp and existing code over new code or dependencies.
- **Comment well, but only about the current code:** why it is done this way and how non-obvious parts work. Don't describe what was there before, what changed or what was discarded, unless that is needed to explain the why (e.g. "HEAD instead of the listing date, because the listing has no per-file date").
- **Markdown is not code: no manual line breaks.** Write each paragraph and list item as one line; the editor wraps it. Break lines only where Markdown needs it (lists, tables, code blocks, headings).
- **Keep the archive ID format stable** (`<remote key>@<version>`, see `file_entry`): a changed format makes every file look new, and `keep-both` then downloads all of them again as copies.
- Use your own judgment to hand in work to the appropriate subagent instead of doing everything on your own!