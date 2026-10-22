"""`coursedl-nextcloud:` a public Nextcloud share, read via WebDAV."""

import base64
import re
from typing import Any, final, override

from yt_dlp.utils import ExtractorError

from coursedl.extractors.common import CoursedlIE
from coursedl.extractors.webdav import walk


@final
class CoursedlNextcloudIE(CoursedlIE):
    PREFIX = "coursedl-nextcloud:"
    _VALID_URL = r"coursedl-nextcloud:https?://.+"

    @override
    def _real_extract(self, url: str) -> Any:
        share = url.removeprefix(self.PREFIX)
        m = re.match(r"(?P<base>https?://.+?)(/index\.php)?/s/(?P<token>[^/?#]+)", share)
        if not m:
            raise ExtractorError(f"not a Nextcloud share link: {share}", expected=True)
        # The legacy public WebDAV endpoint: basic auth with the share token as user name
        # and the share password (if any) as password. Works on all current versions.
        dav = f"{m['base']}/public.php/webdav/"
        password = self.ctx.auth_password or ""
        token = base64.b64encode(f"{m['token']}:{password}".encode()).decode()
        # Nextcloud 35+ answers 401 to public WebDAV requests without this header.
        auth = {"Authorization": f"Basic {token}", "X-Requested-With": "XMLHttpRequest"}
        entries = []
        for f in walk(self, dav, auth):
            entry = self.file_entry(str(f.path), f.url, f.path, f.version, f.timestamp)
            entry["http_headers"] = auth  # the share's auth, not the source's
            entries.append(entry)
        return self._result(entries, share)
