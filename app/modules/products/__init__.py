"""The stateless search flow — ``/public/{product}/…`` (API.md §20).

One generic router for five verticals, bound to a ``ProductAdapter`` from the
registry (ARCHITECTURE.md §6). The module owns **no table, no models, no
repository, no migration**: search is a passthrough and GTS keeps the only
cache, keyed by ``request_id`` (D2, ARCHITECTURE.md §9). A contract test
guards that nothing is stored.
"""
