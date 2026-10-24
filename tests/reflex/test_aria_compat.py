"""Compatibility — the deprecated ``navig.aria`` facade still exposes the Reflex surface."""

from __future__ import annotations


def test_aria_package_reexports_the_reflex_surface() -> None:
    import navig.aria
    import navig.reflex

    assert navig.aria.ReflexRouter is navig.reflex.ReflexRouter
    assert navig.aria.ReflexError is navig.reflex.ReflexError
    # The pre-migration class names survive as aliases for one release.
    assert navig.aria.AriaRouter is navig.reflex.ReflexRouter
    assert navig.aria.AriaError is navig.reflex.ReflexError


def test_aria_submodules_import_from_reflex() -> None:
    import navig.aria.errors as aria_errors
    import navig.aria.service as aria_service
    import navig.reflex.errors as reflex_errors
    import navig.reflex.service as reflex_service

    assert aria_service.AriaRouter is reflex_service.ReflexRouter
    assert aria_service.ReflexRouter is reflex_service.ReflexRouter
    assert aria_errors.AriaError is reflex_errors.ReflexError
    assert aria_errors.ReflexError is reflex_errors.ReflexError


def test_aria_facade_exposes_the_manifest_and_store() -> None:
    import navig.aria.manifest as aria_manifest
    import navig.aria.store as aria_store
    import navig.reflex.manifest as reflex_manifest
    import navig.reflex.store as reflex_store

    assert aria_manifest.default_registry is reflex_manifest.default_registry
    assert aria_store.CommitPlanStore is reflex_store.CommitPlanStore
