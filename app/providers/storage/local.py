"""Files on a local disk — the first-release adapter (PROJECT.md A8).

One installation, one server, one Docker volume, and that volume is one of the
three things the client's backup has to cover (PROJECT.md §14). A client who
outgrows it gets a different adapter behind the same port, not different
callers.

The root is **not** an environment variable. The image creates ``/app/uploads``
and the compose file mounts the volume there, and ``WORKDIR`` is ``/app``, so a
path relative to the working directory is right in the container and in
development without anyone configuring anything. "Could two clients want
different values?" — no; where the volume is mounted is the compose file's
business, not the application's (PROJECT.md §7).

Keys are ``<purpose>/<two hex characters>/<uuid><ext>``. The middle level
shards files across 256 directories so no single one grows without limit, and
the purpose in front is what lets the file route decide whether a request needs
a token before it touches the database.
"""

import uuid
from pathlib import Path

from anyio import Path as AsyncPath

from app.core.logging import get_logger
from app.providers.storage.base import UploadPurpose

logger = get_logger(__name__)

#: Relative to the working directory — ``/app/uploads`` in the container.
DEFAULT_ROOT = Path("uploads")

#: Where the file route is mounted. Not part of the API contract: API.md §11
#: promises a ``url`` field, not a particular path behind it.
URL_PREFIX = "/uploads"

#: Sits between the prefix and the key for files that need a token. It is not a
#: purpose, so a key can never begin with it and the two routes cannot overlap.
PRIVATE_SEGMENT = "private"


class LocalStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or DEFAULT_ROOT).resolve()

    def _path(self, key: str) -> Path:
        """Resolve a key inside the root, or refuse.

        The key reaches this from a URL, so ``../../etc/passwd`` has to be
        impossible rather than unlikely. Resolving and then checking
        containment catches traversal, absolute paths and symlinks in one.
        """
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError(f"key escapes the storage root: {key!r}")
        return candidate

    def build_key(self, *, filename: str, purpose: UploadPurpose) -> str:
        # The client's filename is never part of the key: it would be a second
        # place for traversal to hide, and two people uploading `logo.png`
        # would collide.
        suffix = Path(filename).suffix.lower()[:16]
        stamp = uuid.uuid4()
        return f"{purpose.value}/{stamp.hex[:2]}/{stamp.hex}{suffix}"

    async def save(
        self, *, content: bytes, filename: str, purpose: UploadPurpose
    ) -> str:
        key = self.build_key(filename=filename, purpose=purpose)
        path = AsyncPath(self._path(key))
        await path.parent.mkdir(parents=True, exist_ok=True)
        await path.write_bytes(content)
        return key

    async def url(self, key: str, *, private: bool = False) -> str:
        """Root-relative on purpose.

        The installation's own domain is a database setting, not something this
        adapter knows (PROJECT.md §7). A root-relative URL is correct for the
        panel and the site, which are served from that same domain, and the
        settings module can make it absolute once there is a domain to prefix.
        """
        if private:
            return f"{URL_PREFIX}/{PRIVATE_SEGMENT}/{key}"
        return f"{URL_PREFIX}/{key}"

    async def delete(self, key: str) -> None:
        try:
            await AsyncPath(self._path(key)).unlink(missing_ok=True)
        except ValueError:
            # A key that does not belong to us is already "not there".
            logger.warning("storage_delete_rejected", key=key)

    async def exists(self, key: str) -> bool:
        return self.resolve(key) is not None

    def resolve(self, key: str) -> Path | None:
        """The path to serve, or ``None`` — used by the file route.

        Synchronous, and deliberately so: it is one ``stat``, and the route
        hands the path straight to ``FileResponse``, which does the reading
        off the event loop itself.
        """
        try:
            path = self._path(key)
        except ValueError:
            return None
        return path if path.is_file() else None


__all__ = ["DEFAULT_ROOT", "PRIVATE_SEGMENT", "URL_PREFIX", "LocalStorage"]
