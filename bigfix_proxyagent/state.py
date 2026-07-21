"""Persist per-device state across the plugin's (many, short-lived) runs.

A plugin process starts fresh on every invocation, and a device report fully
replaces the device's previous data in BigFix - there is no merge. So anything
that must survive between runs or be re-sent on every report (a last-known
error, a cached report to replay, a pending-deletion flag) has to be
remembered by the plugin itself.

:class:`DeviceStateStore` is that memory: a JSON file keyed by device id,
holding one free-form dict per device. It is concurrency-safe on save (the
agent may run several plugin instances at once, never against the same
device) and lets a subclass declare exactly which keys persist via
:meth:`DeviceStateStore._clean_entry`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .report import SEQUENCE_KEYS
from .util import write_json_atomic

log = logging.getLogger(__name__)


class DeviceStateStore:
    """Per-device state backed by a JSON file (in-memory only if no path).

    Entries are plain dicts. Subclasses add typed accessors and override
    :meth:`_clean_entry` to whitelist and migrate their own persisted keys;
    the base persists the two generic ones (``last report``, the cached report
    for replay, and ``pending deletion``).
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any]] = self._read_state(path)
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
        re-submit it without redoing the underlying work. The volatile report
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
        if self.path is None:
            return
        try:
            # Another instance may have saved since we loaded, so re-read and
            # overlay only this instance's changes instead of rewriting wholesale.
            current = self._read_state(self.path)
            current.update(self._updates)
            for device in self._removals:
                current.pop(device, None)
            write_json_atomic(self.path, current)
        except OSError as error:
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

    def _read_state(self, path: Path | None) -> dict[str, dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        for device, entry in _load_json_object(path).items():
            if not isinstance(entry, dict):
                continue
            cleaned = self._clean_entry(entry)
            if cleaned:
                state[device] = cleaned
        return state


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
