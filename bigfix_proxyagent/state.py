"""Persist per-device state across the plugin's (many, short-lived) runs.

A plugin process starts fresh on every invocation, and a device report fully
replaces the device's previous data in BigFix - there is no merge. So anything
that must survive between runs or be re-sent on every report (a last-known
error, a cached report to replay, a pending-deletion flag) has to be
remembered by the plugin itself.

:class:`DeviceStateStore` is that memory: a file keyed by device id, holding
one free-form dict per device. Two storage backends carry the same data:

- **JSON** (the default): one object keyed by device id, human-readable and
  ideal for development and testing.
- **SQLite**: one row per device; better when a deployment tracks many
  devices, and inherently concurrency-safe on save.

The in-memory model and every subclass hook (:meth:`_clean_entry` and the
accessors built on ``self._data``) are identical for both - only load and save
differ. A store looks for a SQLite file next to its JSON path and, if one is
already present, uses it regardless of the requested backend, so a store that
has been migrated to SQLite never silently reverts to JSON. See
:meth:`_resolve_backend` for the (deliberately one-way) migration rule.

The store is concurrency-safe on save (the agent may run several plugin
instances at once, never against the same device) and lets a subclass declare
exactly which keys persist via :meth:`DeviceStateStore._clean_entry`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from .report import SEQUENCE_KEYS
from .util import write_json_atomic

log = logging.getLogger(__name__)

# A SQLite store lives beside its JSON path with this suffix, so a plugin only
# ever configures one state path and the backend follows from the file present.
SQLITE_SUFFIX = ".sqlite"
# Seconds a save waits for a concurrent instance's lock before giving up; a
# plugin run is short and never contends on the same device, so this is only a
# safety margin against overlapping whole-database writes.
_SQLITE_TIMEOUT = 30.0
_SQLITE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS device_state ("
    "device_id TEXT PRIMARY KEY, entry TEXT NOT NULL)"
)


class DeviceStateStore:
    """Per-device state backed by a JSON or SQLite file (in-memory only if no
    path).

    Entries are plain dicts. Subclasses add typed accessors and override
    :meth:`_clean_entry` to whitelist and migrate their own persisted keys;
    the base persists the two generic ones (``last report``, the cached report
    for replay, and ``pending deletion``).

    ``backend`` selects the store's file format for a *new* store: ``"json"``
    (the default) or ``"sqlite"``. It is only advisory - an existing SQLite
    file always wins (see :meth:`_resolve_backend`).
    """

    def __init__(self, path: Path | None = None, *, backend: str = "json") -> None:
        self.path = Path(path) if path is not None else None
        # Anything but an explicit "sqlite" means JSON; invalid values fall
        # back to the safe default rather than raising.
        self._requested_backend = (
            "sqlite" if str(backend).lower() == "sqlite" else "json"
        )
        self._backend = self._resolve_backend()
        self._data: dict[str, dict[str, Any]] = self._read_state()
        # Devices this instance changed, tracked apart from _data so save()
        # can overlay them on the file's current contents rather than clobber
        # a concurrent instance's writes (see save()).
        self._updates: dict[str, dict[str, Any]] = {}
        self._removals: set[str] = set()

    def get(self, device_id: str) -> dict[str, Any]:
        """A copy of the device's entry (empty dict if unknown); safe to mutate
        and hand back to :meth:`update`.
        """
        return dict(self._data.get(device_id, {}))

    def update(self, device_id: str, entry: dict[str, Any]) -> None:
        """Replace the device's entry and mark it to be persisted on save."""
        entry = dict(entry)
        self._data[device_id] = entry
        self._updates[device_id] = entry

    def store_report(self, device_id: str, report: dict[str, Any]) -> None:
        """Cache a device's report so a refresh within its check interval can
        re-submit it without redoing the underlying work.

        The volatile report
        sequence keys are dropped so a stale sequence is never replayed.
        """
        entry = self.get(device_id)
        entry["last report"] = {
            key: value for key, value in report.items() if key not in SEQUENCE_KEYS
        }
        self.update(device_id, entry)

    def cached_report(self, device_id: str) -> dict[str, Any] | None:
        report = self._data.get(device_id, {}).get("last report")
        return dict(report) if isinstance(report, dict) else None

    def mark_pending_deletion(self, device_id: str) -> None:
        """Flag a device for deletion without removing it yet.

        A "delete device" action should defer the actual removal until the
        device has been reported once more, so the agent's post-action refresh
        still gets a report and the action can leave "running".
        """
        entry = self.get(device_id)
        entry["pending deletion"] = True
        self.update(device_id, entry)

    def is_pending_deletion(self, device_id: str) -> bool:
        return bool(self._data.get(device_id, {}).get("pending deletion"))

    def forget(self, device_id: str) -> None:
        """Drop all state for a device (used to finalize a deferred deletion)."""
        self._data.pop(device_id, None)
        self._updates.pop(device_id, None)
        self._removals.add(device_id)

    def save(self) -> None:
        if self._backend is None:
            return
        try:
            self._backend.save(self._updates, self._removals)
        except (OSError, sqlite3.Error) as error:
            # Losing state must not break the plugin's real work.
            log.warning("could not write state file %s: %s", self.path, error)

    def _clean_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Return the persisted subset of one raw entry read from disk.

        The base keeps only the generic keys it manages. A subclass overrides
        this, calls ``super()._clean_entry(entry)``, and adds its own keys
        (and any format migration). An entry that cleans to empty is dropped.
        """
        cleaned: dict[str, Any] = {}
        if isinstance(entry.get("last report"), dict):
            cleaned["last report"] = entry["last report"]
        if entry.get("pending deletion") is True:
            cleaned["pending deletion"] = True
        return cleaned

    def _resolve_backend(self) -> _StateBackend | None:
        """Pick the storage backend for this store, honoring the one-way rule.

        With no path the store is in-memory only (``None``). Otherwise a
        SQLite file beside the JSON path always wins - once a store has been
        migrated to SQLite it never reverts to JSON, whatever ``backend`` asks
        for. If SQLite is requested and none exists yet, a new one is created
        and seeded once from the existing JSON file (if any); that migration is
        deliberately one-way (there is no SQLite-to-JSON path).
        """
        if self.path is None:
            return None
        sqlite_path = self.path.with_suffix(SQLITE_SUFFIX)
        if sqlite_path.exists():
            return _SqliteBackend(sqlite_path)
        if self._requested_backend == "sqlite":
            backend = _SqliteBackend(sqlite_path)
            if self.path.is_file():
                backend.migrate_from_json(self.path)
            return backend
        return _JsonBackend(self.path)

    def _read_state(self) -> dict[str, dict[str, Any]]:
        if self._backend is None:
            return {}
        state: dict[str, dict[str, Any]] = {}
        for device, entry in self._backend.load().items():
            if not isinstance(entry, dict):
                continue
            cleaned = self._clean_entry(entry)
            if cleaned:
                state[device] = cleaned
        return state


class _StateBackend:
    """Raw persistence for the store: load the device->entry map, and save one
    instance's changes with merge-on-save semantics (only touch the devices.

    this instance changed, so a concurrent instance's writes survive).

    Backends deal in raw dicts; whitelisting via ``_clean_entry`` is the
    store's concern and happens on load.
    """

    def load(self) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def save(
        self, updates: dict[str, dict[str, Any]], removals: set[str]
    ) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class _JsonBackend(_StateBackend):
    """One JSON object keyed by device id."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        return _load_json_object(self.path)

    def save(self, updates: dict[str, dict[str, Any]], removals: set[str]) -> None:
        # Another instance may have saved since we loaded, so re-read and
        # overlay only this instance's changes instead of rewriting wholesale.
        current = _load_json_object(self.path)
        current.update(updates)
        for device in removals:
            current.pop(device, None)
        write_json_atomic(self.path, current)


