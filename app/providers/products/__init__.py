"""The verticals, registered.

One line per vertical, and it lives in the package's ``__init__`` rather than
beside a router or a service. Both surfaces need a populated registry and they
reach it by different roads: a request comes through ``modules/products``, and
a ticketing run comes through a Celery worker that never imports a router at
all. Registering in either place left the other with an empty registry — which
is how ticketing found no adapter for an order it had just booked.

Importing ``app.providers.products.base`` imports this package first, so there
is no import anybody has to remember to write.

Phase 3 is four more lines here.
"""

from app.providers.products.base import registry
from app.providers.products.flight import FlightAdapter

registry.register(FlightAdapter())

__all__ = ["registry"]
