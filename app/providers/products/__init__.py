"""The verticals, registered.

One line per vertical, and it lives in the package's ``__init__`` rather than
beside a router or a service. A request reaches the registry through
``modules/products``, but a worker process never imports a router at all, and
registering beside one left the other road with an empty registry.

Importing ``app.providers.products.base`` imports this package first, so there
is no import anybody has to remember to write.

Phase 3 is four more lines here.
"""

from app.providers.products.base import registry
from app.providers.products.flight import FlightAdapter

registry.register(FlightAdapter())

__all__ = ["registry"]
