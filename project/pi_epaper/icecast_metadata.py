"""Update the existing Icecast mount without changing its audio source."""
import base64
import logging
import os
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from xml.etree import ElementTree

LOG = logging.getLogger("icecast_metadata")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Keep authenticated admin requests on the configured server.
        return None


class IcecastMetadata:
    def __init__(self, url, mount, username="admin", password="", include_album=False, opener=None):
        self.url = url.rstrip("/")
        self.mount, self.username, self.password = mount, username, password
        self.include_album = include_album
        self.opener = opener or build_opener(NoRedirect()).open
        parsed = urlsplit(self.url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None \
                or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError("ICECAST_METADATA_URL must be an HTTP(S) server URL without credentials or query")
        if not mount.startswith("/"):
            raise ValueError("Icecast mount must begin with /")

    @classmethod
    def from_environment(cls, url, mount):
        client = cls(os.environ.get("ICECAST_METADATA_URL") or url, mount,
                     os.environ.get("ICECAST_METADATA_USERNAME") or "admin",
                     os.environ.get("ICECAST_METADATA_PASSWORD", ""),
                     os.environ.get("ICECAST_METADATA_INCLUDE_ALBUM", "0").lower() in ("1", "true", "yes"))
        if not client.password:
            LOG.info("Icecast metadata disabled; set ICECAST_METADATA_PASSWORD to enable it.")
        return client

    def update(self, artist="", title="", album="", label=None):
        """Return success/failure; an unavailable Icecast admin endpoint is isolated."""
        if not self.password:
            return False
        clean = lambda value: " ".join(str(value or "").split())
        if label is not None:
            song = clean(label)
        else:
            song = " - ".join(value for value in (clean(artist), clean(title)) if value)
            if self.include_album and clean(album):
                song += " ["+clean(album)+"]"
        if not song:
            return False
        query = urlencode({"mount": self.mount, "mode": "updinfo", "charset": "UTF-8", "song": song})
        auth = base64.b64encode((self.username+":"+self.password).encode("utf-8")).decode("ascii")
        request = Request(self.url+"/admin/metadata?"+query,
                          headers={"Authorization": "Basic "+auth, "Accept": "application/xml"})
        try:
            with self.opener(request, timeout=5) as response:
                xml = ElementTree.fromstring(response.read(16384))
            if xml.findtext("return") != "1":
                LOG.warning("Icecast rejected metadata; check the active mount and stream format.")
                return False
        except HTTPError as exc:
            LOG.warning("Icecast metadata update failed (HTTP %s); check credentials and active mount.", exc.code)
            return False
        except Exception as exc:
            LOG.warning("Icecast metadata unavailable (%s); recognition and Last.fm will continue.", type(exc).__name__)
            return False
        LOG.info("Icecast metadata updated: %s", song)
        return True
