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
"""

from __future__ import annotations

import glob
import importlib
import os
import sys
from pathlib import Path
from types import ModuleType


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


def vendored_wheel_name(name: str, vendor_dir: Path | str) -> str | None:
    """Basename of the newest vendored ``<name>-*.whl``, for diagnostics."""
    wheel = _newest_wheel(name, vendor_dir)
    return os.path.basename(wheel) if wheel else None


def _newest_wheel(name: str, vendor_dir: Path | str) -> str | None:
    wheels = glob.glob(str(Path(vendor_dir) / f"{name}-*.whl"))
    return sorted(wheels)[-1] if wheels else None
