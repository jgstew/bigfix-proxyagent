"""Device-identity helpers.

A plugin must give each device a unique string id and keep it stable across
runs (it is the report file's name and the device's identity in BigFix).
Deriving it deterministically from something intrinsic to the device avoids
maintaining an identity database: servermon hashes the normalized URL, an SNMP
plugin might hash the host plus an engine id.
"""

from __future__ import annotations

import hashlib


def stable_device_id(key: str) -> str:
    """A stable device id: the SHA-256 hex digest of ``key``.

    Pass a value that is normalized and unique per device, so the same device
    always yields the same id (and two distinct devices never collide).
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
