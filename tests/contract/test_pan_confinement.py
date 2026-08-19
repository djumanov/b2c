"""Only ``payments`` may open a secret — ARCHITECTURE.md §5, PROJECT.md §13.

The documents make a claim that no test has ever held them to: *everything that
touches a card number lives in the ``payments`` module, in one place*. Until now
that was a habit, and habits are kept until the week somebody is in a hurry.

This reads the source rather than exercising it, because the failure it guards
against is a line that is never reached in a test — a decrypt added to a report,
a ``get_secret_value`` in a router that logs what it found. Both are one-line
changes that no behavioural test would notice and both would move the PAN out of
the one module the design confines it to.

The allow-list is short on purpose. Growing it is the decision this test exists
to make somebody take deliberately.
"""

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

#: Unwrapping a ``SecretStr``: only where a card number is turned into digits
#: for a provider, which is ``payments`` and nowhere else.
_UNWRAPS = "get_secret_value"

#: Opening a ciphertext. ``core.crypto`` defines it; ``integrations`` opens
#: provider and GTS credentials; ``payments`` opens the stored card number and
#: the provider's card token. Nothing else has a key to anything.
_DECRYPT_ALLOWED = {
    pathlib.Path("core/crypto.py"),
    pathlib.Path("modules/integrations/service.py"),
    pathlib.Path("modules/payments/service.py"),
}

#: ``payments`` owns the card. The schema validates it, the service spends it.
_UNWRAP_ALLOWED = {
    pathlib.Path("modules/payments/schemas.py"),
    pathlib.Path("modules/payments/service.py"),
}


def _calls_named(tree: ast.AST, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == name:
                return True
            if isinstance(func, ast.Name) and func.id == name:
                return True
    return False


def _sources() -> list[tuple[pathlib.Path, ast.AST]]:
    parsed: list[tuple[pathlib.Path, ast.AST]] = []
    for path in sorted(APP.rglob("*.py")):
        parsed.append((path.relative_to(APP), ast.parse(path.read_text())))
    return parsed


def test_only_payments_unwraps_a_secret_string() -> None:
    parsed = _sources()
    assert parsed, "no sources parsed — the sweep would pass vacuously"

    offenders = {
        path for path, tree in parsed if _calls_named(tree, _UNWRAPS)
    } - _UNWRAP_ALLOWED

    assert offenders == set(), (
        "a card number is unwrapped outside `payments`: "
        + ", ".join(sorted(str(path) for path in offenders))
    )


def test_only_the_modules_that_own_a_key_decrypt() -> None:
    parsed = _sources()
    offenders = {
        path for path, tree in parsed if _calls_named(tree, "decrypt")
    } - _DECRYPT_ALLOWED

    assert offenders == set(), (
        "a ciphertext is opened outside the modules that own one: "
        + ", ".join(sorted(str(path) for path in offenders))
    )


def test_the_allow_lists_are_not_stale() -> None:
    """Every file named above must still do the thing it is excused for.

    An allow-list nobody prunes becomes a list of files that *may* leak, and the
    day one of them stops decrypting is the day the entry should go rather than
    sit there permitting a future line.
    """
    by_path = dict(_sources())

    for path in _DECRYPT_ALLOWED - {pathlib.Path("core/crypto.py")}:
        assert path in by_path, f"{path} no longer exists"
        assert _calls_named(by_path[path], "decrypt"), f"{path} no longer decrypts"

    for path in _UNWRAP_ALLOWED:
        assert path in by_path, f"{path} no longer exists"
        assert _calls_named(by_path[path], _UNWRAPS), f"{path} no longer unwraps one"
