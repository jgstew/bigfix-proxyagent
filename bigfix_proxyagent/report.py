"""Build the device reports a plugin writes to ``<device id>.report``.

Every plugin's report needs the same handful of keys the Proxy Agent itself
understands - the three mandatory identity keys, the standard "proxy agent
plugin" object, the effective-communication timestamp, and the echoed report
sequence. :func:`base_report` fills those; a plugin adds its own inspector
data (the domain payload) on top of the returned dict.

See "Device reports" in the servermon repo's
``bigfix/reference-files/ProxyAgents.md``.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

# The two spellings of the per-device report sequence number. The correct key
# casing is not publicly documented, so a report echoes both; consumers that
# cache a report should drop these so a stale sequence is never re-submitted.
SEQUENCE_KEYS = ("device report sequence", "deviceReportSequence")


def local_host_name() -> str:
    """The host running this plugin (the Management Extender / BigFix relay).

    Resolved with a local syscall, no network lookup; falls back to
    ``"Unknown"`` if the hostname cannot be determined. Suitable to resolve
    once at import and reuse - it does not change for the life of the process.
    """
    try:
        return socket.gethostname()
    except OSError:
        return "Unknown"


def base_report(
    device_id: str,
    computer_name: str,
    data_source: str,
    *,
    last_server_communication: str,
    plugin_name: str | None = None,
    plugin_version: str | None = None,
    plugin_host: str | None = None,
    plugin_last_report_time: str | None = None,
    last_device_report_time: str | None = None,
    sequence: int | None = None,
) -> dict[str, Any]:
    """Return a device report pre-filled with the Proxy-Agent-understood keys.

    Mandatory identity keys the agent cannot register a device without:
    ``device id``, ``data source``, ``computer name``.

    - ``last_server_communication`` becomes the "effective device
      communication time"; the agent treats a report as new only when it
      advances, so pass the moment the device was contacted/checked.
    - the ``proxy agent plugin`` object is the standard inspector describing
      this plugin; ``plugin_name`` defaults to ``data_source`` and
      ``plugin_last_report_time`` defaults to ``last_server_communication``.
      ``version`` and ``host`` are included only when given.
    - ``last_device_report_time``, when set, becomes the console's Last Report
      Time (let it lag ``last_server_communication`` to show a device as stale
      while still keeping its reports fresh).
    - ``sequence`` echoes a refresh's report sequence number back to the agent.

    A plugin mutates the returned dict to attach its own inspector data.
    """
    report: dict[str, Any] = {
        "device id": device_id,
        "data source": data_source,
        "computer name": computer_name,
        "in proxy agent context": True,
        "last server communication": last_server_communication,
    }
    plugin_obj: dict[str, Any] = {"name": plugin_name or data_source}
    if plugin_version is not None:
        plugin_obj["version"] = plugin_version
    if plugin_host is not None:
        plugin_obj["host"] = plugin_host
    plugin_obj["last report time"] = (
        plugin_last_report_time
        if plugin_last_report_time is not None
        else last_server_communication
    )
    report["proxy agent plugin"] = plugin_obj
    if last_device_report_time is not None:
        report["last device report time"] = last_device_report_time
    if sequence is not None:
        report["device report sequence"] = sequence
        report["deviceReportSequence"] = sequence
    return report


def restamp_report(
    report: dict[str, Any],
    *,
    last_server_communication: str,
    sequence: int | None = None,
) -> dict[str, Any]:
    """Freshen a cached report for re-submission, in place.

    A polling plugin caches a device's last report and replays it while the
    device is not yet due for fresh work. Replaying verbatim would look stale
    to the agent (the effective communication time would not advance) and could
    echo an old report sequence, so this advances ``last server communication``
    to ``last_server_communication`` and, when ``sequence`` is given, re-stamps
    both :data:`SEQUENCE_KEYS`. All other (cached) data is left untouched.
    Returns the same dict for chaining.
    """
    report["last server communication"] = last_server_communication
    if sequence is not None:
        for key in SEQUENCE_KEYS:
            report[key] = sequence
    return report


def network_structure(peer_ip: str) -> dict[str, Any]:
    """Model a remote IP as the device's built-in network inspectors.

    Fills "ip interfaces of network" (and, for IPv6, "adapters of network")
    so the console's reserved IP Address / IPv6 Address properties resolve.
    Build this only for a peer actually connected to, so the interface is
    reported "up".
    """
    try:
        parsed = ipaddress.ip_address(peer_ip)
        loopback = parsed.is_loopback
        is_ipv6 = parsed.version == 6
    except ValueError:
        loopback = False
        is_ipv6 = ":" in peer_ip

    network: dict[str, Any] = {
        "ip interfaces": [{"address": peer_ip, "loopback": loopback, "up": True}],
    }
    if is_ipv6:
        network["adapters"] = [
            {
                "up": True,
                "loopback": loopback,
                "ipv6 interfaces": [{"address": peer_ip}],
            }
        ]
    return network
