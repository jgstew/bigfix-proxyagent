"""Load a vendored pure-Python dependency shipped as a wheel.

A Proxy Agent plugin deploys as a folder copied under the Management Extender
and runs straight from that checkout - there is no ``pip install`` step. A
plugin that needs a third-party package (including this SDK itself) therefore
ships it as a wheel in the plugin's own ``vendor/`` directory and loads it with
:func:`load_wheel`.

A wheel is a zip and a pure-Python package needs no compilation, so
:mod:`zipimport` can import straight from the ``.whl`` on ``sys.path`` - no
unpacking required. Any failure returns ``None`` so a plugin can fall back
rather than crash.

The SDK also *bundles* its own pure-Python helper (tomlkit) inside its wheel;
:func:`load_bundled_wheel` loads that. :func:`load_wheel_or_bundled` is the
standard entry point for a plugin: it prefers a loose wheel the plugin ships in
its own ``vendor/`` (so a plugin can pin its own version) and otherwise uses
the SDK's bundled copy - so a plugin works with zero extra wheels but can still
override.
"""

from __future__ import annotations

import glob
import importlib
import os
import sys
import tempfile
from importlib import resources
from pathlib import Path
from types import ModuleType

from .util import write_bytes_atomic

# Real filesystem paths of wheels extracted from inside the SDK's own zip,
# keyed by wheel basename. Kept for the process lifetime: sys.path references
# them and imports happen lazily, so the temp files must outlive this call.
_extracted_bundled: dict[str, str] = {}

# The plugin's own ``vendor/`` directory, registered once at startup via
# set_plugin_vendor_dir(). It lets SDK-internal loads (e.g. the config editor's
# tomlkit) prefer a loose wheel the plugin ships there over the SDK's bundled
# copy, with no per-call wiring. A process runs a single plugin, so a
# module-level value is the natural scope.
_plugin_vendor_dir: Path | None = None


def set_plugin_vendor_dir(vendor_dir: Path | str | None) -> None:
    """Register the plugin's own ``vendor/`` directory (call once at startup).

    Afterwards :func:`load_wheel_or_bundled` - including the SDK's own use of
    it for tomlkit - prefers a loose ``<name>-*.whl`` there before the SDK's
    bundled copy. Pass ``None`` to clear it (mainly for tests).
    """
    global _plugin_vendor_dir
    _plugin_vendor_dir = Path(vendor_dir) if vendor_dir is not None else None


def plugin_vendor_dir() -> Path | None:
    """The directory registered with :func:`set_plugin_vendor_dir`, or ``None``."""
    return _plugin_vendor_dir


def load_wheel_or_bundled(
    name: str,
    vendor_dir: Path | str | None = None,
    *,
    import_name: str | None = None,
) -> ModuleType | None:
    """Load ``name`` with the standard plugin precedence, or ``None``.

    In order: an already-importable copy (e.g. pip-installed); then the newest
    loose ``<name>-*.whl`` in ``vendor_dir`` (a real file, loaded with no
    extraction - and it wins, so a plugin can pin its own version); then the
    copy bundled inside the SDK. ``vendor_dir`` defaults to the directory
    registered with :func:`set_plugin_vendor_dir`; when neither is given only
    the bundled copy is tried. This is the mechanism every plugin should use.

    When the bundled copy is used and ``vendor_dir`` is writable, the wheel is
    first copied into ``vendor_dir`` so this and every future run load it
    directly from that real file - avoiding the per-process extraction the
    SDK's zip-in-zip bundling would otherwise need.
    """
    if vendor_dir is None:
        vendor_dir = _plugin_vendor_dir
    if vendor_dir is not None:
        module = load_wheel(name, vendor_dir, import_name=import_name)
        if module is not None:
            return module
        if _cache_bundled_in_vendor(name, vendor_dir):
            module = load_wheel(name, vendor_dir, import_name=import_name)
            if module is not None:
                return module
    return load_bundled_wheel(name, import_name=import_name)


def load_wheel(
    name: str, vendor_dir: Path | str, *, import_name: str | None = None
) -> ModuleType | None:
    """Return the named module, or ``None`` if it cannot be loaded.

    Uses the package if it is already importable (e.g. pip-installed);
    otherwise adds the newest matching ``<name>-*.whl`` in ``vendor_dir`` to
    ``sys.path`` and imports from there.

    ``name`` is the distribution name (the wheel's filename prefix);
    ``import_name`` is the importable module name when it differs from the
    distribution name (e.g. ``PyYAML`` -> ``yaml``).
    """
    import_name = import_name or name
    try:
        return importlib.import_module(import_name)
    except ImportError:
        pass

    try:
        wheel = _newest_wheel(name, vendor_dir)
        if wheel is None:
            return None
        if wheel not in sys.path:
            sys.path.insert(0, wheel)
        return importlib.import_module(import_name)
    except Exception:
        # A corrupt or incompatible wheel must not take the plugin down.
        return None


