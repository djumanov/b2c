"""Static reference data — API.md §26.

The only module that owns no table. There is no ``models.py``, no
``repository.py`` and no migration, because none of this is ours: the
catalogues live in GTS's static service and are read through
``providers/gts/static.py`` with a long Redis cache in front.

ARCHITECTURE.md §5 describes where this ends up — a beat task syncing the
catalogues into our own tables. That is still the destination; a cached
passthrough is what stands here until the beat schedule and the rest of the
GTS client arrive with phase 2. The router and the service survive that
change: only where ``cache.py`` gets its data would move.
"""
