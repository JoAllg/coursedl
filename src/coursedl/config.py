"""`courses.toml`: pydantic models and loading."""

import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from coursedl.transform import RuleParseError, Transformer

# The config can be anywhere (`-c`, default: this name in the current folder). The state
# lives with the downloads, in a hidden folder in `output_root` (like PFERD's `.report`).
CONFIG_NAME = "coursedl.toml"
STATE_FOLDER = ".coursedl"

# Extensions of course material. Matched against the URL path, so a page that serves files
# from extensionless URLs needs its own `link_regex`.
DEFAULT_LINK_REGEX = (
    r"(?i)\.(pdf|zip|tar|gz|tgz|bz2|xz|7z|mp4|mkv|webm|mp3|ipynb|py|lean|hs|c|cc|cpp|h|hpp|"
    r"java|rs|ml|m|r|jl|tsv|csv|json|txt|md|tex|odp|odt|ods|pptx?|docx?|xlsx?)$"
)

DEFAULT_TEMPLATES = {
    "file": "%(cd_path)s",
    "video": "%(cd_dir)s/%(title)s.%(ext)s",
}

# aria2c for plain http(s)/ftp files; HLS/DASH stay with yt-dlp's native downloader.
# 4 connections: these are university servers, not CDNs.
DEFAULT_YTDLP: dict[str, Any] = {
    "external_downloader": {"http": "aria2c", "ftp": "aria2c"},
    "external_downloader_args": {"aria2c": ["-x4", "-s4", "--min-split-size=10M"]},
    "concurrent_fragment_downloads": 4,
}


class _Model(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class _PasswordAuth(_Model):
    """The password comes from the environment variable `COURSEDL_<SOURCE>_PASSWORD`, else
    `password_cmd` (stdout of a command), else `password` in plain text."""

    username: str = ""
    password: str | None = None
    password_cmd: str | list[str] | None = None

    def resolve_password(self, source: str) -> str:
        env = f"COURSEDL_{re.sub(r'\W', '_', source).upper()}_PASSWORD"
        if env in os.environ:
            return os.environ[env]
        if self.password_cmd is not None:
            cmd = (
                shlex.split(self.password_cmd)
                if isinstance(self.password_cmd, str)
                else self.password_cmd
            )
            # The timeout ends a hung credential helper under a timer.
            out = subprocess.run(
                cmd, check=True, capture_output=True, text=True, timeout=120
            ).stdout
            # `pass` and friends print the secret on the first line.
            return out.splitlines()[0] if out else ""
        if self.password is not None:
            return self.password
        raise ValueError(f"source {source!r}: no password (${env}, password_cmd or password)")


class BasicAuth(_PasswordAuth):
    """HTTP basic auth, sent only to the host of the source URL."""

    type: Literal["basic"]


class FormAuth(_PasswordAuth):
    """A login form on the page itself (WordPress "protected post", simple password
    pages, …): when a fetched page of the source contains a form with a password field,
    it is filled in and submitted, then the page is fetched again. The form's own
    fields (hidden inputs, submit button) are sent as they are; `username` goes into its
    text/email field, `fields` adds or overrides others. The session cookie lives in
    yt-dlp's cookie jar for the run, so file downloads get it too."""

    type: Literal["form"]
    fields: dict[str, str] = {}


class CookiesAuth(_Model):
    """Cookies from a Netscape cookie file or from a browser profile (yt-dlp does both)."""

    type: Literal["cookies"]
    file: Path | None = None
    browser: str | None = None

    @field_validator("file")
    @classmethod
    def _expand(cls, v: Path | None) -> Path | None:
        return v.expanduser() if v else None


Auth = Annotated[BasicAuth | FormAuth | CookiesAuth, Field(discriminator="type")]


SELECTOR_RE = re.compile(r"(?P<tag>[a-z][a-z0-9]*)?(?:#(?P<id>[\w-]+))?(?:\.(?P<cls>[\w-]+))?")


class Source(_Model):
    url: Annotated[
        str,
        Field(
            pattern=r"^coursedl-(web|dir|nextcloud|youtube):https?://|^coursedl-svn:(https?|svn|file)://"
        ),
    ]
    output_dir: Path
    auth: Auth | None = None
    transform: str = ""
    # A file no transform rule matches: "warn" = report it once and don't download it
    # (it is picked up as soon as a rule maps it); "keep" = download it to its remote path.
    # Only used when there are rules.
    unmatched: Literal["warn", "keep"] = "warn"
    templates: dict[str, str] = {}
    ytdlp: dict[str, Any] = {}

    # coursedl-web
    link_regex: re.Pattern[str] = re.compile(DEFAULT_LINK_REGEX)
    follow_regex: re.Pattern[str] | None = None
    follow_depth: int = 1
    dir_regex: re.Pattern[str] | None = None
    ignore_regex: re.Pattern[str] | None = None
    youtube: bool = True
    google_drive: bool = True
    folders: Literal["headings", "none"] = "headings"
    content_selector: str | None = None
    warn_unhandled: bool = True
    # coursedl-web and coursedl-dir
    version: Literal["head", "none"] = "head"

    @field_validator("transform")
    @classmethod
    def _check_transform(cls, v: str) -> str:
        try:
            _ = Transformer(v)
        except RuleParseError as e:
            raise ValueError(str(e)) from e
        return v

    @field_validator("content_selector")
    @classmethod
    def _check_selector(cls, v: str | None) -> str | None:
        if v is not None and not SELECTOR_RE.fullmatch(v):
            raise ValueError("only `tag`, `#id`, `.class` and combinations like `div#id` work")
        return v

    @property
    def all_templates(self) -> dict[str, str]:
        return DEFAULT_TEMPLATES | self.templates


class Defaults(_Model):
    # Base folder of the lectures (each source's `output_dir` is below it). Relative paths
    # count from the config file's folder; a relative `state_dir` from `output_root`.
    output_root: Path = Field(default=Path(), validate_default=True)
    state_dir: Path = Field(default=Path(STATE_FOLDER), validate_default=True)
    on_change: Literal["keep-both", "backup-old"] = "keep-both"
    notify_cmd: list[str] = [
        "notify-send",
        "--app-name=coursedl",
        "{title}",
        "{body}",
    ]
    ytdlp: dict[str, Any] = DEFAULT_YTDLP

    @field_validator("ytdlp")
    @classmethod
    def _merge_ytdlp(cls, v: dict[str, Any]) -> dict[str, Any]:
        return DEFAULT_YTDLP | v

    @field_validator("output_root")
    @classmethod
    def _expand(cls, v: Path, info: ValidationInfo) -> Path:
        base: Path = (info.context or {}).get("base", Path.cwd())
        return base / v.expanduser()

    @field_validator("state_dir")
    @classmethod
    def _expand_state(cls, v: Path, info: ValidationInfo) -> Path:
        return info.data.get("output_root", Path()) / v.expanduser()  # validated first


class Config(_Model):
    defaults: Defaults
    sources: dict[str, Source]


def load(path: Path) -> Config:
    with path.open("rb") as f:
        data = tomllib.load(f)
    _ = data.setdefault("defaults", {})  # validated here, so its paths get the base folder
    return Config.model_validate(data, context={"base": path.resolve().parent})
