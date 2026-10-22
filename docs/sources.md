# Source types

Each source type except `coursedl-svn:` is a yt-dlp extractor in `src/coursedl/extractors/`. An extractor only lists files (URL, remote path, version); yt-dlp downloads them. `coursedl-svn:` uses the svn client instead (`src/coursedl/svn.py`). They are generic: they handle a kind of site, and your config picks the concrete page and its rules. The `url` of a source starts with the type's prefix.

| Prefix | For | Changes detected by | Auth |
|---|---|---|---|
| `coursedl-web:` | A course web page (and subpages) | HEAD per file: ETag, Last-Modified or size | basic, form, cookies |
| `coursedl-dir:` | An HTTP directory listing (Apache/nginx autoindex, SVN web views) | HEAD per file | basic, cookies |
| `coursedl-svn:` | An SVN folder, as an svn working copy | `svn update` | svn's own login, once |
| `coursedl-nextcloud:` | A public Nextcloud share | WebDAV ETag | basic (share password) |
| `coursedl-youtube:` | A YouTube playlist, all playlists of a channel (`…/@channel/playlists`), or a single video | video ID (videos don't change) | – |

YouTube and Google Drive links found on a page are handled by `coursedl-web:` (YouTube through yt-dlp's own YouTube extractor).

`coursedl-youtube:` fields for the `video` template: `cd_dir` = the playlist title (for a channel below the tab's title, `<channel> - Playlists/<playlist>`), `cd_index` = the video's position in the playlist (single video: no `cd_dir`, `cd_index` 1). E.g. `video = "%(cd_dir)s/%(cd_index)02d %(title)s.%(ext)s"`.

## `coursedl-web:` course page

```toml
url = "coursedl-web:https://example.edu/teaching/cpp/"
```

**Content area.** Only links in the page's content count; navigation, sidebars and footers are ignored. Without `content_selector`, the content area is the first of `<main>`, an element with `id="content"`, `<body>`. `<nav>` and `<footer>` are skipped inside it as well. That covers HTML5 pages, MoinMoin wikis, WordPress and old table layouts.

**What a link becomes**, in this order:

1. Ignored: `ignore_regex`, non-http links (`mailto:`), wiki actions (`?action=`), login/logout, anchors on the same page, links already handled.
2. YouTube video (`youtube = true`): downloaded with yt-dlp; name from `title`.
3. Directory listing (`dir_regex`): all files below it, like `coursedl-dir:`.
4. Google Drive file (`google_drive = true`): `drive.google.com/file/d/…`, `open?id=`, `uc?id=`. Downloaded through Drive's direct download link, which also gives the file's real name and modification date. (yt-dlp's Drive extractor only handles videos.)
5. File (`link_regex` on the URL path; default: PDF, archives, videos, notebooks, code, office documents, data files).
6. Subpage (`follow_regex`, up to `follow_depth` levels): crawled the same way; its files go below a folder named after it.
7. Otherwise: *unhandled*, reported once (see [files.md](files.md#warnings)).

**Remote path** (`cd_path`) = folders + file name:
- Folders (`folders = "headings"`): the h2–h6 headings above the link (h1 is the page title), then the labels of list items that contain a sublist. The label is the item's text before its first link: `<li>Week 1 (<a>Sheet</a>): <ul>…` gives `Week 1`, and the sheet link on that line belongs to `Week 1` too.
- File name: from the server's Content-Disposition if it sends one (Drive), else the last part of the URL. Two different files with the same path get the link text added: `week.mp4`, `week (merged videos with subtitles).mp4`.
- Also available to templates: the link text (`cd_text`) and the text of the link's list item or table row (`cd_item`), see [configuration.md](configuration.md#naming-templates-and-transform-rules).

**Versions.** One HEAD request per file and run (`version = "head"`). A HEAD that fails (e.g. 401 for a protected file) makes the link unhandled instead of an error every run.

Examples: the C++ wiki (MoinMoin, `#content`), the AutoML page (WordPress, password form, Drive and YouTube), the LMB page (table layout), the Lean page (HTML5).

## `coursedl-dir:` directory listing

```toml
url = "coursedl-dir:https://example.edu/cpp/files/"
```

No listing format is parsed: every link in the content area whose URL (without query) lies below the listing's URL counts; ending in `/` means subfolder. That works for Apache/nginx autoindex (the `?C=N;O=D` sort links point at the listing itself), mod_dav_svn and similar views. Links to parents or other sites are ignored. Versions by HEAD.

## `coursedl-svn:` SVN working copy

```toml
url        = "coursedl-svn:https://svn.example.edu/cpp/solutions/"
output_dir = "Programmieren in C++/Solutions/SVN"
```

Not an extractor: coursedl runs the svn client (package `subversion`). The source's `output_dir` is an svn working copy: `svn checkout` on the first run, `svn update` on every run after that. What changed is reported like other downloads (new, updated, deleted, restored), and conflicts are reported (see [files.md](files.md#svn-sources)). Works with any SVN server over `https://`, `svn://` or `file://`. Transform rules don't apply: the repository's layout is kept.

**Login.** svn's own credential cache is used, so there is no password in coursedl's config. Log in once by running svn yourself on the URL (with an access token, see [Course SVN with a web view](#course-svn-with-a-web-view)):

```sh
svn ls --username <user> https://svn.example.edu/cpp/solutions/
```

svn asks for the password and keeps it (`~/.subversion`, or the desktop keyring). coursedl runs svn with `--non-interactive`; if the login is missing or no longer accepted, the source fails with "svn is not logged in for …: run `svn ls …` once", which is in the notification.

A folder that already exists, is not empty and is not a working copy is never touched; neither is a working copy of another URL (e.g. last semester's).

A folder the login may not read (HTTP 403, e.g. a course's solutions when you are not a member of that course) is reported once as "not accessible: …", not as a failure.

### Course SVN with a web view

Some course platforms serve each course's SVN over https, e.g. `https://svn.example.edu/<semester>/<course>/svn/<folder>/`, with a public folder for lecture material, a solutions folder only for members of the course, and one folder per student.

- Public folder: use the web view, `coursedl-dir:…/svn/public/`. It needs no login and, unlike a working copy, goes through the transform rules into the lecture layout. Versions come from the file size only (the web view shows no revisions to anonymous users).
- Everything that needs a login (solutions, your own folder): `coursedl-svn:`.

**Login with a token.** If the SVN takes an access token instead of your password:

1. Create a token on the platform's account page.
2. Log in once with your user ID as user name and the token as password:
   ```sh
   svn ls --username <user-id> https://svn.example.edu/<semester>/<course>/svn/solutions/
   ```
   svn asks for the password (paste the token) and stores both.

When the token expires or is revoked, the next run reports "svn is not logged in"; repeat the two steps.

## `coursedl-nextcloud:` public share

```toml
url = "coursedl-nextcloud:https://cloud.example.edu/index.php/s/<token>"
```

Read through the share's WebDAV endpoint (`/public.php/webdav/`, basic auth with the share token as user name), one `PROPFIND Depth: 1` per folder. Every file's ETag and modification date come with the listing, so changes are detected exactly without extra requests. Password-protected share: `auth = { type = "basic", password_cmd = "…" }`. The remote path is the path inside the share.

## Adding a source type

See [extractors.md](extractors.md).
