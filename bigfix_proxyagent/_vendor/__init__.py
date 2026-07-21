"""Wheels bundled inside the SDK so a plugin need only vendor the SDK itself.

A pure-Python dependency the SDK can use (currently tomlkit, for
comment-preserving TOML edits) ships here as a ``<name>-*.whl`` and is loaded
by :func:`bigfix_proxyagent.vendor.load_bundled_wheel`. Making this a package
guarantees the wheels travel with the SDK wheel and are reachable via
:mod:`importlib.resources` even when the SDK is imported straight from its own
``.whl`` via :mod:`zipimport`.
"""
