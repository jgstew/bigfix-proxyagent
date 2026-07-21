"""A base class for plugins that poll devices on a per-device interval.

Many plugins share the same shape: they track a set of devices, check each one
on its own refresh interval, and persist per-device state (last check, a cached
report, a pending-deletion flag) across the plugin's short-lived runs. This
base sits on :class:`~bigfix_proxyagent.plugin.ProxyAgentPlugin` and adds the
reusable machinery that shape needs on top of a
:class:`~bigfix_proxyagent.state.DeviceStateStore`:

- :meth:`replay_cached_report` re-submits a device's cached report (freshened)
  while it is not yet due for a real check, so the agent always receives a
  report and pending actions keep flowing;
- :meth:`finalize_pending_deletions` completes deferred "delete device"
  removals once the device has been reported one last time.

The scheduling arithmetic itself lives in
:mod:`bigfix_proxyagent.scheduling` (pure functions). A concrete plugin still
implements :meth:`handle_refresh`, deciding what a device is, how to check it,
and how to build its report; this base only owns the cross-cutting mechanics.
"""

from __future__ import annotations

import email.utils
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .config import ConfigError
from .plugin import ProxyAgentPlugin
from .report import restamp_report
from .state import DeviceStateStore

log = logging.getLogger(__name__)


class ScheduledPollingPlugin(ProxyAgentPlugin):
    """A :class:`ProxyAgentPlugin` backed by a per-device state store.

    Subclasses call ``super().__init__(state)`` with their
    :class:`DeviceStateStore` (or a subclass of it) and implement
    :meth:`handle_refresh`.
    """

    def __init__(self, state: DeviceStateStore) -> None:
        self.state = state

    def replay_cached_report(
        self,
        device_id: str,
        output_directory: Path | str,
        *,
        sequence: int | None = None,
        extra: dict[str, object] | None = None,
    ) -> bool:
        """Re-submit ``device_id``'s cached report, freshened, if one exists.

        Advances the report's effective communication time to now and echoes
        ``sequence`` (when given); ``extra`` overlays any plugin-specific keys
        that should reflect current config rather than the cached snapshot
        (e.g. a re-stamped refresh interval). Writes it to ``output_directory``
        and returns True; returns False (writing nothing) when the device has
        no cached report, so the caller knows to do a real check instead.
        """
        report = self.state.cached_report(device_id)
        if report is None:
            return False
        restamp_report(
            report,
            last_server_communication=email.utils.format_datetime(
                datetime.now().astimezone()
            ),
            sequence=sequence,
        )
        if extra:
            report.update(extra)
        self.write_report(output_directory, report)
        log.info(
            "re-submitted cached report for %s (not yet due)",
            report.get("computer name", device_id),
        )
        return True

    def finalize_pending_deletions(
        self,
        device_ids: Iterable[str],
        reported_ids: set[str],
        remove_device: Callable[[str], None],
    ) -> None:
        """Complete deferred deletions for now-reported devices.

        A "delete device" action defers the real removal so the agent's
        post-action refresh still gets one last report. This finalizes it: for
        each id in ``device_ids`` that was reported this run (in
        ``reported_ids``) and is flagged pending deletion, it calls
        ``remove_device(device_id)`` and then forgets the device's state.

        ``remove_device`` performs the plugin-specific removal (e.g. dropping
        the device from the config file); if it raises :class:`ConfigError` the
        device is left flagged for a later retry rather than half-removed. State
        is saved once at the end.
        """
        for device_id in list(device_ids):
            if device_id not in reported_ids or not self.state.is_pending_deletion(
                device_id
            ):
                continue
            try:
                remove_device(device_id)
            except ConfigError as error:
                log.warning(
                    "could not finalize deletion of %s: %s", device_id, error
                )
                continue
            self.state.forget(device_id)
        self.state.save()
