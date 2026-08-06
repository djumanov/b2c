"""Reading an uploaded body without trusting its size.

Two surfaces accept files — ``/admin/uploads/`` (API.md §11) and
``/public/profile/avatar/`` (§19) — and both need the same guard, so it lives
here rather than in either module. A router importing another module's router
to borrow a helper is the shape ARCHITECTURE.md §4 exists to prevent.

Validation itself stays where the rules are (``uploads.rules``): this only
decides how many bytes are worth pulling into memory before somebody else gets
to say no.
"""

from typing import Final

from fastapi import UploadFile

_CHUNK: Final = 64 * 1024


async def read_limited(file: UploadFile, *, limit: int) -> bytes:
    """Read the body, but stop just past ``limit``.

    ``await file.read()`` with no argument would pull an arbitrarily large body
    into memory before anything got the chance to reject it. Callers pass one
    byte over the largest size they will accept, so an oversized upload still
    arrives long enough to be refused with a 422 naming the field — rather than
    as a bare 413 from the reverse proxy, which says nothing a client can act
    on.
    """
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


__all__ = ["read_limited"]
