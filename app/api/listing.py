"""The list-query contract (API.md §6), expressed once.

Every list endpoint on both surfaces accepts the same five parameters —
``page``, ``page_size``, ``search``, ``ordering``, ``created_from`` and
``created_to``. ``page``/``page_size`` are already ``deps.Pagination``; the
other four are here, together with the helpers that turn them into SQL and the
one that builds the ``Page`` the envelope expects.

Two decisions worth stating, because both are easy to get wrong quietly:

* **``ordering`` is a whitelist, never a column name from the request.** The
  caller sends a field, the endpoint maps it to a column it chose to expose. A
  pass-through would let a client order by ``password_hash`` and learn its
  prefix, and it would make every column name part of the public contract.
* **Ordering always ends in a tiebreak.** Two rows with the same
  ``created_at`` have no defined order, so without one, page 2 can repeat a row
  page 1 already showed and skip another entirely.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Final

from fastapi import Depends, Query
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.api.deps import Pagination
from app.api.envelope import Page, PageMeta
from app.api.errors import ValidationFailed

#: Anything an endpoint may search, filter or order by: a mapped column
#: (``Staff.email``) or an expression over one (``func.lower(Staff.email)``).
#: Both are spelled out because SQLAlchemy's ``InstrumentedAttribute`` is not a
#: ``ColumnElement`` as far as the type checker is concerned.
type ColumnExpr = InstrumentedAttribute[Any] | ColumnElement[Any]

#: What ``ordering`` may name: the client's field → the column it maps to.
OrderingMap = Mapping[str, ColumnExpr]


@dataclass(frozen=True, slots=True)
class ListQuery:
    """The filters shared by every list endpoint (API.md §6)."""

    search: str | None = None
    ordering: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


def _as_utc(moment: datetime | None) -> datetime | None:
    """A date sent without an offset is read as UTC (API.md §1)."""
    if moment is None:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


_SEARCH_TEXT: Final = (
    "Case-insensitive substring match over {columns}. Leading and trailing "
    "spaces are ignored; empty means no filter."
)
_ORDERING_TEXT: Final = (
    "Sort field, `-` prefix for descending: {fields}. Default `{default}`. "
    "Anything else is a `422` naming the choices."
)
_CREATED_FROM_TEXT: Final = (
    "Only rows created at or after this moment (ISO 8601; without an offset "
    "it is read as UTC)."
)
_CREATED_TO_TEXT: Final = (
    "Only rows created at or before this moment (inclusive; ISO 8601; without "
    "an offset it is read as UTC)."
)


def _build(
    search: str | None,
    ordering: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> ListQuery:
    term = search.strip() if search else None
    return ListQuery(
        search=term or None,
        ordering=ordering.strip() if ordering else None,
        created_from=_as_utc(created_from),
        created_to=_as_utc(created_to),
    )


def list_query(
    search: Annotated[
        str | None,
        Query(description=_SEARCH_TEXT.format(columns="the fields this list searches")),
    ] = None,
    ordering: Annotated[
        str | None,
        Query(
            description=(
                "Sort field, `-` prefix for descending, from this endpoint's "
                "whitelist. Anything else is a `422` naming the choices."
            )
        ),
    ] = None,
    created_from: Annotated[
        datetime | None, Query(description=_CREATED_FROM_TEXT)
    ] = None,
    created_to: Annotated[datetime | None, Query(description=_CREATED_TO_TEXT)] = None,
) -> ListQuery:
    return _build(search, ordering, created_from, created_to)


ListQueryDep = Annotated[ListQuery, Depends(list_query)]


def list_query_dep(
    *, ordering: Sequence[str], default: str, search: str
) -> Callable[..., ListQuery]:
    """``list_query`` whose parameter descriptions name *this* endpoint's
    sort fields and searched columns — the whitelist a client otherwise
    discovers only by tripping the 422. Behaviour is ``list_query``'s.

    ``ordering`` is the whitelist the service hands to ``apply_ordering``;
    ``default`` its default spelling (``"-created_at"``); ``search`` a phrase
    such as ``"PNR and route"``.
    """
    fields = ", ".join(f"`{field}`" for field in ordering)

    def dependency(
        search_: Annotated[
            str | None,
            Query(alias="search", description=_SEARCH_TEXT.format(columns=search)),
        ] = None,
        ordering_: Annotated[
            str | None,
            Query(
                alias="ordering",
                description=_ORDERING_TEXT.format(fields=fields, default=default),
            ),
        ] = None,
        created_from: Annotated[
            datetime | None, Query(description=_CREATED_FROM_TEXT)
        ] = None,
        created_to: Annotated[
            datetime | None, Query(description=_CREATED_TO_TEXT)
        ] = None,
    ) -> ListQuery:
        return _build(search_, ordering_, created_from, created_to)

    return dependency


# --- turning the query into SQL -------------------------------------------------


def apply_search[RowT](
    stmt: Select[tuple[RowT]], query: ListQuery, *columns: ColumnExpr
) -> Select[tuple[RowT]]:
    """Case-insensitive substring match across the columns the endpoint offers.

    ``ILIKE '%term%'`` cannot use a plain b-tree index. That is accepted: these
    are panel searches over operator-sized tables, and the alternative — full
    text search with its own configuration per language — is a lot of machinery
    for "find the staff member called Aziz".
    """
    if not query.search or not columns:
        return stmt
    pattern = f"%{query.search}%"
    return stmt.where(or_(*(column.ilike(pattern) for column in columns)))


def apply_created_range[RowT](
    stmt: Select[tuple[RowT]], query: ListQuery, column: ColumnExpr
) -> Select[tuple[RowT]]:
    """``created_from`` / ``created_to``, both inclusive."""
    if query.created_from is not None:
        stmt = stmt.where(column >= query.created_from)
    if query.created_to is not None:
        stmt = stmt.where(column <= query.created_to)
    return stmt


def apply_ordering[RowT](
    stmt: Select[tuple[RowT]],
    query: ListQuery,
    *,
    allowed: OrderingMap,
    default: str,
    tiebreak: ColumnExpr,
) -> Select[tuple[RowT]]:
    """``?ordering=name`` or ``?ordering=-created_at`` — whitelisted (API.md §6).

    ``default`` is the endpoint's own choice and uses the same syntax, so a
    resource that lists newest-first writes ``default="-created_at"``.
    """
    requested = query.ordering or default
    descending = requested.startswith("-")
    field = requested.removeprefix("-")

    column = allowed.get(field)
    if column is None:
        raise ValidationFailed(
            f"Cannot order by {field!r}. Allowed: {', '.join(sorted(allowed))}",
            field="ordering",
        )
    return stmt.order_by(column.desc() if descending else column.asc(), tiebreak.asc())


# --- paging ---------------------------------------------------------------------


async def paginate[RowT](
    session: AsyncSession, stmt: Select[tuple[RowT]], pagination: Pagination
) -> tuple[Sequence[RowT], int]:
    """Run the query for one page and count the whole thing.

    Two round trips, on purpose. A window-function count travels with every row
    of the page, and the count is what ``meta.total_pages`` needs before the
    client can render a pager at all (API.md §6).
    """
    total = await session.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    rows = await session.scalars(stmt.offset(pagination.offset).limit(pagination.limit))
    return rows.all(), int(total or 0)


def page[ItemT](
    items: Sequence[ItemT], pagination: Pagination, total: int
) -> Page[ItemT]:
    """Wrap rows and their count in what a list handler returns.

    The handler returns this and nothing else — ``EnvelopeRoute`` lifts
    ``items`` into ``data`` and ``meta`` into ``meta`` (``api/envelope.py``).
    """
    return Page(
        items=list(items),
        meta=PageMeta.build(
            page=pagination.page, page_size=pagination.page_size, total=total
        ),
    )


__all__ = [
    "ColumnExpr",
    "ListQuery",
    "ListQueryDep",
    "list_query_dep",
    "OrderingMap",
    "apply_created_range",
    "apply_ordering",
    "apply_search",
    "list_query",
    "page",
    "paginate",
]
