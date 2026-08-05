"""Create the first ``owner`` — first boot only (PROJECT.md §14).

The client installs this themselves and has no panel to log into until one
staff account exists, so the very first one comes from the environment. It is
created once: if any staff row already exists, this does nothing, which is what
makes it safe to run on every container start.

A no-op until the ``staff`` module and its table exist.
"""

import asyncio
import sys


async def main() -> int:
    try:
        from app.modules.staff import bootstrap  # type: ignore[attr-defined]
    except ImportError:
        print("bootstrap: staff module not built yet, nothing to do")
        return 0

    created = await bootstrap.create_first_owner()
    print(
        "bootstrap: first owner created"
        if created
        else "bootstrap: staff already exist, skipping"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
