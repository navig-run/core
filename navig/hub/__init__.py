"""navig.hub — the Store aggregator (one view over every connectable thing).

Deliberately NOT named `navig.store` — that package is the SQLite data layer.
See `docs/plugin-spec.md` § The Store.
"""

from navig.hub.aggregator import (  # noqa: F401
    StoreItem,
    WireState,
    apply_action,
    collect_store,
    store_status,
)
