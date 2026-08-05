"""Which storage the application writes through.

One accessor, so no caller knows whether files land on a disk or in a bucket.
``set_storage`` is the test seam, matching ``db.redis.set_redis``.
"""

from app.providers.storage.base import Storage
from app.providers.storage.local import LocalStorage

_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = LocalStorage()
    return _storage


def set_storage(storage: Storage | None) -> None:
    global _storage
    _storage = storage


__all__ = ["get_storage", "set_storage"]
