"""Brain ownership — one authoritative Brain per logical deployment.

Public surface of the ``navig.brain`` package. Keep this import-light: the daemon
entry point imports it on every boot, including on a machine that is about to
refuse to start.

    from navig.brain import guard, lease, roles, telegram_mode

    # a singleton entry point, before it serves anything
    guard.require_ownership(service="gateway")

    # the operator's view
    lease.LeaseStore().status()

What lives where:

``identity``       machine-bound ``owner_id`` (never copied with a config dir)
``lease``          the record shape, acquire/renew/release/takeover, fencing epochs
``authority``      the SHARED owner record over HTTP (the Lighthouse Durable
                   Object): authoritative when configured, fail-closed when not
                   reachable. The local file is only a cache in that mode.
``guard``          the singleton guard + the process epoch writers present
``roles``          the four install choices, detection, host-dependency warning
``telegram_mode``  polling vs webhook ownership, transitions, delivery ledger
``migrate``        the guided migration and its non-lying receipt

The reasoning, invariants and the incident history are in
``docs/architecture/brain-ownership.md``.
"""

from __future__ import annotations

from navig.brain import authority, guard, identity, lease, roles, telegram_mode
from navig.brain.authority import (
    AuthorityClient,
    AuthorityError,
    AuthorityLeaseStore,
    AuthorityRefused,
    AuthorityUnavailable,
    MovedBrain,
)
from navig.brain.identity import hostname, installation_fingerprint, owner_id
from navig.brain.lease import (
    EXIT_OWNERSHIP_REFUSED,
    ROLE_ALL_IN_ONE,
    ROLE_BRAIN,
    ROLE_CLIENT,
    ROLE_EXECUTOR,
    ROLES,
    SINGLETON_ROLES,
    BrainLease,
    BrainOwnershipError,
    LeaseStore,
    OwnershipConflict,
    StaleEpoch,
    brain_settings,
    normalize_role,
)

__all__ = [
    "AuthorityClient",
    "AuthorityError",
    "AuthorityLeaseStore",
    "AuthorityRefused",
    "AuthorityUnavailable",
    "BrainLease",
    "BrainOwnershipError",
    "EXIT_OWNERSHIP_REFUSED",
    "LeaseStore",
    "MovedBrain",
    "OwnershipConflict",
    "ROLES",
    "ROLE_ALL_IN_ONE",
    "ROLE_BRAIN",
    "ROLE_CLIENT",
    "ROLE_EXECUTOR",
    "SINGLETON_ROLES",
    "StaleEpoch",
    "authority",
    "brain_settings",
    "guard",
    "hostname",
    "identity",
    "installation_fingerprint",
    "lease",
    "normalize_role",
    "owner_id",
    "roles",
    "telegram_mode",
]