class _SqliteBackend(_StateBackend):
    """One row per device, each entry stored as a JSON blob.

    Per-device UPSERT/DELETE is the concurrency mechanism: it only touches the
    rows this instance changed, so overlapping saves from different instances
    never clobber each other and no whole-file re-read is needed.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=_SQLITE_TIMEOUT)
        conn.execute(_SQLITE_SCHEMA)
        return conn

    def load(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        try:
            conn = self._connect()
        except sqlite3.Error as error:
            log.warning("state db %s unreadable, starting fresh: %s", self.path, error)
            return data
        try:
            for device_id, raw in conn.execute(
                "SELECT device_id, entry FROM device_state"
            ):
                try:
                    entry = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(entry, dict):
                    data[device_id] = entry
        finally:
            conn.close()
        return data

    def save(self, updates: dict[str, dict[str, Any]], removals: set[str]) -> None:
        if not updates and not removals:
            return
        conn = self._connect()
        try:
            with conn:  # commit on success, roll back on error
                for device_id, entry in updates.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO device_state (device_id, entry) "
                        "VALUES (?, ?)",
                        (device_id, json.dumps(entry, ensure_ascii=False)),
                    )
                for device_id in removals:
                    conn.execute(
                        "DELETE FROM device_state WHERE device_id = ?", (device_id,)
                    )
        finally:
            conn.close()

    def migrate_from_json(self, json_path: Path) -> None:
        """Seed a fresh SQLite store once from an existing JSON file.

        Uses ``INSERT OR IGNORE`` so it never overwrites a row already present
        (idempotent, and safe if two instances migrate at once), and leaves the
        JSON file in place as a manual fallback. Any failure is logged, not
        raised - a failed migration must not break the plugin's real work.
        """
        raw = _load_json_object(json_path)
        if not raw:
            return
        try:
            conn = self._connect()
            try:
                with conn:
                    for device_id, entry in raw.items():
                        if not isinstance(entry, dict):
                            continue
                        conn.execute(
                            "INSERT OR IGNORE INTO device_state (device_id, entry) "
                            "VALUES (?, ?)",
                            (device_id, json.dumps(entry, ensure_ascii=False)),
                        )
            finally:
                conn.close()
            log.info(
                "migrated state from %s to %s (JSON file left in place)",
                json_path,
                self.path,
            )
        except (OSError, sqlite3.Error) as error:
            log.warning("could not migrate state to %s: %s", self.path, error)


def _load_json_object(path: Path | None) -> dict[str, Any]:
    """Load a JSON object from ``path``; return ``{}`` on any problem."""
    if path is None or not Path(path).is_file():
        return {}
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        log.warning("state file %s unreadable, starting fresh: %s", path, error)
        return {}
    return data if isinstance(data, dict) else {}