def load_bundled_wheel(
    name: str, *, import_name: str | None = None
) -> ModuleType | None:
    """Return a module from a wheel bundled inside this SDK, or ``None``.

    The SDK ships pure-Python helper wheels (e.g. tomlkit) under its own
    ``_vendor/`` package. A plugin therefore vendors only the SDK wheel; the
    SDK reaches its bundled dependency itself. Uses the package if it is
    already importable; otherwise loads ``<name>-*.whl`` from ``_vendor/``.

    The SDK may itself be running straight from its ``.whl`` via
    :mod:`zipimport`, which cannot import from a zip nested inside a zip, so
    the bundled wheel's bytes are extracted to a temp file first. When the SDK
    is on disk (pip-installed or a source checkout) the real path is used with
    no copy. Any failure returns ``None`` so the caller can fall back.
    """
    import_name = import_name or name
    try:
        return importlib.import_module(import_name)
    except ImportError:
        pass

    try:
        wheel = _bundled_wheel_path(name)
        if wheel is None:
            return None
        if wheel not in sys.path:
            sys.path.insert(0, wheel)
        return importlib.import_module(import_name)
    except Exception:
        # A missing, corrupt, or incompatible bundled wheel must not crash.
        return None


def bundled_wheel_name(name: str) -> str | None:
    """Basename of the bundled ``<name>-*.whl``, for diagnostics."""
    try:
        resource = _newest_bundled_resource(name)
    except Exception:
        return None
    return resource.name if resource is not None else None


def vendored_wheel_name(name: str, vendor_dir: Path | str) -> str | None:
    """Basename of the newest vendored ``<name>-*.whl``, for diagnostics."""
    wheel = _newest_wheel(name, vendor_dir)
    return os.path.basename(wheel) if wheel else None


def _newest_wheel(name: str, vendor_dir: Path | str) -> str | None:
    wheels = glob.glob(str(Path(vendor_dir) / f"{name}-*.whl"))
    return sorted(wheels)[-1] if wheels else None


def _cache_bundled_in_vendor(name: str, vendor_dir: Path | str) -> bool:
    """Copy the SDK's bundled ``<name>-*.whl`` into ``vendor_dir`` once.

    Returns ``True`` if a loose wheel is now present there (so the caller can
    load it directly). A no-op returning ``False`` when the bundled wheel is
    already a real on-disk file (:func:`load_bundled_wheel` loads that without
    extracting, so there is nothing to gain) or on any error - e.g. a
    read-only ``vendor_dir`` - so caching never breaks the fall-through to
    per-process bundled loading.
    """
    try:
        resource = _newest_bundled_resource(name)
        if resource is None:
            return False
        # On-disk bundled wheel: loaded directly already, no copy needed.
        try:
            if os.path.isfile(os.fspath(resource)):
                return False
        except TypeError:
            pass  # zip-backed resource: caching avoids per-process extraction.
        target = Path(vendor_dir) / resource.name
        if not target.exists():
            write_bytes_atomic(target, resource.read_bytes())
        return True
    except Exception:
        return False


def _newest_bundled_resource(name: str):
    """Newest ``<name>-*.whl`` :class:`~importlib.resources.abc.Traversable`
    under the SDK's ``_vendor/`` package, or ``None``.
    """
    anchor = resources.files(__package__).joinpath("_vendor")
    prefix = f"{name}-"
    matches = [
        item
        for item in anchor.iterdir()
        if item.name.startswith(prefix) and item.name.endswith(".whl")
    ]
    return max(matches, key=lambda item: item.name) if matches else None


def _bundled_wheel_path(name: str) -> str | None:
    """Real filesystem path to the newest bundled ``<name>-*.whl``.

    Returns the resource's own path when the SDK lives on disk; extracts it to
    a temp file (cached for the process) when the SDK is inside a zip.
    """
    resource = _newest_bundled_resource(name)
    if resource is None:
        return None

    # On disk already (pip-installed or source checkout): use it in place.
    try:
        direct = os.fspath(resource)
        if os.path.isfile(direct):
            return direct
    except TypeError:
        pass  # zip-backed resource: os.fspath is unsupported, so extract it.

    if resource.name in _extracted_bundled:
        return _extracted_bundled[resource.name]
    # Keep the ".whl" suffix so the import machinery treats it as a wheel.
    handle, tmp = tempfile.mkstemp(suffix=f"-{resource.name}")
    with os.fdopen(handle, "wb") as out:
        out.write(resource.read_bytes())
    _extracted_bundled[resource.name] = tmp
    return tmp
