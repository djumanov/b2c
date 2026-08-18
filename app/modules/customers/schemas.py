"""Request and response shapes for the customer surface — API.md §18 and §19.

Handlers return these directly; the envelope is added by the route class.

``Password`` repeats the staff module's floor rather than importing it. The two
are the same number today and mean different things — one is a client's own
staff policy, the other is what the public may pick — and a shared constant
would make changing either look like changing both.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Final

from pydantic import AfterValidator, BaseModel, EmailStr, Field, field_validator

from app.core.i18n import SUPPORTED_LANGUAGES, Translated

#: A floor, not a policy (PROJECT.md §7): below this the argon2 hash stops
#: being the thing that protects the account.
MIN_PASSWORD_LENGTH: Final = 8

#: API.md §18. Four digits, as a string — leading zeros are part of the code,
#: which is why it never travels as a number.
OTP_LENGTH: Final = 4

Password = Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)]
PersonName = Annotated[str, Field(min_length=1, max_length=120)]
OtpCode = Annotated[str, Field(min_length=OTP_LENGTH, max_length=OTP_LENGTH)]


class PhoneOut(BaseModel):
    """A stored phone, in the shape GTS's booking body takes (API.md §19).

    ``phone_mask`` is nullable and the other two are not: a row only ever holds
    a phone through ``PhoneIn``, which requires both halves, so a phone that
    exists at all has them. The mask is what a client optionally saved
    alongside.
    """

    phone_code: str
    phone_number: str
    phone_mask: str | None


class PhoneIn(BaseModel):
    """Whole object or ``null`` — there is no half a phone.

    ``phone_code`` without ``phone_number`` (or the reverse) cannot be handed
    to GTS, and storing one alone would leave a profile that looks filled in
    and books nothing. ``extra="forbid"`` for the reason ``ProfileUpdateIn``
    has it: ``phone_musk`` is a typo GTS's own collection contains, and
    silently dropping it would show the client a mask it never saved.
    """

    model_config = {"extra": "forbid"}

    phone_code: Annotated[str, Field(min_length=1, max_length=8)]
    phone_number: Annotated[str, Field(min_length=1, max_length=32)]
    #: Opaque, like ``avatar_id``: the set of masks lives in §26's country
    #: catalogue, which is a GTS passthrough, so there is no list here to check
    #: against.
    phone_mask: Annotated[str | None, Field(max_length=32)] = None


class RegisterIn(BaseModel):
    """Only the address and the password are required — API.md §18.

    Everything else is a detail of the person, and the account is the address
    plus what proves it. A name the customer did not type is asked for again on
    the profile screen, which is the place that owns it.
    """

    email: EmailStr
    password: Password
    first_name: PersonName | None = None
    last_name: PersonName | None = None
    phone: PhoneIn | None = None


class RegisterConfirmIn(BaseModel):
    email: EmailStr
    code: OtpCode


class ResendCodeIn(BaseModel):
    email: EmailStr


class LoginIn(BaseModel):
    """``login`` is an email in the first release — phone + SMS is D6 work."""

    login: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class SocialLoginIn(BaseModel):
    """The identity token the provider handed the browser (API.md §18)."""

    id_token: Annotated[str, Field(min_length=1, max_length=4096)]


class TokenPairOut(BaseModel):
    """API.md §18. ``expires_in`` describes the **access** token, in seconds."""

    access_token: str
    refresh_token: str
    expires_in: int


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetVerifyIn(BaseModel):
    email: EmailStr
    code: OtpCode


class ResetTokenOut(BaseModel):
    """What ``password/reset/verify/`` hands back — single use, short-lived."""

    reset_token: str
    expires_in: int


class PasswordResetConfirmIn(BaseModel):
    reset_token: str
    new_password: Password


# --- profile (API.md §19) -------------------------------------------------------


class ProfileOut(BaseModel):
    """What the customer sees of their own row.

    ``is_blocked`` is deliberately absent: a blocked account cannot reach this
    endpoint at all (``service.get_active`` raises first), so the field could
    only ever read ``false``.

    No ``from_attributes``: ``is_profile_complete`` is not a column, and listing
    the fields is also what keeps ``password_hash`` from ever being one
    rename away from the response.
    """

    id: uuid.UUID
    email: EmailStr
    first_name: str | None
    last_name: str | None
    middle_name: str | None
    #: ``null`` when nothing was ever saved — never a half-filled object
    #: (API.md §19).
    phone: PhoneOut | None
    birth_date: date | None
    #: The client's own picture code, returned exactly as it was stored — no
    #: URL beside it, because there is no file (API.md §19).
    avatar_id: str | None
    created_at: datetime
    #: Derived — see ``Customer.is_profile_complete``. Read-only: sending it to
    #: ``PATCH`` is an unknown field like any other, so 422.
    is_profile_complete: bool


class ProfileUpdateIn(BaseModel):
    """``PATCH`` is partial — only the fields present change (API.md §8).

    ``extra="forbid"`` so that ``email`` comes back as a 422 naming the field.
    Ignoring it would be worse than refusing it: the client would render an
    address as changed that never changed, and the address is what the OTP
    proved and what password reset trusts (API.md §19).
    """

    model_config = {"extra": "forbid"}

    first_name: PersonName | None = None
    last_name: PersonName | None = None
    middle_name: PersonName | None = None
    #: ``null`` clears all three columns at once (API.md §8).
    phone: PhoneIn | None = None
    birth_date: date | None = None
    #: Not validated against a list — there is none on this side (API.md §19).
    #: The length is the only bound, and ``None`` is how the choice is cleared.
    avatar_id: Annotated[str | None, Field(max_length=64)] = None


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: Password


class AccountDeleteIn(BaseModel):
    """Why the customer is leaving — mandatory and verbatim (API.md §19).

    The texts arrive as the customer read them, in whatever language that was,
    and are never checked against the ``deletion_reasons`` dictionary — only
    bounded. No password: the token is enough (STATUS.md №71 reversed №45).
    """

    reasons: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=500)]],
        Field(min_length=1, max_length=20),
    ]

    @field_validator("reasons")
    @classmethod
    def _no_blank_reasons(cls, value: list[str]) -> list[str]:
        stripped = [reason.strip() for reason in value]
        if not all(stripped):
            raise ValueError("a reason must not be blank")
        return stripped


# --- deletion reasons (API.md §19, §34) --------------------------------------------


def _check_translated(value: Translated) -> Translated:
    """The cms module's rule, repeated: schemas are not a module's ``service``
    door, so importing it from there would cross the one line ARCHITECTURE.md
    draws."""
    filtered = {
        lang: text for lang, text in value.items() if lang in SUPPORTED_LANGUAGES
    }
    if not any(text.strip() for text in filtered.values()):
        raise ValueError(
            "at least one language must have a value; "
            f"this release serves {', '.join(SUPPORTED_LANGUAGES)}"
        )
    return filtered


class DeletionReasonIn(BaseModel):
    text: Translated
    #: Omitted means "append at the end" — the service picks max + 1.
    sort_order: int | None = None

    @field_validator("text")
    @classmethod
    def _known_languages(cls, value: Translated) -> Translated:
        return _check_translated(value)


class DeletionReasonUpdateIn(BaseModel):
    #: Merged per language, like every translated PATCH (API.md §30).
    text: Translated | None = None
    sort_order: int | None = None

    @field_validator("text")
    @classmethod
    def _known_languages(cls, value: Translated | None) -> Translated | None:
        return None if value is None else _check_translated(value)


class DeletionReasonAdminOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    text: Translated
    sort_order: int
    created_at: datetime
    updated_at: datetime


class DeletionReasonPublicOut(BaseModel):
    id: uuid.UUID
    text: str
    #: The language the text actually came back in (API.md §7).
    lang: str | None


# --- saved passengers (API.md §19) ------------------------------------------------


def _check_catalog_object(
    value: dict[str, Any] | None, key: str
) -> dict[str, Any] | None:
    """The §26 catalogue object is stored verbatim; only its identifier key is
    checked, because that is the one part the booking flow will need to hand
    back to GTS. Everything else is GTS's shape, not ours (STATUS.md §4.75).
    """
    if value is None:
        return None
    identifier = value.get(key)
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f'must be a catalog object with a non-empty "{key}"')
    return value


def _check_country(value: dict[str, Any] | None) -> dict[str, Any] | None:
    return _check_catalog_object(value, "code")


def _check_document_type(value: dict[str, Any] | None) -> dict[str, Any] | None:
    return _check_catalog_object(value, "type")


#: A country object picked from §26 ``countries/`` — stored as it came.
CountryObject = Annotated[dict[str, Any] | None, AfterValidator(_check_country)]
#: A document-type object picked from §26 ``document-types/`` — stored as it came.
DocumentTypeObject = Annotated[
    dict[str, Any] | None, AfterValidator(_check_document_type)
]


class PassengerOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    first_name: str
    last_name: str
    middle_name: str | None
    birth_date: date
    citizenship: dict[str, Any] | None
    document_type: dict[str, Any] | None
    document_number: str | None
    document_expiry_date: date | None
    created_at: datetime
    updated_at: datetime


class PassengerCreateIn(BaseModel):
    first_name: PersonName
    last_name: PersonName
    #: Optional, unlike the customer's own: a foreign passport often has no
    #: patronymic on it.
    middle_name: PersonName | None = None
    #: Required — API.md §19. A saved passenger without one still has to be
    #: retyped before it can be booked, so it would only look saved.
    birth_date: date
    citizenship: CountryObject = None
    document_type: DocumentTypeObject = None
    document_number: Annotated[str | None, Field(max_length=64)] = None
    document_expiry_date: date | None = None


class PassengerUpdateIn(BaseModel):
    model_config = {"extra": "forbid"}

    first_name: PersonName | None = None
    last_name: PersonName | None = None
    middle_name: PersonName | None = None
    birth_date: date | None = None
    citizenship: CountryObject = None
    document_type: DocumentTypeObject = None
    document_number: Annotated[str | None, Field(max_length=64)] = None
    document_expiry_date: date | None = None

    @field_validator("first_name", "last_name", "birth_date")
    @classmethod
    def _not_cleared[T](cls, value: T) -> T:
        """These three have no NULL in the table, so ``PATCH`` may leave them
        out but may not empty them.

        A **field** validator rather than a model one: ``api/errors.py`` reads
        the field name out of ``loc``, and a model validator's ``loc`` stops at
        ``("body",)`` — the 422 would say something was wrong without saying
        what, which is the one thing §3 asks of a validation error.

        Pydantic does not run validators over defaults, so this fires only when
        the key was really sent: omitted and ``null`` stay different requests.
        The optional document fields are deliberately not listed — ``null`` is
        how a customer clears one, and the staff module's habit of skipping
        ``None`` (``staff/service.py``) would silently make that impossible.
        """
        if value is None:
            raise ValueError("This field cannot be cleared")
        return value


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "OTP_LENGTH",
    "AccountDeleteIn",
    "DeletionReasonAdminOut",
    "DeletionReasonIn",
    "DeletionReasonPublicOut",
    "DeletionReasonUpdateIn",
    "LoginIn",
    "PassengerCreateIn",
    "PassengerOut",
    "PassengerUpdateIn",
    "PasswordChangeIn",
    "PhoneIn",
    "PhoneOut",
    "ProfileOut",
    "ProfileUpdateIn",
    "PasswordResetConfirmIn",
    "PasswordResetRequestIn",
    "PasswordResetVerifyIn",
    "RefreshIn",
    "RegisterConfirmIn",
    "RegisterIn",
    "ResendCodeIn",
    "SocialLoginIn",
    "ResetTokenOut",
    "TokenPairOut",
]
