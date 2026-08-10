"""First boot only: the owner, and the relay that mails them (PROJECT.md §14).

The client installs this themselves and has no panel to log into until one
staff account exists, so the very first one comes from the environment. It is
created once: if any staff row already exists, this does nothing, which is what
makes it safe to run on every container start.

The SMTP seed follows the same rule and is here for the same reason — a fresh
installation cannot mail the password-reset code its own first sign-in may need
until *something* has configured a relay, and there is nobody signed in yet to
do it. Once the row has a host, the panel owns it and ``.env`` is ignored.

Run as a script (``python /app/docker/bootstrap.py``), which puts *this*
directory on ``sys.path`` rather than the project root — hence the insert
below. Without it ``import app`` fails, and a first boot would quietly end with
nobody able to sign in.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.integrations import bootstrap as smtp_bootstrap  # noqa: E402
from app.modules.staff import bootstrap  # noqa: E402


async def main() -> int:
    seeded = await smtp_bootstrap.configure_smtp()
    print(
        "bootstrap: smtp configured from the environment"
        if seeded
        else "bootstrap: smtp already configured or SMTP_* is unset, skipping"
    )

    # Last, because it disposes the engine on its way out.
    created = await bootstrap.create_first_owner()
    print(
        "bootstrap: first owner created"
        if created
        else "bootstrap: staff already exist or FIRST_OWNER_* is unset, skipping"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
